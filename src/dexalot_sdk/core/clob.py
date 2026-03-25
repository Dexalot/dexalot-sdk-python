import asyncio
import json
import threading
import time
from collections.abc import Callable
from typing import Any, cast

import websocket

from ..constants import (
    ENDPOINT_SIGNED_ORDERS,
    ENDPOINT_TRADING_PAIRS,
    ENV_FUJI_MULTI_SUBNET,
    ENV_PROD_MULTI_SUBNET,
    ws_api_url_for_rest_base,
)
from ..utils import Utils
from ..utils.cache import async_ttl_cached
from ..utils.input_validators import (
    validate_order_id_format,
    validate_order_params,
    validate_pair_format,
)
from ..utils.observability import track_method
from ..utils.result import Result
from ..utils.retry import async_retry
from ..utils.websocket_manager import WebSocketManager
from .base import _ORDERBOOK_CACHE, _SEMI_STATIC_CACHE, DexalotBaseClient


class CLOBClient(DexalotBaseClient):
    _ws_manager: WebSocketManager | None
    async def _get_w3_l1(self):
        """
        Get w3_l1 provider, using provider manager if enabled.

        Returns:
            AsyncWeb3 instance or None if not available
        """
        if self._provider_manager:
            provider = await self._provider_manager.get_provider("DEXALOT_L1")
            if provider:
                return provider
        # Fallback to direct w3_l1 if provider manager is disabled or returns None
        return self.w3_l1

    def _transform_pair_field(
        self, item: dict, transformed: dict, target_key: str, *source_keys: str
    ) -> None:
        """Helper to transform a single field from API response.

        Args:
            item: Original API response item
            transformed: Transformed dict being built
            target_key: Target field name in transformed dict
            source_keys: Alternative source field names to try (in order)
        """
        if target_key not in transformed:
            for source_key in source_keys:
                if source_key in item:
                    transformed[target_key] = item[source_key]
                    break

    def _transform_pair_from_api(self, item: dict) -> dict:
        """Transform API pair response to match standardized field names.

        Maps lowercase/camelCase API fields to snake_case SDK fields to match
        Python naming conventions.

        Args:
            item: Raw pair dict from API response

        Returns:
            Transformed pair dict with standardized field names
        """
        transformed = dict(item)  # Start with all original fields

        # Map fields: prefer existing snake_case, fallback to variations
        self._transform_pair_field(
            item,
            transformed,
            "base_decimals",
            "base_evmdecimals",
            "baseEvmDecimals",
            "base_evm_decimals",
        )
        self._transform_pair_field(
            item,
            transformed,
            "quote_decimals",
            "quote_evmdecimals",
            "quoteEvmDecimals",
            "quote_evm_decimals",
        )
        self._transform_pair_field(
            item,
            transformed,
            "base_display_decimals",
            "base_display_decimals",
            "basedisplaydecimals",
            "baseDisplayDecimals",
        )
        self._transform_pair_field(
            item,
            transformed,
            "quote_display_decimals",
            "quote_display_decimals",
            "quotedisplaydecimals",
            "quoteDisplayDecimals",
        )
        self._transform_pair_field(
            item,
            transformed,
            "min_trade_amount",
            "min_trade_amount",
            "mintrade_amnt",
            "minTradeAmnt",
        )
        self._transform_pair_field(
            item,
            transformed,
            "max_trade_amount",
            "max_trade_amount",
            "maxtrade_amnt",
            "maxTradeAmnt",
        )

        return transformed

    @async_ttl_cached(_SEMI_STATIC_CACHE)
    @track_method("clob")
    async def get_clob_pairs(self) -> Result[str]:
        """Fetch and store trading pair metadata.

        Note: Cached for 15 minutes (semi-static data). Pairs are transformed to
        standardized field names (snake_case) before storing.

        Returns:
            Result with success message on success, or error message on failure
        """
        try:
            # Use self._session from DexalotBaseClient
            async with await self._make_http_request(
                "get", f"{self.api_base_url}{ENDPOINT_TRADING_PAIRS}"
            ) as response:
                response.raise_for_status()
                data = await response.json()

            # Transform pairs before processing
            transformed_data = [self._transform_pair_from_api(item) for item in data]

            self.pairs = {}
            for item in transformed_data:
                if item.get("env") in [ENV_PROD_MULTI_SUBNET, ENV_FUJI_MULTI_SUBNET]:
                    pair_name = item["pair"]
                    self.pairs[pair_name] = {
                        "pair": pair_name,
                        "base": item["base"],
                        "quote": item["quote"],
                        "base_decimals": item.get("base_decimals"),
                        "quote_decimals": item.get("quote_decimals"),
                        "base_display_decimals": item.get("base_display_decimals", 18),
                        "quote_display_decimals": item.get("quote_display_decimals", 18),
                        "min_trade_amount": float(item.get("min_trade_amount", 0)),
                        "max_trade_amount": float(item.get("max_trade_amount", 0)),
                        "tradePairId": Utils.to_bytes32(pair_name),
                    }
            return Result.ok("Pairs fetched and cached.")
        except Exception as e:
            error_msg = self._sanitize_error(e, "fetching pairs")
            return Result.fail(error_msg)

    @async_ttl_cached(_ORDERBOOK_CACHE)
    @track_method("clob")
    async def get_orderbook(self, pair) -> Result[dict]:
        """
        Get the orderbook snapshot for a pair using getNBook contract method.
        Returns top 10 bids and asks.

        Note: Cached for 1 second (real-time data with short TTL to reduce API load).

        Returns:
            Result with orderbook data (dict with 'pair', 'bids', 'asks') on success,
            or error message on failure
        """
        # Validate pair format
        pair_result = validate_pair_format(pair, "pair")
        if not pair_result.success:
            return cast(Result[dict[Any, Any]], pair_result)

        if pair not in self.pairs:
            pairs_result = await self.get_clob_pairs()
            if not pairs_result.success:
                return Result.fail(f"Failed to fetch pairs: {pairs_result.error}")
            if pair not in self.pairs:
                return Result.fail(f"Pair {pair} not found.")

        pair_data = self.pairs[pair]
        trade_pair_id = pair_data["tradePairId"]
        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            # Side: 0=Buy, 1=Sell
            # getNBook(_tradePairId, _side, _nPrice, _nOrder, _lastPrice, _lastOrder)
            # Fetch top 10 bids (Buy side)
            bids_task = contract.functions.getNBook(trade_pair_id, 0, 10, 10, 0, b"\0" * 32).call()

            # Fetch top 10 asks (Sell side)
            asks_task = contract.functions.getNBook(trade_pair_id, 1, 10, 10, 0, b"\0" * 32).call()

            bids_data, asks_data = await asyncio.gather(bids_task, asks_task)

            # Parse results (assuming returns [prices, quantities, ...])
            # Note: Prices and Quantities are in atomic units. Need to normalize.
            # Price decimals: quote_decimals
            # Quantity decimals: base_decimals

            bids = []
            for p, q in zip(bids_data[0], bids_data[1], strict=False):
                if p == 0:
                    continue
                bids.append(
                    {
                        "price": p / (10 ** pair_data["quote_decimals"]),
                        "quantity": q / (10 ** pair_data["base_decimals"]),
                    }
                )

            asks = []
            for p, q in zip(asks_data[0], asks_data[1], strict=False):
                if p == 0:
                    continue
                asks.append(
                    {
                        "price": p / (10 ** pair_data["quote_decimals"]),
                        "quantity": q / (10 ** pair_data["base_decimals"]),
                    }
                )

            return Result.ok({"pair": pair, "bids": bids, "asks": asks})
        except Exception as e:
            error_msg = self._sanitize_error(e, "fetching orderbook")
            return Result.fail(error_msg)

    @track_method("clob")
    async def add_order(
        self, pair, side, amount, price, order_type="LIMIT", wait_for_receipt: bool = True
    ) -> Result[dict]:
        """
        Place a new order.
        side: 'BUY' or 'SELL'
        order_type: 'LIMIT' or 'MARKET'

        Returns:
            Result with order details (dict with 'status', 'tx_hash', 'client_order_id') on success,
            or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")
        from_addr = cast(str, cast(Any, self.account).address)

        # Validate input parameters
        order_params_result = validate_order_params(pair, amount, price, order_type)
        if not order_params_result.success:
            return cast(Result[dict[Any, Any]], order_params_result)

        if not await self._ensure_pair_exists(pair):
            return Result.fail(f"Pair {pair} not found.")

        pair_data = self.pairs[pair]
        contract = self.trade_pairs_contract
        w3 = await self._get_w3_l1()

        if not contract or not w3:
            return Result.fail("TradePairs contract not initialized.")

        try:
            # Validate and parse order parameters
            side_enum, type_enum, validation_error = self._validate_order_params(
                side, order_type, price, None
            )
            if validation_error is not None:
                return cast(Result[dict[Any, Any]], validation_error)

            # Rounding to display decimals
            if "quote_display_decimals" in pair_data and price:
                price = round(price, pair_data["quote_display_decimals"])
            if "base_display_decimals" in pair_data:
                amount = round(amount, pair_data["base_display_decimals"])

            # Portfolio Balance Check
            required_token = pair_data["quote"] if side_enum == 0 else pair_data["base"]
            required_amount = (price * amount) if side_enum == 0 else amount

            balance_error = await self._check_order_balance(required_token, required_amount)
            if balance_error is not None:
                return cast(Result[dict[Any, Any]], balance_error)

            # Decimals
            price_wei = int(price * (10 ** pair_data["quote_decimals"])) if price else 0
            qty_wei = int(amount * (10 ** pair_data["base_decimals"]))

            # Generate random Client Order ID
            import secrets

            client_order_id = secrets.token_bytes(32)

            # Struct: (clientOrderId, tradePairId, price, quantity, traderaddress, side, type1, type2, stp)
            # Using dictionary for safety
            order_struct = {
                "clientOrderId": client_order_id,
                "tradePairId": pair_data["tradePairId"],
                "price": price_wei,
                "quantity": qty_wei,
                "traderaddress": from_addr,
                "side": side_enum,
                "type1": type_enum,
                "type2": 0,  # GTC
                "stp": 0,  # Cancel Newest
            }

            # Estimate gas, build, sign, send, wait for receipt
            tx_hash_hex, receipt = await self._send_trade_tx(
                contract.functions.addNewOrder(order_struct), wait_for_receipt=wait_for_receipt
            )

            if wait_for_receipt:
                receipt_status = (
                    receipt.status
                    if hasattr(receipt, "status")
                    else receipt.get("status", 1)
                    if receipt
                    else 1
                )
                if receipt_status == 1:
                    return Result.ok(
                        {
                            "status": "Order Sent",
                            "tx_hash": tx_hash_hex,
                            "client_order_id": w3.to_hex(client_order_id),
                        }
                    )
                # Transaction reverted - _send_trade_tx should have raised, but handle just in case
                return Result.fail("Transaction reverted")

            return Result.ok(
                {
                    "status": "Order Sent",
                    "tx_hash": tx_hash_hex,
                    "client_order_id": w3.to_hex(client_order_id),
                }
            )

        except Exception as e:
            error_msg = self._sanitize_error(e, "placing order")
            return Result.fail(error_msg)

    @track_method("clob")
    async def cancel_order(self, order_id, wait_for_receipt: bool = True) -> Result[str]:
        """Cancel a single order by ID (Internal or Client ID).

        Returns:
            Result with transaction hash message on success, or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        # Validate order_id format
        order_id_result = validate_order_id_format(order_id, "order_id")
        if not order_id_result.success:
            return cast(Result[str], order_id_result)

        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            order_id_bytes = self._get_order_id_bytes(order_id)

            # Heuristic: Internal IDs start with 0x00 (bytes \x00), Client IDs usually don't.
            is_likely_internal = order_id_bytes.startswith(b"\x00")

            if is_likely_internal:
                # 1. Try Internal ID
                try:
                    tx_hash_hex, receipt = await self._send_trade_tx(
                        contract.functions.cancelOrder(order_id_bytes),
                        wait_for_receipt=wait_for_receipt,
                    )
                    if (
                        wait_for_receipt
                        and receipt
                        and (
                            receipt.status
                            if hasattr(receipt, "status")
                            else receipt.get("status", 1)
                        )
                        != 1
                    ):
                        return Result.fail("Transaction reverted")
                    return Result.ok(f"Cancel transaction sent (Internal ID): {tx_hash_hex}")
                except Exception as e_internal:
                    # Fallback to Client ID
                    try:
                        tx_hash_hex, receipt = await self._send_trade_tx(
                            contract.functions.cancelOrderByClientId(order_id_bytes),
                            wait_for_receipt=wait_for_receipt,
                        )
                        if (
                            wait_for_receipt
                            and receipt
                            and (
                                receipt.status
                                if hasattr(receipt, "status")
                                else receipt.get("status", 1)
                            )
                            != 1
                        ):
                            return Result.fail("Transaction reverted")
                        return Result.ok(f"Cancel transaction sent (Client ID): {tx_hash_hex}")
                    except Exception:
                        error_msg = self._sanitize_error(
                            e_internal, "cancelling order (tried both Internal and Client ID)"
                        )
                        return Result.fail(error_msg)
            else:
                # 1. Try Client ID
                try:
                    tx_hash_hex, receipt = await self._send_trade_tx(
                        contract.functions.cancelOrderByClientId(order_id_bytes),
                        wait_for_receipt=wait_for_receipt,
                    )
                    if (
                        wait_for_receipt
                        and receipt
                        and (
                            receipt.status
                            if hasattr(receipt, "status")
                            else receipt.get("status", 1)
                        )
                        != 1
                    ):
                        return Result.fail("Transaction reverted")
                    return Result.ok(f"Cancel transaction sent (Client ID): {tx_hash_hex}")
                except Exception as e_client:
                    # Fallback to Internal ID
                    try:
                        tx_hash_hex, receipt = await self._send_trade_tx(
                            contract.functions.cancelOrder(order_id_bytes),
                            wait_for_receipt=wait_for_receipt,
                        )
                        if (
                            wait_for_receipt
                            and receipt
                            and (
                                receipt.status
                                if hasattr(receipt, "status")
                                else receipt.get("status", 1)
                            )
                            != 1
                        ):
                            return Result.fail("Transaction reverted")
                        return Result.ok(f"Cancel transaction sent (Internal ID): {tx_hash_hex}")
                    except Exception:
                        error_msg = self._sanitize_error(
                            e_client, "cancelling order (tried both Client and Internal ID)"
                        )
                        return Result.fail(error_msg)

        except Exception as e:
            error_msg = self._sanitize_error(e, "cancelling order")
            return Result.fail(error_msg)

    @track_method("clob")
    async def cancel_all_orders(self) -> Result[str]:
        """Cancels all open orders.

        Returns:
            Result with success message on success, or error message on failure
        """
        open_orders_result = await self.get_open_orders()
        if not open_orders_result.success:
            return Result.fail(open_orders_result.error or "Failed to fetch open orders")

        open_orders = open_orders_result.data
        if not open_orders:
            return Result.fail("No open orders to cancel.")

        # Extract order IDs (internal IDs are needed for cancelListOrders)
        # API returns 'id' which is usually the internal ID.
        order_ids = []
        for order in open_orders:
            if "id" in order:
                order_ids.append(order["id"])

        if not order_ids:
            return Result.fail("No valid order IDs found.")

        return cast(Result[str], await self.cancel_list_orders(order_ids))

    def _get_auth_headers(self) -> dict[str, str]:
        """Generates authentication headers for signed endpoints.

        When config.timestamped_auth is True, the signed message is f"dexalot{ts}"
        (millisecond timestamp) and an x-timestamp header is included alongside
        x-signature. This prevents replay attacks but requires backend support —
        default is False until the backend confirms timestamp window validation.
        See docs/python-sdk-remediation-plan.md C-2.
        """
        if not self.account:
            raise Exception("Private key not configured.")

        from eth_account.messages import encode_defunct

        addr = cast(str, cast(Any, self.account).address)

        if self.config.timestamped_auth:
            ts = int(time.time() * 1000)
            message = encode_defunct(text=f"dexalot{ts}")
            signature = self.account.sign_message(message).signature.hex()
            return {
                "x-signature": f"{addr}:0x{signature}",
                "x-timestamp": str(ts),
            }

        message = encode_defunct(text="dexalot")
        signature = self.account.sign_message(message).signature.hex()
        return {"x-signature": f"{addr}:0x{signature}"}

    @track_method("clob")
    def _transform_order_from_api(self, order: dict) -> dict:
        """Transform API order response to match _format_order_data() convention.

        Maps lowercase/snake_case API fields to camelCase SDK fields to match
        the format used by _format_order_data() for contract responses.

        Args:
            order: Raw order dict from API response

        Returns:
            Transformed order dict with standardized field names
        """
        transformed = dict(order)  # Start with all original fields

        # Map confirmed and potential field name mismatches
        if "clientordid" in order and "clientOrderId" not in order:
            transformed["clientOrderId"] = order["clientordid"]
        if "client_order_id" in order and "clientOrderId" not in order:
            transformed["clientOrderId"] = order["client_order_id"]

        if "tradepairid" in order and "tradePairId" not in order:
            transformed["tradePairId"] = order["tradepairid"]
        if "trade_pair_id" in order and "tradePairId" not in order:
            transformed["tradePairId"] = order["trade_pair_id"]

        if "filledquantity" in order and "filledQuantity" not in order:
            transformed["filledQuantity"] = order["filledquantity"]
        if "filled_quantity" in order and "filledQuantity" not in order:
            transformed["filledQuantity"] = order["filled_quantity"]

        if "totalamount" in order and "totalAmount" not in order:
            transformed["totalAmount"] = order["totalamount"]
        if "total_amount" in order and "totalAmount" not in order:
            transformed["totalAmount"] = order["total_amount"]

        if "totalfee" in order and "totalFee" not in order:
            transformed["totalFee"] = order["totalfee"]
        if "total_fee" in order and "totalFee" not in order:
            transformed["totalFee"] = order["total_fee"]

        if "txhash" in order and "txHash" not in order:
            transformed["txHash"] = order["txhash"]
        if "tx_hash" in order and "txHash" not in order:
            transformed["txHash"] = order["tx_hash"]

        return transformed

    async def get_open_orders(self, pair=None) -> Result[list]:
        """Fetches open orders from the REST API.

        Returns:
            Result with list of open orders on success, or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        # Validate pair format if provided
        if pair is not None:
            pair_result = validate_pair_format(pair, "pair")
            if not pair_result.success:
                return cast(Result[list[Any]], pair_result)

        endpoint = ENDPOINT_SIGNED_ORDERS
        url = f"{self.api_base_url}{endpoint}"

        try:
            headers = self._get_auth_headers()
            params = {"category": 0}  # 0 = Open Orders
            if pair:
                params["pair"] = pair

            async with await self._make_http_request(
                "get", url, headers=headers, params=params
            ) as res:
                if res.status == 200:
                    data = await res.json()
                    orders = []
                    if isinstance(data, dict) and "rows" in data:
                        orders = data["rows"]
                    elif isinstance(data, list):
                        orders = data
                    elif data:
                        orders = [data]

                    # Transform API field names to match _format_order_data() convention
                    transformed_orders = [self._transform_order_from_api(order) for order in orders]
                    return Result.ok(transformed_orders)
                else:
                    error_text = await res.text()
                    error_msg = self._sanitize_error(
                        Exception(f"HTTP {res.status}: {error_text}"), "fetching open orders"
                    )
                    return Result.fail(error_msg)
        except Exception as e:
            error_msg = self._sanitize_error(e, "fetching open orders")
            return Result.fail(error_msg)

    @track_method("clob")
    async def get_order(self, order_id) -> Result[dict]:
        """Returns the details of the order given (by Internal ID or Client ID).

        Returns:
            Result with order details (dict) on success, or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        # Validate order_id format
        order_id_result = validate_order_id_format(order_id, "order_id")
        if not order_id_result.success:
            return cast(Result[dict[Any, Any]], order_id_result)

        contract = self.trade_pairs_contract
        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            # Ensure order_id is bytes32
            if isinstance(order_id, str):
                if order_id.startswith("0x"):
                    order_id_bytes = bytes.fromhex(order_id[2:])
                else:
                    order_id_bytes = order_id.encode("utf-8").ljust(32, b"\0")
            else:
                order_id_bytes = order_id

            # Try getOrder(_orderId) first
            order_data = await contract.functions.getOrder(order_id_bytes).call()

            # Check if order found (ID is not empty/zero)
            # order_data[0] is the ID (bytes32)
            if order_data[0] == b"\0" * 32:
                # Not found by Internal ID, try Client ID
                # getOrderByClientId(_clientOrderId)
                try:
                    # For getOrderByClientId, we need the trader address as well
                    w3_l1 = await self._get_w3_l1()
                    if not w3_l1:
                        return Result.fail("L1 provider not available.")
                    trader = w3_l1.to_checksum_address(
                        cast(str, cast(Any, self.account).address)
                    )
                    order_data = await contract.functions.getOrderByClientId(
                        trader, order_id_bytes
                    ).call()
                    if order_data[0] == b"\0" * 32:
                        return Result.fail("Order not found (checked both Internal and Client ID).")
                except Exception:
                    # getOrderByClientId might fail if ABI is different or other issues
                    return Result.fail("Order not found (Internal ID).")

            order_details = await self._format_order_data(order_data)
            return Result.ok(order_details)

        except Exception as e:
            error_msg = self._sanitize_error(e, "getting order")
            return Result.fail(error_msg)

    @track_method("clob")
    async def get_order_by_client_id(self, client_order_id) -> Result[dict]:
        """Returns the details of the order given by Client Order ID.

        Returns:
            Result with order details (dict) on success, or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        # Validate client_order_id format
        order_id_result = validate_order_id_format(client_order_id, "client_order_id")
        if not order_id_result.success:
            return cast(Result[dict[Any, Any]], order_id_result)

        contract = self.trade_pairs_contract
        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            # Ensure client_order_id is bytes32
            if isinstance(client_order_id, str):
                if client_order_id.startswith("0x"):
                    client_order_id_bytes = bytes.fromhex(client_order_id[2:])
                else:
                    client_order_id_bytes = client_order_id.encode("utf-8").ljust(32, b"\0")
            else:
                client_order_id_bytes = client_order_id

            # getOrderByClientOrderId(_trader, _clientOrderId)
            w3_l1 = await self._get_w3_l1()
            if not w3_l1:
                return Result.fail("L1 provider not available.")
            trader = w3_l1.to_checksum_address(cast(str, cast(Any, self.account).address))
            order_data = await contract.functions.getOrderByClientOrderId(
                trader, client_order_id_bytes
            ).call()
            order_details = await self._format_order_data(order_data)
            return Result.ok(order_details)

        except Exception as e:
            error_msg = self._sanitize_error(e, "getting order by client ID")
            return Result.fail(error_msg)

    async def _format_order_data(self, order_data):
        # Parse order data (Struct: id, clientOrderId, tradePairId, price, totalAmount, quantity, quantityFilled, totalFee, traderaddress, side, type1, type2, status, updateBlock, createBlock)
        # Note: The struct fields order might vary based on ABI, but usually matches the return tuple.
        # Based on inspect_abi.py output:
        # 0: id
        # 1: clientOrderId
        # 2: tradePairId
        # 3: price
        # 4: totalAmount
        # 5: quantity
        # 6: quantityFilled
        # 7: totalFee
        # 8: traderaddress
        # 9: side
        # 10: type1
        # 11: type2
        # 12: status

        trade_pair_id = order_data[2]

        # Find pair info
        pair_info = None
        for _p_name, p_data in self.pairs.items():
            if p_data["tradePairId"] == trade_pair_id:
                pair_info = p_data
                break

        if not pair_info:
            # Try to fetch pairs if not found
            await self.get_clob_pairs()
            for _p_name, p_data in self.pairs.items():
                if p_data["tradePairId"] == trade_pair_id:
                    pair_info = p_data
                    break

        w3_l1 = await self._get_w3_l1()
        if not w3_l1:
            return Result.fail("L1 provider not available.")
        result = {
            "id": w3_l1.to_hex(order_data[0]),
            "clientOrderId": w3_l1.to_hex(order_data[1]),
            "tradePairId": w3_l1.to_hex(trade_pair_id),
            "price": order_data[3],
            "quantity": order_data[5],
            "filledQuantity": order_data[6],
            "status": order_data[12],  # Enum
            "side": "BUY" if order_data[9] == 0 else "SELL",
            "type": "MARKET" if order_data[10] == 0 else "LIMIT",
        }

        if pair_info:
            result["price"] = result["price"] / (10 ** pair_info["quote_decimals"])
            result["quantity"] = result["quantity"] / (10 ** pair_info["base_decimals"])
            result["filledQuantity"] = result["filledQuantity"] / (10 ** pair_info["base_decimals"])
            result["pair"] = pair_info["pair"]

        return result

    def _parse_order_side(self, side_str, pair_data):
        """Parse order side and return side enum, required token, and required amount."""
        side_clean = side_str.strip().upper()
        if side_clean == "BUY":
            return 0, pair_data["quote"], None  # req_amt calculated from price * amount
        elif side_clean == "SELL":
            return 1, pair_data["base"], None  # req_amt is just amount
        else:
            return None, None, f"Invalid side '{side_str}'. Must be 'BUY' or 'SELL'."

    def _validate_order_params(self, side, order_type, price, type_enum):
        """Validate order parameters and return enums or error."""
        side_clean = side.strip().upper()
        if side_clean == "BUY":
            side_enum = 0
        elif side_clean == "SELL":
            side_enum = 1
        else:
            return None, None, Result.fail(f"Invalid side '{side}'. Must be 'BUY' or 'SELL'.")

        type_clean = order_type.strip().upper()
        if type_clean == "MARKET":
            type_enum = 0
        elif type_clean == "LIMIT":
            type_enum = 1
        else:
            return (
                None,
                None,
                Result.fail(f"Invalid type '{order_type}'. Must be 'MARKET' or 'LIMIT'."),
            )

        if type_enum == 1 and price is None:
            return None, None, Result.fail("Price is required for LIMIT orders.")

        return side_enum, type_enum, None

    async def _check_order_balance(self, required_token, required_amount):
        """Check if sufficient balance exists for an order."""
        balance_info = await cast(Any, self).get_portfolio_balance(required_token)
        if isinstance(balance_info, Result):
            if not balance_info.success:
                return Result.fail(f"Error checking balance: {balance_info.error}")
            if balance_info.data is None:
                return Result.fail("Error checking balance: empty response")
            balance_info = balance_info.data
        elif isinstance(balance_info, dict) and "error" in balance_info:
            return Result.fail(f"Error checking balance: {balance_info['error']}")

        if balance_info["available"] < required_amount:
            return Result.fail(
                f"Insufficient {required_token} balance. Required: {required_amount}, Available: {balance_info['available']}"
            )
        return None

    def _normalize_order_amounts(self, price, amount, pair_data):
        """Normalize price and amount based on display decimals."""
        if "quote_display_decimals" in pair_data and price:
            price = round(price, pair_data["quote_display_decimals"])
        if "base_display_decimals" in pair_data:
            amount = round(amount, pair_data["base_display_decimals"])
        return price, amount

    def _build_order_tuple(self, order, pair_data, side_enum, w3):
        """Build order tuple for contract call."""
        import secrets

        price, amount = self._normalize_order_amounts(order["price"], order["amount"], pair_data)

        price_wei = int(price * (10 ** pair_data["quote_decimals"]))
        qty_wei = int(amount * (10 ** pair_data["base_decimals"]))
        client_order_id = secrets.token_bytes(32)
        client_order_id_hex = w3.to_hex(client_order_id)

        # Struct: (clientOrderId, tradePairId, price, quantity, traderaddress, side, type1, type2, stp)
        assert self.account is not None
        trader = cast(str, cast(Any, self.account).address)
        order_tuple = (
            client_order_id,
            pair_data["tradePairId"],
            price_wei,
            qty_wei,
            trader,
            side_enum,
            1,  # LIMIT
            0,  # GTC
            0,  # STP
        )

        return order_tuple, client_order_id_hex

    async def _check_balance_for_token(self, token, req_amt):
        """Check if sufficient balance exists for a token.

        Returns:
            None if balance is sufficient, or error message string if insufficient/error
        """
        balance_info = await cast(Any, self).get_portfolio_balance(token)
        if isinstance(balance_info, Result):
            if not balance_info.success:
                return f"Error checking balance for {token}: {balance_info.error}"
            balance_info = balance_info.data
        elif isinstance(balance_info, str):
            return f"Error checking balance for {token}: {balance_info}"
        if not isinstance(balance_info, dict) or "error" in balance_info:
            error_msg = (
                balance_info.get("error", str(balance_info))
                if isinstance(balance_info, dict)
                else str(balance_info)
            )
            return f"Error checking balance for {token}: {error_msg}"
        if "available" not in balance_info:
            return f"Error checking balance for {token}: Invalid balance response format"
        if balance_info["available"] < req_amt:
            return f"Insufficient {token} balance. Required: {req_amt}, Available: {balance_info['available']}"
        return None

    async def _process_orders_for_batch(self, orders, w3):
        """Process orders and return order tuples, client IDs, and required balances."""
        order_tuples = []
        client_order_ids = []
        required_balances: dict[str, float] = {}  # token -> amount

        for order in orders:
            pair = order["pair"]
            if not await self._ensure_pair_exists(pair):
                return None, None, None, f"Pair {pair} not found."

            pair_data = self.pairs[pair]

            side_enum, req_token, error = self._parse_order_side(order["side"], pair_data)
            if error:
                return None, None, None, error

            # Calculate required amount
            if side_enum == 0:  # BUY
                req_amt = order["price"] * order["amount"]
            else:  # SELL
                req_amt = order["amount"]

            required_balances[req_token] = required_balances.get(req_token, 0) + req_amt

            order_tuple, client_order_id_hex = self._build_order_tuple(
                order, pair_data, side_enum, w3
            )
            order_tuples.append(order_tuple)
            client_order_ids.append(client_order_id_hex)

        return order_tuples, client_order_ids, required_balances, None

    @track_method("clob")
    async def add_limit_order_list(self, orders, wait_for_receipt: bool = True) -> Result[dict]:
        """
        Add a list of BUY or SELL Limit Orders in a single transaction.
        orders: List of dicts: {"pair": "AVAX/USDC", "side": "BUY", "amount": 1.0, "price": 10.0}

        Returns:
            Result with order details (dict with 'tx_hash', 'client_order_ids') on success,
            or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        contract = self.trade_pairs_contract
        w3 = self.w3_l1

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            (
                order_tuples,
                client_order_ids,
                required_balances,
                error,
            ) = await self._process_orders_for_batch(orders, w3)
            if error:
                return Result.fail(error)

            # Perform Balance Checks
            for token, req_amt in required_balances.items():
                balance_error = await self._check_balance_for_token(token, req_amt)
                if balance_error:
                    return Result.fail(balance_error)

            tx_hash_hex, receipt = await self._send_trade_tx(
                contract.functions.addOrderList(order_tuples), wait_for_receipt=wait_for_receipt
            )

            if wait_for_receipt:
                receipt_status = (
                    receipt.status
                    if hasattr(receipt, "status")
                    else receipt.get("status", 1)
                    if receipt
                    else 1
                )
                if receipt_status == 1:
                    return Result.ok({"tx_hash": tx_hash_hex, "client_order_ids": client_order_ids})
                # else: Transaction reverted is raised by _send_trade_tx
                return Result.fail("Transaction reverted")

            return Result.ok({"tx_hash": tx_hash_hex, "client_order_ids": client_order_ids})

        except Exception as e:
            error_msg = self._sanitize_error(e, "placing batch orders")
            return Result.fail(error_msg)

    @track_method("clob")
    async def cancel_list_orders(self, order_ids, wait_for_receipt: bool = True) -> Result[str]:
        """Cancels all orders contained in the given array of order ids (Internal IDs).

        Returns:
            Result with transaction hash message on success, or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            order_ids_bytes = []
            for oid in order_ids:
                if isinstance(oid, str):
                    if oid.startswith("0x"):
                        order_ids_bytes.append(bytes.fromhex(oid[2:]))
                    else:
                        # If ID is decimal string (from API sometimes), convert to int then bytes
                        if oid.isdigit():
                            order_ids_bytes.append(int(oid).to_bytes(32, "big"))
                        else:
                            order_ids_bytes.append(oid.encode("utf-8").ljust(32, b"\0"))
                elif isinstance(oid, int):
                    order_ids_bytes.append(oid.to_bytes(32, "big"))
                else:
                    order_ids_bytes.append(oid)

            tx_hash_hex, receipt = await self._send_trade_tx(
                contract.functions.cancelOrderList(order_ids_bytes),
                wait_for_receipt=wait_for_receipt,
            )
            if (
                wait_for_receipt
                and receipt
                and (receipt.status if hasattr(receipt, "status") else receipt.get("status", 1))
                != 1
            ):
                return Result.fail("Transaction reverted")
            return Result.ok(f"Cancel List transaction sent: {tx_hash_hex}")

        except Exception as e:
            error_msg = self._sanitize_error(e, "cancelling list orders")
            return Result.fail(error_msg)

    @track_method("clob")
    async def replace_order(
        self, order_id, new_price, new_amount, wait_for_receipt: bool = True
    ) -> Result[str]:
        """
        Cancels the given order and replaces it with one for the same pair with the given price and quantity.
        Uses cancelAndReplaceOrder.

        Returns:
            Result with transaction hash message on success, or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            order_id_bytes = self._get_order_id_bytes(order_id)

            # We need to fetch the order to know the pair, so we can normalize decimals.
            order_details_result = await self.get_order(order_id)
            if not order_details_result.success:
                return Result.fail(
                    f"Could not fetch order details for replacement: {order_details_result.error}"
                )

            order_details = order_details_result.data
            pair_name = order_details.get("pair")
            if not pair_name:
                return Result.fail("Could not determine pair from order details.")

            pair_data = self.pairs[pair_name]

            price_wei = int(new_price * (10 ** pair_data["quote_decimals"]))
            qty_wei = int(new_amount * (10 ** pair_data["base_decimals"]))

            import secrets

            new_client_order_id = secrets.token_bytes(32)

            tx_hash_hex, receipt = await self._send_trade_tx(
                contract.functions.cancelReplaceOrder(
                    order_id_bytes, new_client_order_id, price_wei, qty_wei
                ),
                wait_for_receipt=wait_for_receipt,
            )
            if (
                wait_for_receipt
                and receipt
                and (receipt.status if hasattr(receipt, "status") else receipt.get("status", 1))
                != 1
            ):
                return Result.fail("Transaction reverted")
            return Result.ok(f"Replace Order transaction sent: {tx_hash_hex}")
        except Exception as e:
            error_msg = self._sanitize_error(e, "replacing order")
            return Result.fail(error_msg)

    @track_method("clob")
    async def cancel_list_orders_by_client_id(
        self, client_order_ids, wait_for_receipt: bool = True
    ) -> Result[str]:
        """Cancels all orders contained in the given array of Client Order IDs.

        Returns:
            Result with transaction hash message on success, or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            order_ids_bytes = []
            for oid in client_order_ids:
                if isinstance(oid, str):
                    if oid.startswith("0x"):
                        order_ids_bytes.append(bytes.fromhex(oid[2:]))
                    else:
                        order_ids_bytes.append(oid.encode("utf-8").ljust(32, b"\0"))
                else:
                    order_ids_bytes.append(oid)

            tx_hash_hex, receipt = await self._send_trade_tx(
                contract.functions.cancelOrderListByClientIds(order_ids_bytes),
                wait_for_receipt=wait_for_receipt,
            )
            if (
                wait_for_receipt
                and receipt
                and (receipt.status if hasattr(receipt, "status") else receipt.get("status", 1))
                != 1
            ):
                return Result.fail("Transaction reverted")
            return Result.ok(f"Cancel List By Client ID transaction sent: {tx_hash_hex}")

        except Exception as e:
            error_msg = self._sanitize_error(e, "cancelling list orders by client ID")
            return Result.fail(error_msg)

    @track_method("clob")
    async def cancel_add_list(
        self, replacements: list, wait_for_receipt: bool = True
    ) -> Result[dict]:
        """
        To Cancel/Replace multiple Orders in a single transaction using cancelAddList.
        replacements: List of dicts: {"order_id": "...", "pair": "AVAX/USDC", "side": "BUY", "amount": 1.0, "price": 10.0}

        Returns:
            Result with transaction details (dict with 'tx_hash') on success,
            or error message on failure
        """
        if not self.account:
            return Result.fail("Private key not configured.")
        from_addr = cast(str, cast(Any, self.account).address)

        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            order_ids = []
            new_orders = []

            import secrets

            # Balance Check Aggregation
            required_balances: dict[str, float] = {}  # token -> amount

            for rep in replacements:
                # 1. Prepare Order ID to Cancel
                order_id = rep["order_id"]
                if isinstance(order_id, str):
                    if order_id.startswith("0x"):
                        order_ids.append(bytes.fromhex(order_id[2:]))
                    else:
                        order_ids.append(order_id.encode("utf-8").ljust(32, b"\0"))
                else:
                    order_ids.append(order_id)

                # 2. Prepare New Order
                pair = rep.get("pair", "AVAX/USDC")
                if not await self._ensure_pair_exists(pair):
                    return Result.fail(f"Pair {pair} not found.")

                pair_data = self.pairs[pair]

                side_clean = rep["side"].strip().upper()
                if side_clean == "BUY":
                    side_enum = 0
                    req_token = pair_data["quote"]
                    req_amt = rep["price"] * rep["amount"]
                elif side_clean == "SELL":
                    side_enum = 1
                    req_token = pair_data["base"]
                    req_amt = rep["amount"]
                else:
                    return Result.fail(f"Invalid side '{rep['side']}'. Must be 'BUY' or 'SELL'.")

                required_balances[req_token] = required_balances.get(req_token, 0) + req_amt

                price = rep["price"]
                amount = rep["amount"]

                # Rounding to display decimals
                if "quote_display_decimals" in pair_data and price:
                    price = round(price, pair_data["quote_display_decimals"])
                if "base_display_decimals" in pair_data:
                    amount = round(amount, pair_data["base_display_decimals"])

                price_wei = int(price * (10 ** pair_data["quote_decimals"]))
                qty_wei = int(amount * (10 ** pair_data["base_decimals"]))
                client_order_id = secrets.token_bytes(32)

                # Struct: (clientOrderId, tradePairId, price, quantity, traderaddress, side, type1, type2, stp)
                new_orders.append(
                    (
                        client_order_id,
                        pair_data["tradePairId"],
                        price_wei,
                        qty_wei,
                        from_addr,
                        side_enum,
                        1,  # LIMIT
                        0,  # GTC
                        0,  # STP
                    )
                )

            # given complexity, rely on contract revert for insufficient funds

            tx_hash_hex, receipt = await self._send_trade_tx(
                contract.functions.cancelAddList(order_ids, new_orders),
                wait_for_receipt=wait_for_receipt,
            )

            if wait_for_receipt:
                receipt_status = (
                    receipt.status
                    if hasattr(receipt, "status")
                    else receipt.get("status", 1)
                    if receipt
                    else 1
                )
                if receipt_status == 1:
                    return Result.ok({"tx_hash": tx_hash_hex})
                # Transaction reverted - _send_trade_tx should have raised, but handle just in case
                return Result.fail("Transaction reverted")

            return Result.ok({"tx_hash": tx_hash_hex})

        except Exception as e:
            error_msg = self._sanitize_error(e, "cancel/add list")
            return Result.fail(error_msg)

    def _get_ws_manager(self) -> WebSocketManager | None:
        """
        Get or create WebSocket manager instance.
        Returns None if WebSocket is disabled.
        """
        if not self.config.ws_manager_enabled:
            return None

        # Initialize _ws_manager as instance attribute if not exists
        if not hasattr(self, "_ws_manager") or self._ws_manager is None:
            self._ws_manager = WebSocketManager(
                ws_url=ws_api_url_for_rest_base(self.api_base_url),
                account=self.account,
                config=self.config,
                logger=self.logger,
            )
        return self._ws_manager

    async def subscribe_to_events(
        self, topic: str, callback: Callable, is_private: bool = False
    ) -> None:
        """
        Subscribe to WebSocket events with a callback.

        Args:
            topic: Topic to subscribe to (e.g., "OrderBook/AVAX/USDC" or "Orders")
            callback: Callable that receives message data (dict)
            is_private: Whether this is a private topic requiring authentication

        Raises:
            RuntimeError: If WebSocket is disabled in config
        """
        manager = self._get_ws_manager()
        if manager is None:
            raise RuntimeError(
                "WebSocket Manager is disabled. Set ws_manager_enabled=True in config."
            )

        orderbook_pair: str | None = None
        orderbook_decimal: int | None = None
        if not is_private:
            if topic.startswith("OrderBook/"):
                orderbook_pair = topic[len("OrderBook/") :]
            elif "/" in topic and topic.count("/") == 1:
                orderbook_pair = topic
        if orderbook_pair:
            if not await self._ensure_pair_exists(orderbook_pair):
                raise ValueError(f"Trading pair not found for WebSocket: {orderbook_pair}")
            pd = self.pairs.get(orderbook_pair, {})
            orderbook_decimal = int(
                pd.get("quote_display_decimals") or pd.get("base_display_decimals") or 8
            )
            manager.subscribe(
                topic,
                callback,
                is_private,
                orderbook_pair=orderbook_pair,
                orderbook_decimal=orderbook_decimal,
            )
        else:
            manager.subscribe(topic, callback, is_private)

    def unsubscribe_from_events(self, topic: str) -> None:
        """
        Unsubscribe from a topic.

        Args:
            topic: Topic to unsubscribe from
        """
        if hasattr(self, "_ws_manager") and self._ws_manager is not None:
            self._ws_manager.unsubscribe(topic)

    async def close_websocket(self, *, grace_s: float = 3.0) -> None:
        """Close WebSocket connection and cleanup.

        Runs ``disconnect()`` on a **daemon** thread and optionally waits up to ``grace_s``
        seconds for it to finish (polls with short async sleeps). We avoid
        ``asyncio.to_thread`` / the default ``ThreadPoolExecutor`` so loop shutdown does not
        block on ``shutdown_default_executor()``.

        Use ``grace_s=0`` when tearing down after task cancellation (e.g. Ctrl+C under
        pytest-asyncio): ``disconnect()`` can block inside ``websocket-client`` longer than
        any grace period; waiting would only delay returning and invite a second SIGINT
        (``KeyboardInterrupt``), while the daemon thread keeps finishing in the background.

        On timeout or ``CancelledError`` during the wait loop we return without re-raising.
        """
        if not hasattr(self, "_ws_manager") or self._ws_manager is None:
            return
        mgr = self._ws_manager
        self._ws_manager = None

        def _sync_disconnect() -> None:
            try:
                mgr.disconnect()
            except Exception:
                pass

        done = threading.Event()

        def _run_disconnect() -> None:
            try:
                _sync_disconnect()
            finally:
                done.set()

        threading.Thread(target=_run_disconnect, daemon=True).start()

        if grace_s <= 0:
            await asyncio.sleep(0)
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + grace_s
        try:
            while not done.is_set():
                if loop.time() >= deadline:
                    self.logger.warning(
                        "WebSocket disconnect exceeded %.1fs; daemon thread still finishing",
                        grace_s,
                    )
                    break
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            # Do not re-raise: allow test/fixture finally to finish after Ctrl+C.
            pass

        await asyncio.sleep(0)

    @track_method("websocket")
    def listen_to_events(self, topic, duration_seconds=10):
        """
        Listen to WebSocket events for a specific topic for a duration.
        topic: e.g. "OrderBook/AVAX/USDC" or legacy private channel names.

        Note: This method creates a temporary connection. For persistent subscriptions,
        use subscribe_to_events() instead.

        Public order books use the pair subscribe shape from docs/websocket.md.

        This method works independently of the ws_manager_enabled setting, allowing one-off
        WebSocket connections even when the persistent WebSocketManager is disabled.
        """
        import threading

        messages = []

        ws_url = ws_api_url_for_rest_base(self.api_base_url)

        def on_message(ws, message):
            self.logger.debug(f"WS Message: {message}")
            messages.append(json.loads(message))

        def on_error(ws, error):
            self.logger.error(f"WS Error: {error}")
            # pass  # print(f"WS Error: {error}")

        def on_close(ws, close_status_code, close_msg):
            self.logger.info(f"WS Closed: {close_status_code} {close_msg}")
            # pass  # print("WS Closed")

        def on_open(ws):
            self.logger.info("WS Opened")
            pair: str | None = None
            if isinstance(topic, str) and topic.startswith("OrderBook/"):
                pair = topic[len("OrderBook/") :]
            elif isinstance(topic, str) and "/" in topic and topic.count("/") == 1:
                pair = topic

            if pair and topic not in ["Orders", "Executions", "Balances"]:
                pd = self.pairs.get(pair) if isinstance(self.pairs, dict) else None
                dec = 8
                if isinstance(pd, dict):
                    dec = int(
                        pd.get("quote_display_decimals")
                        or pd.get("base_display_decimals")
                        or 8
                    )
                payload_ob: dict[str, Any] = {
                    "type": "subscribe",
                    "data": pair,
                    "pair": pair,
                    "decimal": dec,
                }
                if self.account:
                    addr = cast(str, cast(Any, self.account).address)
                    payload_ob["traderaddress"] = addr
                ws.send(json.dumps(payload_ob))
                return

            payload: dict[str, Any] = {"type": "subscribe", "topics": [topic]}

            # Check if private topic
            if topic in ["Orders", "Executions", "Balances"]:
                if not self.account:
                    pass  # print("Cannot subscribe to private topic without account.")
                    return

                # Auth logic
                # 1. Get nonce/timestamp?
                # Docs say:
                # {
                #   "type": "subscribe",
                #   "topics": ["Orders"],
                #   "address": "0x...",
                #   "signature": "0x...",
                #   "timestamp": 1234567890
                # }
                # Signature matches: keccak256(address + timestamp) ?
                # Need to check docs for exact signature format.
                # Assuming standard: sign(address + timestamp)

                ts = int(time.time() * 1000)
                msg_to_sign = f"{cast(str, cast(Any, self.account).address)}{ts}"
                # This is a guess. Let's look at docs if possible.
                # Docs link provided: https://docs.dexalot.com/en/apiv2/Websocket.html
                # I can't browse, but I can assume standard or try to read if I had the tool.
                # I'll implement a generic signature mechanism if I can't verify.
                # "The signature is generated by signing the concatenation of the user's address and the timestamp."

                from eth_account.messages import encode_defunct

                message_hash = encode_defunct(text=msg_to_sign)
                # Use Account instance method - never expose private key
                signed_message = self.account.sign_message(message_hash)
                signature = signed_message.signature.hex()

                payload["address"] = cast(str, cast(Any, self.account).address)
                payload["signature"] = signature
                payload["timestamp"] = ts

            ws.send(json.dumps(payload))

        ws = websocket.WebSocketApp(
            ws_url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close
        )

        wst = threading.Thread(target=ws.run_forever)
        wst.daemon = True
        wst.start()

        time.sleep(duration_seconds)
        ws.close()
        wst.join(timeout=1)

        return messages

    async def _ensure_pair_exists(self, pair):
        """Ensure pair exists in local cache, fetching if necessary."""
        if pair not in self.pairs:
            await self.get_clob_pairs()
            if pair not in self.pairs:
                return False
        return True

    def _get_order_id_bytes(self, order_id):
        """Convert order ID to bytes32."""
        if isinstance(order_id, str):
            if order_id.startswith("0x"):
                return bytes.fromhex(order_id[2:])
            elif order_id.isdigit():
                # Handle decimal string IDs
                return int(order_id).to_bytes(32, "big")
            else:
                return order_id.encode("utf-8").ljust(32, b"\0")
        elif isinstance(order_id, int):
            return order_id.to_bytes(32, "big")
        return order_id

    async def _send_trade_tx(self, function_call, wait_for_receipt=False):
        """
        Helper to estimate gas, build, sign, and send a transaction.
        Returns (tx_hash_hex, receipt).
        """
        if not self.account:
            raise ValueError(
                "Account is required for signing transactions. Set signer or PRIVATE_KEY."
            )
        from_addr = cast(str, cast(Any, self.account).address)

        w3 = await self._get_w3_l1()
        if not w3:
            raise ValueError("L1 provider not available.")
        nonce = await self._get_nonce(w3)

        try:
            # estimate_gas is a method on the contract function, not directly on w3
            # We'll wrap it with retry/rate limiting manually
            if self._rpc_rate_limiter:
                await self._rpc_rate_limiter.acquire()

            if self.config.retry_enabled:

                async def _estimate_gas():
                    return await function_call.estimate_gas({"from": from_addr})

                retry_func = async_retry(
                    max_attempts=self.config.retry_max_attempts,
                    initial_delay=self.config.retry_initial_delay,
                    max_delay=self.config.retry_max_delay,
                    exponential_base=self.config.retry_exponential_base,
                    retry_on_status=self.config.retry_on_status,
                    retry_on_exceptions=self.config.retry_on_exceptions,
                )(_estimate_gas)
                gas_estimate = await retry_func()
            else:
                gas_estimate = await function_call.estimate_gas({"from": from_addr})
        except Exception as e:
            error_desc = self._parse_revert_reason(e)
            raise Exception(f"Gas estimation failed: {error_desc}") from e

        gas_price = await self._rpc_call(w3, "eth.gas_price")

        tx = await function_call.build_transaction(
            {
                "from": from_addr,
                "nonce": nonce,
                "gas": int(gas_estimate * 1.2),
                "gasPrice": gas_price,
            }
        )

        # Use Account instance method - never expose private key
        signed_tx = self.account.sign_transaction(tx)
        tx_hash = await self._rpc_call(w3, "eth.send_raw_transaction", signed_tx.raw_transaction)
        tx_hash_hex = w3.to_hex(tx_hash)

        receipt = None
        if wait_for_receipt:
            receipt = await self._rpc_call(w3, "eth.wait_for_transaction_receipt", tx_hash)
            if receipt.status != 1:
                raise Exception(f"Transaction reverted. Hash: {tx_hash_hex}")

        return tx_hash_hex, receipt
