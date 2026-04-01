import asyncio
import time
from collections.abc import Callable
from typing import Any, cast

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
    async def get_clob_pairs(self) -> Result[list[dict[str, Any]]]:
        """Fetch and store trading pair metadata.

        Note: Cached for 15 minutes (semi-static data). Pairs are transformed to
        standardized field names (snake_case) before storing.

        Returns:
            Result containing the normalized pair list on success. Also populates
            ``client.pairs`` keyed by pair symbol for follow-on SDK operations.
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
            pair_list = self._store_clob_pairs(transformed_data)
            return Result.ok(pair_list)
        except Exception as e:
            error_msg = self._sanitize_error(e, "fetching pairs")
            return Result.fail(error_msg)

    def _store_clob_pairs(self, transformed_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize pair metadata into both list and keyed lookup forms."""
        pair_map: dict[str, dict[str, Any]] = {}
        for item in transformed_data:
            if item.get("env") not in [ENV_PROD_MULTI_SUBNET, ENV_FUJI_MULTI_SUBNET]:
                continue

            pair_name = item["pair"]
            pair_map[pair_name] = {
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

        self.pairs = pair_map
        return list(pair_map.values())

    def _rehydrate_cached_get_clob_pairs(self, cached: Result[list[dict[str, Any]]]) -> None:
        """Rebuild ``client.pairs`` when ``get_clob_pairs`` is served from cache."""
        if not cached.success or cached.data is None:
            return
        self.pairs = {pair["pair"]: dict(pair) for pair in cached.data}

    @async_ttl_cached(_ORDERBOOK_CACHE)
    @track_method("clob")
    async def get_orderbook(self, pair: str) -> Result[dict]:
        """Fetch the top-10 bids and asks for a trading pair.

        Reads directly from the ``TradePairs`` contract via ``getNBook``.

        Note:
            Cached for 1 second (orderbook cache tier).

        Args:
            pair: Trading pair symbol, e.g. ``"AVAX/USDC"``.

        Returns:
            Result containing ``{"pair": str, "bids": list, "asks": list}`` where
            each bid/ask entry is ``{"price": float, "quantity": float}``.
            Returns an error message on failure.
        """
        # Validate pair format
        pair_result = validate_pair_format(pair, "pair")
        if not pair_result.success:
            return cast(Result[dict[Any, Any]], pair_result)

        pair = self._normalize_user_pair(pair)

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
                        "price": Utils.unit_conversion(
                            p, pair_data["quote_decimals"], to_base=False
                        ),
                        "quantity": Utils.unit_conversion(
                            q, pair_data["base_decimals"], to_base=False
                        ),
                    }
                )

            asks = []
            for p, q in zip(asks_data[0], asks_data[1], strict=False):
                if p == 0:
                    continue
                asks.append(
                    {
                        "price": Utils.unit_conversion(
                            p, pair_data["quote_decimals"], to_base=False
                        ),
                        "quantity": Utils.unit_conversion(
                            q, pair_data["base_decimals"], to_base=False
                        ),
                    }
                )

            return Result.ok({"pair": pair, "bids": bids, "asks": asks})
        except Exception as e:
            error_msg = self._sanitize_error(e, "fetching orderbook")
            return Result.fail(error_msg)

    @track_method("clob")
    async def add_order(
        self,
        pair: str,
        side: str,
        amount: float,
        price,
        order_type: str = "LIMIT",
        wait_for_receipt: bool = True,
        client_order_id: str | None = None,
    ) -> Result[dict]:
        """Place a single limit or market order on the CLOB.

        Checks portfolio balance before submitting.  Rounds ``price`` and
        ``amount`` to the pair's display decimals before encoding.

        Args:
            pair: Trading pair symbol, e.g. ``"AVAX/USDC"``.
            side: ``"BUY"`` or ``"SELL"`` (case-insensitive).
            amount: Order quantity in base-token units (human-readable).
            price: Limit price in quote-token units.  Required for ``"LIMIT"``
                orders; ignored for ``"MARKET"`` orders.
            order_type: ``"LIMIT"`` (default) or ``"MARKET"``.
            wait_for_receipt: If ``True``, block until the transaction is
                confirmed on-chain and return the receipt status.
            client_order_id: Optional 32-byte hex string (``"0x"`` + 64 hex
                chars) to use as the client order identifier.  When omitted,
                a random ID is generated.

        Returns:
            Result containing ``{"status": str, "tx_hash": str, "client_order_id": str}``
            on success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")
        from_addr = cast(str, cast(Any, self.account).address)

        # Validate input parameters
        order_params_result = validate_order_params(pair, amount, price, order_type)
        if not order_params_result.success:
            return cast(Result[dict[Any, Any]], order_params_result)

        pair = self._normalize_user_pair(pair)

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

            # Use caller-provided client_order_id or generate a random one
            import secrets

            if client_order_id is not None:
                cid_result = validate_order_id_format(client_order_id, "client_order_id")
                if not cid_result.success:
                    return cast(Result[dict[Any, Any]], cid_result)
                client_order_id_bytes = self._get_order_id_bytes(client_order_id)
            else:
                client_order_id_bytes = secrets.token_bytes(32)

            # Struct: (clientOrderId, tradePairId, price, quantity, traderaddress, side, type1, type2, stp)
            # Using dictionary for safety
            order_struct = {
                "clientOrderId": client_order_id_bytes,
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
                            "client_order_id": "0x" + client_order_id_bytes.hex(),
                        }
                    )
                # Transaction reverted - _send_trade_tx should have raised, but handle just in case
                return Result.fail("Transaction reverted")

            return Result.ok(
                {
                    "status": "Order Sent",
                    "tx_hash": tx_hash_hex,
                    "client_order_id": "0x" + client_order_id_bytes.hex(),
                }
            )

        except Exception as e:
            error_msg = self._sanitize_error(e, "placing order")
            return Result.fail(error_msg)

    @staticmethod
    def _is_empty_order_data(order_data: Any) -> bool:
        """Return True when a contract order tuple represents an empty order."""
        return not order_data or len(order_data) == 0 or order_data[0] == b"\0" * 32

    @staticmethod
    async def _await_if_needed(value: Any) -> Any:
        """Await values that are awaitable; return direct values unchanged."""
        if hasattr(value, "__await__"):
            return await value
        return value

    async def _get_trader_checksum_address(self) -> Result[str]:
        """Return the current trader address in checksum form."""
        if not self.account:
            return Result.fail("Private key not configured.")

        w3_l1 = await self._get_w3_l1()
        if not w3_l1:
            return Result.fail("L1 provider not available.")

        return Result.ok(w3_l1.to_checksum_address(cast(str, cast(Any, self.account).address)))

    def _classify_order_id_input(self, order_id: str | bytes | int) -> str:
        """Classify an order-id input for deterministic resolution."""
        if isinstance(order_id, int):
            return "internal"
        if isinstance(order_id, bytes):
            return "ambiguous"
        if order_id.startswith("0x"):
            return "ambiguous"
        if order_id.isdigit():
            return "internal"
        if len(order_id) == 64 and all(c in "0123456789abcdefABCDEF" for c in order_id):
            return "ambiguous"
        return "client"

    def _build_order_resolution_sequence(self, order_id: str | bytes | int) -> list[str]:
        """Build the deterministic lookup sequence for an order-id input."""
        kind = self._classify_order_id_input(order_id)
        if kind == "client":
            return ["client"]
        return ["internal", "client"]

    async def _fetch_order_by_internal_id(
        self, order_id_bytes: bytes
    ) -> Result[tuple[Any, ...] | None]:
        """Fetch an order using the internal-id contract lookup."""
        contract = self.trade_pairs_contract
        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            order_data = await self._await_if_needed(
                contract.functions.getOrder(order_id_bytes).call()
            )
            if self._is_empty_order_data(order_data):
                return Result.ok(None)
            return Result.ok(order_data)
        except Exception as e:
            return Result.fail(self._sanitize_error(e, "getting order by internal ID"))

    async def _fetch_order_by_client_id(
        self, client_order_id_bytes: bytes
    ) -> Result[tuple[Any, ...] | None]:
        """Fetch an order using the canonical client-id contract lookup sequence."""
        contract = self.trade_pairs_contract
        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        trader_result = await self._get_trader_checksum_address()
        if not trader_result.success:
            return cast(Result[tuple[Any, ...] | None], trader_result)
        trader = trader_result.data

        errors: list[Exception] = []
        for method_name in ("getOrderByClientOrderId", "getOrderByClientId"):
            method = getattr(contract.functions, method_name, None)
            if method is None:
                continue
            try:
                order_data = await self._await_if_needed(
                    method(trader, client_order_id_bytes).call()
                )
                if self._is_empty_order_data(order_data):
                    continue
                return Result.ok(order_data)
            except Exception as e:
                errors.append(e)

        if errors:
            return Result.fail(self._sanitize_error(errors[0], "getting order by client ID"))
        return Result.ok(None)

    async def _resolve_order_reference(
        self, order_id: str | bytes | int, *, allow_int: bool = False
    ) -> Result[dict[str, Any]]:
        """Resolve an order reference to a specific order and identifier type.

        Returns:
            Result containing:
              - ``id_type``: ``"internal"`` or ``"client"``
              - ``input_bytes``: normalized bytes32 form of the caller-provided ID
              - ``order_data``: raw order tuple returned by the contract
              - ``internal_id_bytes``: canonical internal order ID bytes32
              - ``client_order_id_bytes``: canonical client order ID bytes32
        """
        if isinstance(order_id, int):
            if not allow_int:
                return Result.fail("Invalid order_id: must be string or bytes, got int")
        else:
            order_id_result = validate_order_id_format(order_id, "order_id")
            if not order_id_result.success:
                return cast(Result[dict[str, Any]], order_id_result)

        contract = self.trade_pairs_contract
        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            input_bytes = self._get_order_id_bytes(order_id)
        except Exception as e:
            return Result.fail(self._sanitize_error(e, "normalizing order ID"))

        attempts = self._build_order_resolution_sequence(order_id)
        errors: list[str] = []

        for attempt in attempts:
            if attempt == "internal":
                result = await self._fetch_order_by_internal_id(input_bytes)
            else:
                result = await self._fetch_order_by_client_id(input_bytes)

            if not result.success:
                if result.error:
                    errors.append(result.error)
                continue

            order_data = result.data
            if order_data is None:
                continue

            return Result.ok(
                {
                    "id_type": attempt,
                    "input_bytes": input_bytes,
                    "order_data": order_data,
                    "internal_id_bytes": order_data[0],
                    "client_order_id_bytes": order_data[1],
                }
            )

        if errors:
            return Result.fail(errors[0])
        return Result.fail("Order not found (checked supported ID paths).")

    @track_method("clob")
    async def cancel_order(
        self, order_id: str | bytes, wait_for_receipt: bool = True
    ) -> Result[dict]:
        """Cancel a single open order by its Internal ID or Client Order ID.

        Resolves the provided order reference deterministically before executing
        the matching contract method. Internal IDs cancel via ``cancelOrder``.
        Client order IDs cancel via ``cancelOrderByClientId``.

        Args:
            order_id: Order identifier as a hex string (``"0x..."``), plain
                string, or ``bytes32``.  Both internal IDs and client order IDs
                are accepted.
            wait_for_receipt: If ``True``, block until the cancellation
                transaction is confirmed on-chain.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        # Validate order_id format
        order_id_result = validate_order_id_format(order_id, "order_id")
        if not order_id_result.success:
            return cast(Result[dict], order_id_result)

        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            resolved_result = await self._resolve_order_reference(order_id)
            if not resolved_result.success:
                return Result.fail(resolved_result.error or "Could not resolve order ID")

            resolved = resolved_result.data
            assert resolved is not None
            id_type = resolved["id_type"]
            if id_type == "client":
                function_call = contract.functions.cancelOrderByClientId(
                    resolved["client_order_id_bytes"]
                )
            else:
                function_call = contract.functions.cancelOrder(resolved["internal_id_bytes"])

            tx_hash_hex, receipt = await self._send_trade_tx(
                function_call,
                wait_for_receipt=wait_for_receipt,
            )
            if (
                wait_for_receipt
                and receipt
                and (receipt.status if hasattr(receipt, "status") else receipt.get("status", 1))
                != 1
            ):
                return Result.fail("Transaction reverted")
            return Result.ok(
                {
                    "tx_hash": tx_hash_hex,
                    "cancelled_client_order_id": "0x" + resolved["client_order_id_bytes"].hex(),
                    "cancelled_internal_order_id": "0x" + resolved["internal_id_bytes"].hex(),
                }
            )

        except Exception as e:
            error_msg = self._sanitize_error(e, "cancelling order")
            return Result.fail(error_msg)

    @track_method("clob")
    async def cancel_all_orders(self) -> Result[dict]:
        """Cancel all open orders for the current account in a single transaction.

        Fetches open orders via ``get_open_orders()`` then delegates to
        ``cancel_list_orders()`` with all internal IDs.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message if no open orders are found or the
            cancellation fails.
        """
        open_orders_result = await self.get_open_orders()
        if not open_orders_result.success:
            return Result.fail(open_orders_result.error or "Failed to fetch open orders")

        open_orders = open_orders_result.data
        if not open_orders:
            return Result.fail("No open orders to cancel.")

        # Extract internal order IDs (needed for cancelListOrders)
        order_ids = []
        for order in open_orders:
            if "internal_order_id" in order:
                order_ids.append(order["internal_order_id"])

        if not order_ids:
            return Result.fail("No valid order IDs found.")

        return cast(Result[dict], await self.cancel_list_orders(order_ids))

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
        """Transform API order response to match :meth:`_format_order_data` output.

        Extracts only canonical SDK fields, normalizing field names, side/type
        enums, and numeric string values.  Raw API fields (``id``,
        ``clientordid``, ``tx``, ``traderaddress``, etc.) are dropped so that
        consumers see exactly two clearly-labelled identifiers
        (``internal_order_id`` and ``client_order_id``).

        Args:
            order: Raw order dict from API response.

        Returns:
            Clean order dict with only canonical fields.
        """
        # Resolve internal order ID from any API variation
        internal_order_id = order.get("internal_order_id") or order.get("id")

        # Resolve client order ID from any API variation (prefer camelCase)
        client_order_id = (
            order.get("client_order_id") or order.get("clientOrderId") or order.get("clientordid")
        )

        # Normalize side: API returns int (0=BUY, 1=SELL)
        side_raw = order.get("side")
        if isinstance(side_raw, int):
            side: Any = "BUY" if side_raw == 0 else "SELL"
        else:
            side = side_raw

        # Normalize type: API returns int (0=MARKET, 1=LIMIT)
        type_raw = order.get("type")
        if isinstance(type_raw, int):
            order_type: Any = "MARKET" if type_raw == 0 else "LIMIT"
        else:
            order_type = type_raw

        # Coerce numeric strings to float
        def _to_num(val: object) -> float | None:
            if val is None:
                return None
            try:
                return float(val)  # type: ignore[arg-type]
            except (ValueError, TypeError):
                return None

        # Resolve filledQuantity from any API variation
        filled_qty = (
            order.get("filledQuantity")
            or order.get("quantityfilled")
            or order.get("filledquantity")
            or order.get("filled_quantity")
        )

        return {
            "internal_order_id": internal_order_id,
            "client_order_id": client_order_id,
            "pair": order.get("pair"),
            "side": side,
            "type": order_type,
            "price": _to_num(order.get("price")),
            "quantity": _to_num(order.get("quantity")),
            "filledQuantity": _to_num(filled_qty),
            "status": order.get("status"),
        }

    async def get_open_orders(self, pair: str | None = None) -> Result[list]:
        """Fetch open orders for the current account from the REST API.

        Args:
            pair: Optional trading pair filter, e.g. ``"AVAX/USDC"``.  If
                ``None``, returns all open orders across all pairs.

        Returns:
            Result containing a list of order dicts on success, or an error
            message on failure.  Each dict includes fields such as
            ``internal_order_id``, ``client_order_id``, ``tradePairId``,
            ``price``, ``quantity``, ``filledQuantity``, ``side``, and
            ``status``.
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        # Validate pair format if provided
        if pair is not None:
            pair_result = validate_pair_format(pair, "pair")
            if not pair_result.success:
                return cast(Result[list[Any]], pair_result)
            pair = self._normalize_user_pair(pair)

        endpoint = ENDPOINT_SIGNED_ORDERS
        url = f"{self.api_base_url}{endpoint}"

        try:
            headers = self._get_auth_headers()
            params: dict[str, Any] = {"category": 0}  # 0 = Open Orders
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
    async def get_order(self, order_id: str | bytes) -> Result[dict]:
        """Fetch the details of an order by its Internal ID or Client Order ID.

        Resolves the provided ID deterministically. Internal lookup is attempted
        for internal/ambiguous IDs; client lookup is attempted for client/ambiguous IDs.

        Args:
            order_id: Order identifier as a hex string (``"0x..."``), plain
                string, or ``bytes32``.

        Returns:
            Result containing an order dict with ``internal_order_id``,
            ``client_order_id``, ``tradePairId``, ``price``, ``quantity``,
            ``filledQuantity``, ``side``, ``type``, ``status``, and ``pair``
            on success, or an error message on failure.
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
            resolved_result = await self._resolve_order_reference(order_id)
            if not resolved_result.success:
                return Result.fail(resolved_result.error or "Order not found")
            resolved = resolved_result.data
            assert resolved is not None
            order_details = await self._format_order_data(resolved["order_data"])
            return Result.ok(order_details)

        except Exception as e:
            error_msg = self._sanitize_error(e, "getting order")
            return Result.fail(error_msg)

    @track_method("clob")
    async def get_order_by_client_id(self, client_order_id: str | bytes) -> Result[dict]:
        """Fetch the details of an order by its Client Order ID only.

        Unlike ``get_order``, this method uses the client-ID lookup path only.

        Args:
            client_order_id: Client-generated order ID as a hex string
                (``"0x..."``), plain string, or ``bytes32``.

        Returns:
            Result containing an order dict (same fields as ``get_order``) on
            success, or an error message on failure.
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
            client_order_id_bytes = self._get_order_id_bytes(client_order_id)
            fetch_result = await self._fetch_order_by_client_id(client_order_id_bytes)
            if not fetch_result.success:
                return Result.fail(fetch_result.error or "Order not found")
            order_data = fetch_result.data
            if order_data is None:
                return Result.fail("Order not found (Client ID).")
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
            "internal_order_id": w3_l1.to_hex(order_data[0]),
            "client_order_id": w3_l1.to_hex(order_data[1]),
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

    def _build_order_tuple(
        self, order, pair_data, side_enum, w3, client_order_id_bytes: bytes | None = None
    ):
        """Build order tuple for contract call."""
        import secrets

        price, amount = self._normalize_order_amounts(order["price"], order["amount"], pair_data)

        price_wei = int(price * (10 ** pair_data["quote_decimals"]))
        qty_wei = int(amount * (10 ** pair_data["base_decimals"]))
        if client_order_id_bytes is None:
            client_order_id_bytes = secrets.token_bytes(32)
        client_order_id_hex = "0x" + client_order_id_bytes.hex()

        # Struct: (clientOrderId, tradePairId, price, quantity, traderaddress, side, type1, type2, stp)
        assert self.account is not None
        trader = cast(str, cast(Any, self.account).address)
        order_tuple = (
            client_order_id_bytes,
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
            pair_raw = order["pair"]
            pr = validate_pair_format(pair_raw, "pair")
            if not pr.success:
                return None, None, None, pr.error or "Invalid pair"
            pair = self._normalize_user_pair(pair_raw)
            order["pair"] = pair
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

            # Use caller-provided client_order_id or let _build_order_tuple generate one
            cid_bytes: bytes | None = None
            if raw_cid := order.get("client_order_id"):
                cid_result = validate_order_id_format(raw_cid, "client_order_id")
                if not cid_result.success:
                    return None, None, None, cid_result.error or "Invalid client_order_id"
                cid_bytes = self._get_order_id_bytes(raw_cid)

            order_tuple, client_order_id_hex = self._build_order_tuple(
                order, pair_data, side_enum, w3, client_order_id_bytes=cid_bytes
            )
            order_tuples.append(order_tuple)
            client_order_ids.append(client_order_id_hex)

        return order_tuples, client_order_ids, required_balances, None

    @track_method("clob")
    async def add_limit_order_list(
        self, orders: list[dict], wait_for_receipt: bool = True
    ) -> Result[dict]:
        """Place multiple limit orders in a single on-chain transaction.

        Checks aggregated portfolio balances across all orders before submitting.

        Args:
            orders: List of order dicts, each with:
                - ``pair`` (str): Trading pair, e.g. ``"AVAX/USDC"``.
                - ``side`` (str): ``"BUY"`` or ``"SELL"``.
                - ``amount`` (float): Quantity in base-token units.
                - ``price`` (float): Limit price in quote-token units.
            wait_for_receipt: If ``True``, block until the transaction is
                confirmed on-chain.

        Returns:
            Result containing ``{"tx_hash": str, "client_order_ids": list[str]}``
            on success, or an error message on failure.
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
    async def cancel_list_orders(
        self, order_ids: list, wait_for_receipt: bool = True
    ) -> Result[dict]:
        """Cancel multiple orders by Internal ID in a single on-chain transaction.

        Args:
            order_ids: List of internal order IDs.  Each element may be a hex
                string (``"0x..."``), a decimal string, an integer, or ``bytes32``.
            wait_for_receipt: If ``True``, block until the cancellation is
                confirmed on-chain.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            order_ids_bytes = [self._get_order_id_bytes(oid) for oid in order_ids]

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
            return Result.ok(
                {
                    "tx_hash": tx_hash_hex,
                    "cancelled_internal_order_ids": list(order_ids),
                }
            )

        except Exception as e:
            error_msg = self._sanitize_error(e, "cancelling list orders")
            return Result.fail(error_msg)

    @track_method("clob")
    async def replace_order(
        self,
        order_id: str | bytes,
        new_price: float,
        new_amount: float,
        wait_for_receipt: bool = True,
        client_order_id: str | None = None,
    ) -> Result[dict]:
        """Cancel an existing order and replace it atomically with new price and quantity.

        Uses the contract's ``cancelReplaceOrder`` function.  Fetches the
        existing order to determine the pair and decimal precision.

        Args:
            order_id: Identifier of the order to replace (hex string, plain
                string, or ``bytes32``).  Accepts either internal or client
                order ID.
            new_price: New limit price in quote-token units.
            new_amount: New quantity in base-token units.
            wait_for_receipt: If ``True``, block until the transaction is
                confirmed on-chain.
            client_order_id: Optional 32-byte hex string for the replacement
                order.  When omitted, a random ID is generated.

        Returns:
            Result containing ``{"tx_hash": str, "cancelled_client_order_id": str,
            "cancelled_internal_order_id": str, "client_order_id": str}`` on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            resolved_result = await self._resolve_order_reference(order_id)
            if not resolved_result.success:
                return Result.fail(
                    f"Could not fetch order details for replacement: {resolved_result.error}"
                )
            resolved = resolved_result.data
            assert resolved is not None
            order_id_bytes = resolved["internal_id_bytes"]
            order_details = await self._format_order_data(resolved["order_data"])
            pair_name = order_details.get("pair")
            if not pair_name:
                return Result.fail("Could not determine pair from order details.")

            pair_data = self.pairs[pair_name]

            price_wei = int(new_price * (10 ** pair_data["quote_decimals"]))
            qty_wei = int(new_amount * (10 ** pair_data["base_decimals"]))

            import secrets

            if client_order_id is not None:
                cid_result = validate_order_id_format(client_order_id, "client_order_id")
                if not cid_result.success:
                    return cast(Result[dict], cid_result)
                new_client_order_id_bytes = self._get_order_id_bytes(client_order_id)
            else:
                new_client_order_id_bytes = secrets.token_bytes(32)

            tx_hash_hex, receipt = await self._send_trade_tx(
                contract.functions.cancelReplaceOrder(
                    order_id_bytes, new_client_order_id_bytes, price_wei, qty_wei
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
            return Result.ok(
                {
                    "tx_hash": tx_hash_hex,
                    "cancelled_client_order_id": "0x" + resolved["client_order_id_bytes"].hex(),
                    "cancelled_internal_order_id": "0x" + resolved["internal_id_bytes"].hex(),
                    "client_order_id": "0x" + new_client_order_id_bytes.hex(),
                }
            )
        except Exception as e:
            error_msg = self._sanitize_error(e, "replacing order")
            return Result.fail(error_msg)

    @track_method("clob")
    async def cancel_order_by_client_id(
        self, client_order_id: str | bytes, wait_for_receipt: bool = True
    ) -> Result[dict]:
        """Cancel a single open order by its Client Order ID only."""
        if not self.account:
            return Result.fail("Private key not configured.")

        client_order_id_result = validate_order_id_format(client_order_id, "client_order_id")
        if not client_order_id_result.success:
            return cast(Result[dict], client_order_id_result)

        contract = self.trade_pairs_contract
        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            client_order_id_bytes = self._get_order_id_bytes(client_order_id)
            tx_hash_hex, receipt = await self._send_trade_tx(
                contract.functions.cancelOrderByClientId(client_order_id_bytes),
                wait_for_receipt=wait_for_receipt,
            )
            if (
                wait_for_receipt
                and receipt
                and (receipt.status if hasattr(receipt, "status") else receipt.get("status", 1))
                != 1
            ):
                return Result.fail("Transaction reverted")
            return Result.ok(
                {
                    "tx_hash": tx_hash_hex,
                    "cancelled_client_order_id": "0x" + client_order_id_bytes.hex(),
                }
            )
        except Exception as e:
            error_msg = self._sanitize_error(e, "cancelling order by client ID")
            return Result.fail(error_msg)

    @track_method("clob")
    async def cancel_list_orders_by_client_id(
        self, client_order_ids: list, wait_for_receipt: bool = True
    ) -> Result[dict]:
        """Cancel multiple orders by Client Order ID in a single on-chain transaction.

        Args:
            client_order_ids: List of client-generated order IDs.  Each element
                may be a hex string (``"0x..."``), a plain string, or ``bytes32``.
            wait_for_receipt: If ``True``, block until the cancellation is
                confirmed on-chain.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        contract = self.trade_pairs_contract

        if not contract:
            return Result.fail("TradePairs contract not initialized.")

        try:
            order_ids_bytes = [self._get_order_id_bytes(oid) for oid in client_order_ids]

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
            return Result.ok(
                {
                    "tx_hash": tx_hash_hex,
                    "cancelled_client_order_ids": list(client_order_ids),
                }
            )

        except Exception as e:
            error_msg = self._sanitize_error(e, "cancelling list orders by client ID")
            return Result.fail(error_msg)

    def _resolve_cancel_add_pair_from_replacement(
        self,
        raw_pair: str | None,
        inferred_pair: str | None,
        order_id: object,
    ) -> Result[str]:
        if raw_pair:
            pr = validate_pair_format(raw_pair, "pair")
            if not pr.success:
                return Result.fail(pr.error or "Invalid pair")
            norm_rep = self._normalize_user_pair(raw_pair)
            norm_inf = self._normalize_user_pair(inferred_pair) if inferred_pair else None
            if inferred_pair and norm_rep != norm_inf:
                return Result.fail(
                    f"Replacement pair '{raw_pair}' does not match existing order pair '{inferred_pair}'."
                )
            return Result.ok(norm_rep)
        if not inferred_pair:
            return Result.fail(
                f"Replacement for order '{order_id}' requires pair because it could not be inferred."
            )
        return Result.ok(inferred_pair)

    async def _process_replacement(
        self, rep: dict, from_addr: str, required_balances: dict[str, float]
    ) -> Result[dict]:
        """Resolve and build a single replacement entry for cancel_add_list."""
        import secrets

        resolved_result = await self._resolve_order_reference(rep["order_id"], allow_int=True)
        if not resolved_result.success:
            return Result.fail(
                f"Could not resolve order '{rep['order_id']}' for cancel/add: {resolved_result.error}"
            )
        resolved = resolved_result.data
        assert resolved is not None

        existing_order = await self._format_order_data(resolved["order_data"])
        pair_res = self._resolve_cancel_add_pair_from_replacement(
            rep.get("pair"), existing_order.get("pair"), rep["order_id"]
        )
        if not pair_res.success:
            return Result.fail(pair_res.error or "")
        pair = pair_res.data
        assert pair is not None
        if not await self._ensure_pair_exists(pair):
            return Result.fail(f"Pair {pair} not found.")

        pair_data = self.pairs[pair]
        raw_side = rep.get("side") or existing_order.get("side")
        if not raw_side:
            return Result.fail(
                f"Replacement for order '{rep['order_id']}' requires side "
                "because it could not be inferred from the existing order."
            )
        side_clean = raw_side.strip().upper()
        if side_clean == "BUY":
            side_enum, req_token, req_amt = 0, pair_data["quote"], rep["price"] * rep["amount"]
        elif side_clean == "SELL":
            side_enum, req_token, req_amt = 1, pair_data["base"], rep["amount"]
        else:
            return Result.fail(f"Invalid side '{rep['side']}'. Must be 'BUY' or 'SELL'.")

        required_balances[req_token] = required_balances.get(req_token, 0) + req_amt

        price, amount = rep["price"], rep["amount"]
        if "quote_display_decimals" in pair_data and price:
            price = round(price, pair_data["quote_display_decimals"])
        if "base_display_decimals" in pair_data:
            amount = round(amount, pair_data["base_display_decimals"])

        if raw_cid := rep.get("client_order_id"):
            cid_result = validate_order_id_format(raw_cid, "client_order_id")
            if not cid_result.success:
                return Result.fail(cid_result.error or "Invalid client_order_id")
            client_order_id = self._get_order_id_bytes(raw_cid)
        else:
            client_order_id = secrets.token_bytes(32)

        new_order_tuple = (
            client_order_id,
            pair_data["tradePairId"],
            int(price * (10 ** pair_data["quote_decimals"])),
            int(amount * (10 ** pair_data["base_decimals"])),
            from_addr,
            side_enum,
            1,  # LIMIT
            0,  # GTC
            0,  # STP
        )
        return Result.ok(
            {
                "internal_id_bytes": resolved["internal_id_bytes"],
                "cancelled_client_order_id": "0x" + resolved["client_order_id_bytes"].hex(),
                "cancelled_internal_order_id": "0x" + resolved["internal_id_bytes"].hex(),
                "new_client_order_id": "0x" + client_order_id.hex(),
                "new_order_tuple": new_order_tuple,
            }
        )

    @track_method("clob")
    async def cancel_add_list(
        self, replacements: list[dict], wait_for_receipt: bool = True
    ) -> Result[dict]:
        """Cancel existing orders and place new ones atomically in a single transaction.

        Uses the contract's ``cancelAddList`` function.

        Args:
            replacements: List of replacement dicts, each with:
                - ``order_id`` (str | bytes): ID of the order to cancel.
                - ``pair`` (str): Trading pair, e.g. ``"AVAX/USDC"``.
                - ``side`` (str): ``"BUY"`` or ``"SELL"``.
                - ``amount`` (float): New quantity in base-token units.
                - ``price`` (float): New limit price in quote-token units.
                - ``client_order_id`` (str, optional): 32-byte hex string for
                  the new replacement order.  When omitted, a random ID is
                  generated.
            wait_for_receipt: If ``True``, block until the transaction is
                confirmed on-chain.

        Returns:
            Result containing ``{"tx_hash": str, "cancelled_client_order_ids":
            list[str], "cancelled_internal_order_ids": list[str],
            "client_order_ids": list[str]}`` on success, or an error message
            on failure.
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
            new_client_order_ids: list[str] = []
            cancelled_client_order_ids: list[str] = []
            cancelled_internal_order_ids: list[str] = []
            required_balances: dict[str, float] = {}

            for rep in replacements:
                entry_result = await self._process_replacement(rep, from_addr, required_balances)
                if not entry_result.success:
                    return Result.fail(entry_result.error or "")
                entry = entry_result.data
                assert entry is not None
                order_ids.append(entry["internal_id_bytes"])
                cancelled_client_order_ids.append(entry["cancelled_client_order_id"])
                cancelled_internal_order_ids.append(entry["cancelled_internal_order_id"])
                new_client_order_ids.append(entry["new_client_order_id"])
                new_orders.append(entry["new_order_tuple"])

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
                    return Result.ok(
                        {
                            "tx_hash": tx_hash_hex,
                            "cancelled_client_order_ids": cancelled_client_order_ids,
                            "cancelled_internal_order_ids": cancelled_internal_order_ids,
                            "client_order_ids": new_client_order_ids,
                        }
                    )
                # Transaction reverted - _send_trade_tx should have raised, but handle just in case
                return Result.fail("Transaction reverted")

            return Result.ok(
                {
                    "tx_hash": tx_hash_hex,
                    "cancelled_client_order_ids": cancelled_client_order_ids,
                    "cancelled_internal_order_ids": cancelled_internal_order_ids,
                    "client_order_ids": new_client_order_ids,
                }
            )

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
        """Subscribe to real-time WebSocket events for a topic.

        Requires ``ws_manager_enabled=True`` in config (or ``DEXALOT_WS_MANAGER_ENABLED=true``).
        The WebSocket connection is started automatically on first subscription.

        Args:
            topic: Topic to subscribe to.  Public orderbook topics use the
                format ``"AVAX/USDC"`` or ``"OrderBook/AVAX/USDC"``.  Private
                trader event topics (e.g. ``"Orders"``) require ``is_private=True``.
            callback: Async or sync callable invoked with each message payload
                (a dict).  Runs on the asyncio event loop.
            is_private: Set to ``True`` for topics that require authentication
                (orders, executions, balance updates).

        Raises:
            RuntimeError: If ``ws_manager_enabled`` is ``False`` in config.
            ValueError: If the orderbook pair for a public topic is not found.
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
            pr_ob = validate_pair_format(orderbook_pair, "pair")
            if not pr_ob.success:
                raise ValueError(
                    pr_ob.error or f"Invalid trading pair in WebSocket topic: {orderbook_pair}"
                )
            orderbook_pair = self._normalize_user_pair(orderbook_pair)
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
        """Unsubscribe from a previously subscribed WebSocket topic.

        No-op if the WebSocket manager is not initialised or the topic is not
        currently subscribed.

        Args:
            topic: Topic string matching the one used in ``subscribe_to_events``.
        """
        if hasattr(self, "_ws_manager") and self._ws_manager is not None:
            self._ws_manager.unsubscribe(topic)

    async def close_websocket(self, *, grace_s: float = 3.0) -> None:
        """Gracefully close the WebSocket connection and cancel its background task.

        Awaits ``disconnect()`` on the WebSocket manager with a timeout of
        ``grace_s`` seconds.  Returns cleanly on timeout or ``CancelledError``
        so that teardown code is not blocked.

        Args:
            grace_s: Maximum seconds to wait for the background task to finish
                before returning regardless.  Defaults to ``3.0``.
        """
        if not hasattr(self, "_ws_manager") or self._ws_manager is None:
            return
        mgr = self._ws_manager
        self._ws_manager = None

        timeout = grace_s if grace_s > 0 else 1.0
        try:
            await asyncio.wait_for(mgr.disconnect(), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError, Exception):
            pass

    async def _ensure_pair_exists(self, pair):
        """Ensure pair exists in local cache, fetching if necessary."""
        if pair not in self.pairs:
            await self.get_clob_pairs()
            if pair not in self.pairs:
                return False
        return True

    def _get_order_id_bytes(self, order_id):
        """Convert an order identifier to canonical bytes32 form."""
        if isinstance(order_id, str):
            stripped = order_id.strip()
            if stripped.startswith("0x"):
                hex_str = stripped[2:]
                if len(hex_str) % 2 != 0:
                    raise ValueError("Hex order IDs must have an even number of characters.")
                return bytes.fromhex(hex_str).rjust(32, b"\0")
            if stripped.isdigit():
                return int(stripped).to_bytes(32, "big")
            if len(stripped) == 64 and all(c in "0123456789abcdefABCDEF" for c in stripped):
                return bytes.fromhex(stripped)
            encoded = stripped.encode("utf-8")
            if len(encoded) > 32:
                raise ValueError("Plain-string order IDs must fit in 32 bytes.")
            return encoded.ljust(32, b"\0")
        elif isinstance(order_id, int):
            return order_id.to_bytes(32, "big")
        elif isinstance(order_id, bytes):
            return order_id.rjust(32, b"\0")
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
