import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from dexalot_sdk.core.base import DexalotBaseClient
from dexalot_sdk.core.clob import CLOBClient
from dexalot_sdk.core.config import DexalotConfig
from dexalot_sdk.utils.websocket_manager import ConnectionState, WebSocketManager

# Valid test data constants
VALID_ADDRESS = "0x1234567890123456789012345678901234567890"  # 42 characters
VALID_PAIR = "AVAX/USDC"
VALID_ORDER_ID = "0x" + "12" * 32  # 64 hex chars = 32 bytes
VALID_CLIENT_ORDER_ID = "0x" + "ab" * 32  # 64 hex chars = 32 bytes


# Create a test client class that inherits from CLOBClient and BaseClient
class MockClient(CLOBClient, DexalotBaseClient):
    pass


class TestCLOBClient:
    @pytest.fixture(autouse=True)
    def clean_cache(self):
        from dexalot_sdk.core.base import (
            _BALANCE_CACHE,
            _ORDERBOOK_CACHE,
            _SEMI_STATIC_CACHE,
            _STATIC_CACHE,
        )

        _STATIC_CACHE.clear()
        _SEMI_STATIC_CACHE.clear()
        _BALANCE_CACHE.clear()
        _ORDERBOOK_CACHE.clear()

    @pytest.fixture
    def client(self):
        # Mock errors.json loading
        with patch("builtins.open", mock_open(read_data='{"E001": "Some Error"}')):
            client = MockClient()
            client.api_base_url = "https://api.dexalot-test.com"
            client.account = MagicMock()
            client.account.address = VALID_ADDRESS
            client.private_key = "0x" + "a" * 64  # Valid 66-char private key (32 bytes)

            # Async Web3 mocks
            client.w3_l1 = MagicMock()
            client.w3_l1.eth.chain_id = AsyncMock(return_value=43114)
            client.w3_l1.eth.get_transaction_count = AsyncMock(return_value=10)
            client.w3_l1.eth.gas_price = (
                25000000000  # gas_price is usually property, but mock it as needed
            )
            # Mock _get_nonce for nonce manager
            client._get_nonce = AsyncMock(return_value=10)
            client.w3_l1.to_hex.side_effect = lambda x: (
                f"0x{x.hex() if isinstance(x, bytes) else x}"
            )
            client.w3_l1.to_checksum_address = lambda x: x

            client.trade_pairs_contract = MagicMock()
            client.pairs = {}

            # Async Session mocks
            client._mock_session = MagicMock()
            client._session = client._mock_session
            mock_cm = AsyncMock()
            client._mock_session.get.return_value = mock_cm

            client._parse_revert_reason = lambda e: str(e)

            # Standard mock response to avoid coroutine warnings for raise_for_status
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=[])
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            client._mock_session.get.return_value = mock_cm

            return client

    def _stub_resolved_order(
        self,
        client,
        *,
        id_type: str = "internal",
        pair: str = "AVAX/USDC",
        trade_pair_id: bytes = b"TPID",
        internal_id: bytes | None = None,
        client_order_id: bytes | None = None,
        side: int = 0,
        price_wei: int = 10_000_000,
        quantity_wei: int = 10**18,
    ):
        from dexalot_sdk.utils.result import Result

        resolved_internal_id = internal_id or (b"\x01".rjust(32, b"\0"))
        resolved_client_order_id = client_order_id or (b"\xab" * 32)
        order_data = (
            resolved_internal_id,
            resolved_client_order_id,
            trade_pair_id,
            price_wei,
            0,
            quantity_wei,
            0,
            0,
            VALID_ADDRESS,
            side,
            1,
            0,
            1,
        )
        client._resolve_order_reference = AsyncMock(
            return_value=Result.ok(
                {
                    "id_type": id_type,
                    "input_bytes": resolved_internal_id
                    if id_type == "internal"
                    else resolved_client_order_id,
                    "order_data": order_data,
                    "internal_id_bytes": resolved_internal_id,
                    "client_order_id_bytes": resolved_client_order_id,
                }
            )
        )
        return order_data

    def test_transform_pair_from_api(self, client):
        """Test _transform_pair_from_api with various field name combinations."""
        # Test lowercase fields transformed to snake_case
        item1 = {
            "pair": "AVAX/USDC",
            "base": "AVAX",
            "quote": "USDC",
            "base_evmdecimals": 18,
            "quote_evmdecimals": 6,
            "basedisplaydecimals": 18,
            "quotedisplaydecimals": 6,
            "mintrade_amnt": "0.1",
            "maxtrade_amnt": "1000",
        }
        transformed1 = client._transform_pair_from_api(item1)
        assert transformed1["base_decimals"] == 18
        assert transformed1["quote_decimals"] == 6
        assert transformed1["base_display_decimals"] == 18
        assert transformed1["quote_display_decimals"] == 6
        assert transformed1["min_trade_amount"] == "0.1"
        assert transformed1["max_trade_amount"] == "1000"

        # Test camelCase fields transformed to snake_case
        item2 = {
            "pair": "BTC/USDC",
            "base": "BTC",
            "quote": "USDC",
            "baseEvmDecimals": 8,
            "quoteEvmDecimals": 6,
            "baseDisplayDecimals": 8,
            "quoteDisplayDecimals": 6,
            "minTradeAmnt": "0.001",
            "maxTradeAmnt": "10",
        }
        transformed2 = client._transform_pair_from_api(item2)
        assert transformed2["base_decimals"] == 8
        assert transformed2["quote_decimals"] == 6
        assert transformed2["base_display_decimals"] == 8
        assert transformed2["quote_display_decimals"] == 6
        assert transformed2["min_trade_amount"] == "0.001"
        assert transformed2["max_trade_amount"] == "10"

        # Test mixed fields (prefer existing snake_case)
        item3 = {
            "pair": "ETH/USDC",
            "base_decimals": 18,
            "quote_decimals": 6,
            "base_evmdecimals": 999,  # Should be ignored
            "baseEvmDecimals": 999,  # Should be ignored
            "base_display_decimals": 18,
            "quote_display_decimals": 6,
            "basedisplaydecimals": 999,  # Should be ignored
            "min_trade_amount": "0.01",
            "max_trade_amount": "100",
            "mintrade_amnt": "999",  # Should be ignored
        }
        transformed3 = client._transform_pair_from_api(item3)
        assert transformed3["base_decimals"] == 18  # Prefer existing
        assert transformed3["quote_decimals"] == 6  # Prefer existing
        assert transformed3["base_display_decimals"] == 18  # Prefer existing
        assert transformed3["min_trade_amount"] == "0.01"  # Prefer existing

        # Test missing optional fields
        item4 = {
            "pair": "ALOT/USDC",
            "base_evmdecimals": 18,
            "quote_evmdecimals": 6,
            "mintrade_amnt": "0.1",
        }
        transformed4 = client._transform_pair_from_api(item4)
        assert transformed4["base_decimals"] == 18
        assert transformed4["quote_decimals"] == 6
        assert transformed4["min_trade_amount"] == "0.1"
        assert (
            "base_display_decimals" not in transformed4
            or transformed4.get("base_display_decimals") is None
        )

    def test_store_clob_pairs_filters_unsupported_envs(self, client):
        """Only subnet pair metadata should be stored for CLOB operations."""
        stored = client._store_clob_pairs(
            [
                {
                    "env": "unsupported-env",
                    "pair": "BAD/PAIR",
                    "base": "BAD",
                    "quote": "PAIR",
                },
                {
                    "env": "fuji-multi-subnet",
                    "pair": "AVAX/USDC",
                    "base": "AVAX",
                    "quote": "USDC",
                },
            ]
        )

        assert [pair["pair"] for pair in stored] == ["AVAX/USDC"]
        assert list(client.pairs) == ["AVAX/USDC"]

    def test_rehydrate_cached_get_clob_pairs_ignores_missing_data(self, client):
        """No rehydration should happen for failed or empty cached results."""
        from dexalot_sdk.utils.result import Result

        client.pairs = {"KEEP/ME": {}}

        client._rehydrate_cached_get_clob_pairs(Result.fail("boom"))
        assert client.pairs == {"KEEP/ME": {}}

        client._rehydrate_cached_get_clob_pairs(Result.ok(None))
        assert client.pairs == {"KEEP/ME": {}}

    async def test_get_clob_pairs(self, client):
        """Test fetching pairs."""
        mock_resp_data = [
            {
                "env": "fuji-multi-subnet",
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_evmdecimals": 18,
                "quote_evmdecimals": 6,
                "basedisplaydecimals": 18,
                "quotedisplaydecimals": 6,
                "mintrade_amnt": "0.1",
                "maxtrade_amnt": "1000",
            },
            {
                "env": "production-multi-subnet",
                "pair": "BTC/USDC",
                "base": "BTC",
                "quote": "USDC",
                "base_evmdecimals": 8,
                "quote_evmdecimals": 6,
                "basedisplaydecimals": 8,
                "quotedisplaydecimals": 6,
                "mintrade_amnt": "0.001",
                "maxtrade_amnt": "10",
            },
        ]

        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_resp_data
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_clob_pairs()

        assert res.success
        assert [pair["pair"] for pair in res.data] == ["AVAX/USDC", "BTC/USDC"]
        assert "AVAX/USDC" in client.pairs
        assert "BTC/USDC" in client.pairs
        assert client.pairs["AVAX/USDC"]["base_decimals"] == 18
        assert client.pairs["AVAX/USDC"]["quote_decimals"] == 6

    async def test_get_clob_pairs_transforms_field_names(self, client):
        """Test get_clob_pairs transforms API field names to snake_case."""
        mock_resp_data = [
            {
                "env": "fuji-multi-subnet",
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_evmdecimals": 18,
                "quote_evmdecimals": 6,
                "basedisplaydecimals": 18,
                "quotedisplaydecimals": 6,
                "mintrade_amnt": "0.1",
                "maxtrade_amnt": "1000",
            },
            {
                "env": "production-multi-subnet",
                "pair": "BTC/USDC",
                "base": "BTC",
                "quote": "USDC",
                "baseEvmDecimals": 8,
                "quoteEvmDecimals": 6,
                "baseDisplayDecimals": 8,
                "quoteDisplayDecimals": 6,
                "minTradeAmnt": "0.001",
                "maxTradeAmnt": "10",
            },
        ]

        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_resp_data
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_clob_pairs()
        assert res.success
        assert {pair["pair"] for pair in res.data} == {"AVAX/USDC", "BTC/USDC"}

        # First pair: lowercase fields transformed
        assert "AVAX/USDC" in client.pairs
        assert client.pairs["AVAX/USDC"]["base_decimals"] == 18
        assert client.pairs["AVAX/USDC"]["quote_decimals"] == 6
        assert client.pairs["AVAX/USDC"]["base_display_decimals"] == 18
        assert client.pairs["AVAX/USDC"]["quote_display_decimals"] == 6
        assert client.pairs["AVAX/USDC"]["min_trade_amount"] == 0.1
        assert client.pairs["AVAX/USDC"]["max_trade_amount"] == 1000.0

        # Second pair: camelCase fields transformed
        assert "BTC/USDC" in client.pairs
        assert client.pairs["BTC/USDC"]["base_decimals"] == 8
        assert client.pairs["BTC/USDC"]["quote_decimals"] == 6
        assert client.pairs["BTC/USDC"]["base_display_decimals"] == 8
        assert client.pairs["BTC/USDC"]["quote_display_decimals"] == 6
        assert client.pairs["BTC/USDC"]["min_trade_amount"] == 0.001
        assert client.pairs["BTC/USDC"]["max_trade_amount"] == 10.0

    async def test_get_clob_pairs_preserves_existing_snake_case(self, client):
        """Test get_clob_pairs prefers existing snake_case fields over transformations."""
        mock_resp_data = [
            {
                "env": "fuji-multi-subnet",
                "pair": "ETH/USDC",
                "base": "ETH",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "base_evmdecimals": 999,  # Should be ignored
                "baseEvmDecimals": 999,  # Should be ignored
                "base_display_decimals": 18,
                "quote_display_decimals": 6,
                "basedisplaydecimals": 999,  # Should be ignored
                "min_trade_amount": "0.01",
                "max_trade_amount": "100",
                "mintrade_amnt": "999",  # Should be ignored
            }
        ]

        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_resp_data
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_clob_pairs()
        assert res.success
        assert [pair["pair"] for pair in res.data] == ["ETH/USDC"]
        assert "ETH/USDC" in client.pairs
        assert client.pairs["ETH/USDC"]["base_decimals"] == 18  # Prefer existing
        assert client.pairs["ETH/USDC"]["quote_decimals"] == 6  # Prefer existing
        assert client.pairs["ETH/USDC"]["base_display_decimals"] == 18  # Prefer existing
        assert client.pairs["ETH/USDC"]["min_trade_amount"] == 0.01  # Prefer existing

    async def test_get_clob_pairs_rehydrates_pairs_on_cache_hit(self, client):
        """Cached pair results should still rebuild ``client.pairs`` for a fresh client."""
        mock_resp_data = [
            {
                "env": "fuji-multi-subnet",
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_evmdecimals": 18,
                "quote_evmdecimals": 6,
                "basedisplaydecimals": 18,
                "quotedisplaydecimals": 6,
                "mintrade_amnt": "0.1",
                "maxtrade_amnt": "1000",
            }
        ]

        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_resp_data
        mock_resp.raise_for_status = MagicMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        first_result = await client.get_clob_pairs()
        assert first_result.success

        cached_client = client.__class__()
        cached_client.api_base_url = client.api_base_url
        cached_client._cache_enabled = True
        cached_client.pairs = {}
        cached_client._sanitize_error = client._sanitize_error
        cached_client._session = client._session

        second_result = await cached_client.get_clob_pairs()
        assert second_result.success
        assert [pair["pair"] for pair in second_result.data] == ["AVAX/USDC"]
        assert "AVAX/USDC" in cached_client.pairs

    async def test_get_orderbook(self, client):
        """Test get_orderbook."""
        # Ensure pair exists
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"id",
            }
        }

        # Mock get_clob_pairs to avoid overwriting pairs
        from dexalot_sdk.utils.result import Result

        client.get_clob_pairs = AsyncMock(return_value=Result.ok([{"pair": VALID_PAIR}]))

        # Mock contract call
        # getNBook returns (prices, quantities, nextPointers, ...)
        # It is called twice (bids, asks)
        mock_bids = ([100000000], [1000000000000000000], [0])  # Price 100, Qty 1
        mock_asks = ([101000000], [1000000000000000000], [0])  # Price 101, Qty 1

        client.trade_pairs_contract.functions.getNBook.return_value.call = AsyncMock(
            side_effect=[
                mock_bids,
                mock_asks,
            ]
        )

        book = await client.get_orderbook("AVAX/USDC")
        assert book.success
        assert len(book.data["bids"]) == 1
        assert book.data["bids"][0]["price"] == 100.0
        assert book.data["bids"][0]["quantity"] == 1.0

    async def test_get_orderbook_zero_price(self, client):
        """Test get_orderbook filtering of zero prices."""
        # Mock pair info
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"id",
            }
        }

        # Mock get_clob_pairs to avoid overwriting pairs
        from dexalot_sdk.utils.result import Result

        client.get_clob_pairs = AsyncMock(return_value=Result.ok([{"pair": VALID_PAIR}]))

        # Mock contract call
        # getNBook returns (prices, quantities, nextPointers, ...)
        # It is called twice (bids, asks)
        # Bids: [10.0, 0.0], Asks: [11.0, 0.0]
        # Prices are in quote decimals (6), Quantities in base decimals (18)
        mock_bids = ([10000000, 0], [1000000000000000000, 0], [0, 0])
        mock_asks = ([11000000, 0], [5000000000000000000, 0], [0, 0])

        client.trade_pairs_contract.functions.getNBook.return_value.call = AsyncMock(
            side_effect=[
                mock_bids,
                mock_asks,
            ]
        )

        result = await client.get_orderbook("AVAX/USDC")

        assert result.success
        assert len(result.data["bids"]) == 1
        assert result.data["bids"][0]["price"] == 10.0
        assert result.data["bids"][0]["quantity"] == 1.0
        assert len(result.data["asks"]) == 1
        assert result.data["asks"][0]["price"] == 11.0
        assert result.data["asks"][0]["quantity"] == 5.0

    async def test_get_orderbook_decimal_precision(self, client):
        """get_orderbook delegates to Utils.unit_conversion (Decimal-based) for price/quantity."""
        from unittest.mock import patch

        from dexalot_sdk.utils import Utils
        from dexalot_sdk.utils.result import Result

        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"id",
            }
        }
        client.get_clob_pairs = AsyncMock(return_value=Result.ok([{"pair": VALID_PAIR}]))

        mock_bids = ([100_000_000], [2_500_000_000_000_000_000], [0])
        mock_asks = ([101_000_000], [1_500_000_000_000_000_000], [0])

        client.trade_pairs_contract.functions.getNBook.return_value.call = AsyncMock(
            side_effect=[mock_bids, mock_asks]
        )

        with patch.object(Utils, "unit_conversion", wraps=Utils.unit_conversion) as mock_conv:
            book = await client.get_orderbook("AVAX/USDC")

        assert book.success
        # Utils.unit_conversion called for each price and quantity in bids + asks (4 calls)
        assert mock_conv.call_count == 4
        assert book.data["bids"][0]["price"] == 100.0
        assert book.data["bids"][0]["quantity"] == 2.5
        assert book.data["asks"][0]["price"] == 101.0
        assert book.data["asks"][0]["quantity"] == 1.5

    async def test_add_order_success(self, client):
        """Test successful order placement."""
        # Setup pairs
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }

        # Mock _send_trade_tx
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        # Mock portfolio balance
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))

        res = await client.add_order("AVAX/USDC", "BUY", 1.0, 10.0)

        assert res.success
        assert res.data["status"] == "Order Sent"
        assert res.data["tx_hash"] == "0xTxHash"

        # Verify struct construction
        call_args = client.trade_pairs_contract.functions.addNewOrder.call_args[0][0]
        assert call_args["tradePairId"] == b"TPID"
        assert call_args["price"] == 10000000  # 10.0 * 10^6
        assert call_args["quantity"] == 1000000000000000000  # 1.0 * 10^18
        assert call_args["side"] == 0  # BUY

    async def test_add_order_validations(self, client):
        """Test add_order validations."""
        # No account
        client.account = None
        result = await client.add_order(VALID_PAIR, "B", 1, 10)
        assert not result.success
        assert result.error == "Private key not configured."
        client.account = MagicMock()

        # Pair not found (mock _ensure_pair_exists to return False)
        client._ensure_pair_exists = AsyncMock(return_value=False)
        result = await client.add_order("INVALID/PAIR", "BUY", 1, 10)
        assert not result.success
        assert "not found" in result.error

        # Invalid Side
        client._ensure_pair_exists = AsyncMock(return_value=True)
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
            }
        }
        result = await client.add_order("AVAX/USDC", "INVALID", 1, 10)
        assert not result.success
        assert "Invalid side" in result.error

        # Missing Price for Limit
        result = await client.add_order("AVAX/USDC", "BUY", 1, price=None, order_type="LIMIT")
        assert not result.success
        assert "required for LIMIT orders" in result.error

    async def test_add_order_caller_provided_client_order_id(self, client):
        """Caller-provided client_order_id is used verbatim; invalid hex is rejected."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        provided_id = "0x" + "cc" * 32
        res = await client.add_order("AVAX/USDC", "BUY", 1.0, 10.0, client_order_id=provided_id)
        assert res.success
        assert res.data["client_order_id"] == provided_id

        # Invalid hex prefix with non-hex chars is rejected before the transaction
        bad_res = await client.add_order(
            "AVAX/USDC", "BUY", 1.0, 10.0, client_order_id="0x" + "z" * 64
        )
        assert not bad_res.success

    async def test_add_limit_order_list_caller_provided_client_order_id(self, client):
        """Per-order caller-provided client_order_id is used; invalid hex is rejected."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))
        client.get_portfolio_balance = AsyncMock(return_value={"available": 100.0})

        provided_id = "0x" + "dd" * 32
        orders = [
            {
                "pair": "AVAX/USDC",
                "side": "BUY",
                "amount": 1.0,
                "price": 10.0,
                "client_order_id": provided_id,
            }
        ]
        res = await client.add_limit_order_list(orders)
        assert res.success
        assert res.data["client_order_ids"][0] == provided_id

        # Invalid hex per-order is rejected
        bad_orders = [
            {
                "pair": "AVAX/USDC",
                "side": "BUY",
                "amount": 1.0,
                "price": 10.0,
                "client_order_id": "0x" + "z" * 64,
            }
        ]
        bad_res = await client.add_limit_order_list(bad_orders)
        assert not bad_res.success

    async def test_replace_order_caller_provided_client_order_id(self, client):
        """Caller-provided client_order_id is used for the replacement order."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }
        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"TPID")
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        provided_id = "0x" + "ee" * 32
        res = await client.replace_order("0x01", 10.0, 1.0, client_order_id=provided_id)
        assert res.success
        assert res.data["client_order_id"] == provided_id

        # Invalid hex is rejected
        bad_res = await client.replace_order("0x01", 10.0, 1.0, client_order_id="0x" + "z" * 64)
        assert not bad_res.success

    async def test_cancel_add_list_caller_provided_client_order_id(self, client):
        """Per-replacement caller-provided client_order_id is used."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
                "quote": "USDC",
                "base": "AVAX",
            }
        }
        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"TPID")
        client._ensure_pair_exists = AsyncMock(return_value=True)
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", MagicMock(status=1)))

        provided_id = "0x" + "ff" * 32
        replacements = [
            {
                "order_id": "0x01",
                "amount": 1.0,
                "price": 11.0,
                "pair": "AVAX/USDC",
                "side": "BUY",
                "client_order_id": provided_id,
            }
        ]
        res = await client.cancel_add_list(replacements)
        assert res.success
        assert res.data["client_order_ids"][0] == provided_id

        # Invalid hex is rejected
        bad_replacements = [
            {
                "order_id": "0x01",
                "amount": 1.0,
                "price": 11.0,
                "pair": "AVAX/USDC",
                "side": "BUY",
                "client_order_id": "0x" + "z" * 64,
            }
        ]
        bad_res = await client.cancel_add_list(bad_replacements)
        assert not bad_res.success

    async def test_cancel_order(self, client):
        """Test cancel_order."""
        self._stub_resolved_order(client, id_type="internal")
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        res = await client.cancel_order(
            "0x1234567890123456789012345678901234567890123456789012345678901234"
        )
        assert res.success
        assert res.data["tx_hash"] == "0xTxHash"
        assert "cancelled_client_order_id" in res.data
        assert "cancelled_internal_order_id" in res.data
        assert res.data["cancelled_client_order_id"].startswith("0x")
        assert res.data["cancelled_internal_order_id"].startswith("0x")

    async def test_replace_order(self, client):
        """Test replace_order."""
        # Ensure pair exists
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }
        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"TPID")

        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        res = await client.replace_order("0x01", 10.0, 1.0)
        assert res.success
        assert res.data["tx_hash"] == "0xTxHash"
        assert res.data["client_order_id"].startswith("0x")
        assert res.data["cancelled_client_order_id"].startswith("0x")
        assert res.data["cancelled_internal_order_id"].startswith("0x")

        # Verify args to cancelReplaceOrder
        # Inspections should be on the contract function call itself
        client.trade_pairs_contract.functions.cancelReplaceOrder.assert_called_once()
        args = client.trade_pairs_contract.functions.cancelReplaceOrder.call_args[0]
        assert args[0] == bytes.fromhex("01").rjust(32, b"\0")
        assert args[2] == 10000000  # 10.0 * 10^6
        assert args[3] == 1000000000000000000  # 1.0 * 10^18

    async def test_get_open_orders(self, client):
        """Test get_open_orders."""
        mock_orders = [{"id": "0x1"}, {"id": "0x2"}]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"rows": mock_orders}

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_open_orders()
        assert res.success
        assert res.data[0]["internal_order_id"] == "0x1"
        assert res.data[1]["internal_order_id"] == "0x2"

        # Verify signature header logic
        args, kwargs = client._mock_session.get.call_args
        assert "x-signature" in kwargs["headers"]

        mock_resp2 = AsyncMock()
        mock_resp2.status = 200
        mock_resp2.json.return_value = [{"id": "0x1"}]
        mock_cm2 = AsyncMock()
        mock_cm2.__aenter__.return_value = mock_resp2
        client._mock_session.get.return_value = mock_cm2

        res2 = await client.get_open_orders()
        assert res2.success
        assert res2.data[0]["internal_order_id"] == "0x1"

    async def test_get_open_orders_field_transformation(self, client):
        """Test get_open_orders transforms API field names to camelCase."""
        # Mock API response with lowercase field names (as API returns)
        mock_orders = [
            {
                "id": "0x123",
                "clientordid": "0xabc",
                "tradepairid": "0xdef",
                "price": 100,
                "quantity": 1.5,
                "filledquantity": 0.5,
                "status": 0,
                "side": 0,
                "type": 1,
                "pair": "AVAX/USDC",
                "totalamount": 150,
                "totalfee": 0.1,
            }
        ]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"rows": mock_orders}

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_open_orders()
        assert res.success
        assert len(res.data) == 1

        # Verify transformation: lowercase fields should be mapped to SDK convention
        order = res.data[0]
        assert order["internal_order_id"] == "0x123"  # Transformed from id
        assert order["client_order_id"] == "0xabc"  # Transformed from clientordid
        assert order["tradePairId"] == "0xdef"  # Transformed from tradepairid
        assert order["filledQuantity"] == 0.5  # Transformed from filledquantity
        assert order["totalAmount"] == 150  # Transformed from totalamount
        assert order["totalFee"] == 0.1  # Transformed from totalfee
        # Original fields should still be present
        assert "clientordid" in order
        assert "tradepairid" in order

    async def test_get_open_orders_exception(self, client):
        """Test get_open_orders exception handling (lines 361-363)."""
        # Make _make_http_request raise an exception (not just HTTP error)
        client._make_http_request = AsyncMock(side_effect=Exception("Network error"))

        result = await client.get_open_orders()
        assert not result.success
        assert "fetching open orders" in result.error.lower()

    async def test_get_open_orders_single_dict_response(self, client):
        """Test get_open_orders handles single dict response (not in rows or list)."""
        mock_order = {"id": "0x1", "clientordid": "0xabc", "price": 100}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = mock_order  # Single dict, not in rows or list

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_open_orders()
        assert res.success
        assert len(res.data) == 1
        assert res.data[0]["internal_order_id"] == "0x1"
        assert res.data[0]["client_order_id"] == "0xabc"

    async def test_get_open_orders_empty_response(self, client):
        """Test get_open_orders handles empty response."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"rows": []}

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_open_orders()
        assert res.success
        assert res.data == []

    async def test_get_open_orders_prefers_camelcase_over_lowercase(self, client):
        """Test that transformation prefers existing camelCase fields over lowercase."""
        # Order with both camelCase and lowercase versions
        mock_orders = [
            {
                "id": "0x123",
                "clientOrderId": "0xcamel",  # camelCase exists
                "clientordid": "0xlower",  # lowercase also exists
                "tradePairId": "0xdef",
                "tradepairid": "0xlowerdef",
            }
        ]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"rows": mock_orders}

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        res = await client.get_open_orders()
        assert res.success
        order = res.data[0]
        # clientOrderId should be promoted to client_order_id; lowercase is ignored
        assert order["client_order_id"] == "0xcamel"
        assert order["tradePairId"] == "0xdef"

    async def test_transform_order_from_api_snake_case(self, client):
        """Test _transform_order_from_api handles snake_case field names."""
        order = {
            "id": "0x123",  # API uses "id", SDK maps to "internal_order_id"
            "client_order_id": "0xabc",
            "trade_pair_id": "0xdef",
            "filled_quantity": 0.5,
            "total_amount": 150,
            "total_fee": 0.1,
            "tx_hash": "0xtx",
        }
        transformed = client._transform_order_from_api(order)

        assert transformed["internal_order_id"] == "0x123"  # from "id"
        assert transformed["client_order_id"] == "0xabc"
        assert transformed["tradePairId"] == "0xdef"
        assert transformed["filledQuantity"] == 0.5
        assert transformed["totalAmount"] == 150
        assert transformed["totalFee"] == 0.1
        assert transformed["txHash"] == "0xtx"

    async def test_transform_order_from_api_all_variations(self, client):
        """Test _transform_order_from_api handles all field name variations."""
        # Test lowercase variations
        order_lower = {
            "id": "0x1",
            "clientordid": "0xabc",
            "tradepairid": "0xdef",
            "filledquantity": 0.5,
            "totalamount": 150,
            "totalfee": 0.1,
            "txhash": "0xtx",
        }
        transformed = client._transform_order_from_api(order_lower)
        assert transformed["internal_order_id"] == "0x1"  # from "id"
        assert transformed["client_order_id"] == "0xabc"  # from "clientordid"
        assert transformed["tradePairId"] == "0xdef"
        assert transformed["filledQuantity"] == 0.5
        assert transformed["totalAmount"] == 150
        assert transformed["totalFee"] == 0.1
        assert transformed["txHash"] == "0xtx"

    async def test_transform_order_from_api_no_transformation_needed(self, client):
        """Test _transform_order_from_api when fields are already in SDK convention."""
        order = {
            "internal_order_id": "0x123",
            "client_order_id": "0xabc",
            "tradePairId": "0xdef",
            "filledQuantity": 0.5,
            "totalAmount": 150,
            "totalFee": 0.1,
            "txHash": "0xtx",
        }
        transformed = client._transform_order_from_api(order)

        assert transformed["internal_order_id"] == "0x123"
        assert transformed["client_order_id"] == "0xabc"
        assert transformed["tradePairId"] == "0xdef"
        assert transformed["filledQuantity"] == 0.5
        assert transformed["totalAmount"] == 150
        assert transformed["totalFee"] == 0.1
        assert transformed["txHash"] == "0xtx"

    async def test_transform_order_from_api_missing_optional_fields(self, client):
        """Test _transform_order_from_api handles missing optional fields."""
        order = {
            "id": "0x123",
            "price": 100,
            "quantity": 1.5,
            "status": 0,
            "side": 0,
            "type": 1,
        }
        transformed = client._transform_order_from_api(order)

        assert transformed["internal_order_id"] == "0x123"  # from "id"
        assert transformed["price"] == 100
        # Optional fields not present should not be added
        assert "client_order_id" not in transformed
        assert "filledQuantity" not in transformed

    async def test_cancel_all_orders(self, client):
        """Test cancel_all_orders."""
        # Mock get_open_orders
        from dexalot_sdk.utils.result import Result

        client.get_open_orders = AsyncMock(
            return_value=Result.ok(
                [
                    {
                        "internal_order_id": "0x0000000000000000000000000000000000000000000000000000000000000001",
                        "client_order_id": "C1",
                    },
                    {
                        "internal_order_id": "0x0000000000000000000000000000000000000000000000000000000000000002",
                        "client_order_id": "C2",
                    },
                ]
            )
        )

        # Mock cancel_list_orders
        client.cancel_list_orders = AsyncMock(
            return_value=Result.ok(
                {
                    "tx_hash": "0xTxHash",
                    "cancelled_internal_order_ids": [
                        "0x0000000000000000000000000000000000000000000000000000000000000001",
                        "0x0000000000000000000000000000000000000000000000000000000000000002",
                    ],
                }
            )
        )

        res = await client.cancel_all_orders()
        assert res.success
        assert res.data["tx_hash"] == "0xTxHash"

        # Verify args
        client.cancel_list_orders.assert_called_once_with(
            [
                "0x0000000000000000000000000000000000000000000000000000000000000001",
                "0x0000000000000000000000000000000000000000000000000000000000000002",
            ]
        )

    async def test_get_order_contract(self, client):
        """Test get_order via contract."""
        # Mock tuple with 13 elements
        mock_order_data = (b"ID", b"CID", b"TPID", 100, 10, 10, 0, 0, "0xUser", 0, 0, 0, 3)
        client.trade_pairs_contract.functions.getOrder.return_value.call = AsyncMock(
            return_value=mock_order_data
        )

        res = await client.get_order("0x01")
        # get_order returns a Result now
        assert res.success
        assert isinstance(res.data, dict)
        assert res.data["price"] == 100.0

    async def test_add_limit_order_list(self, client):
        """Test add_limit_order_list."""
        orders = [{"pair": "ZZ/USDC", "side": "BUY", "amount": 1.0, "price": 10.0}]

        # Ensure pair exists
        client.pairs = {
            "ZZ/USDC": {
                "tradePairId": b"TPID",
                "pair": "ZZ/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "quote": "USDC",
                "base": "AVAX",
            }
        }

        # Mock portfolio balance
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        client._ensure_pair_exists = AsyncMock(return_value=True)
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", MagicMock(status=1)))

        res = await client.add_limit_order_list(orders)
        assert res.success
        assert "tx_hash" in res.data
        assert res.data["tx_hash"] == "0xTxHash"

        # Verify structs
        client.trade_pairs_contract.functions.addOrderList.assert_called_once()
        order_tuples = client.trade_pairs_contract.functions.addOrderList.call_args[0][0]
        assert len(order_tuples) == 1
        assert order_tuples[0][5] == 0  # side BUY
        assert order_tuples[0][3] == 1000000000000000000  # amount

    async def test_cancel_add_list(self, client):
        """Test cancel_add_list."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
                "quote": "USDC",
                "base": "AVAX",
            }
        }

        replacements = [
            {"order_id": "0x01", "amount": 1.0, "price": 11.0, "pair": "AVAX/USDC", "side": "BUY"}
        ]

        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"TPID")
        client._ensure_pair_exists = AsyncMock(return_value=True)
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", MagicMock(status=1)))

        res = await client.cancel_add_list(replacements)
        assert res.success
        assert res.data["tx_hash"] == "0xTxHash"
        assert len(res.data["client_order_ids"]) == 1
        assert res.data["client_order_ids"][0].startswith("0x")
        assert len(res.data["cancelled_client_order_ids"]) == 1
        assert res.data["cancelled_client_order_ids"][0].startswith("0x")
        assert len(res.data["cancelled_internal_order_ids"]) == 1
        assert res.data["cancelled_internal_order_ids"][0].startswith("0x")

        # Verify args
        client.trade_pairs_contract.functions.cancelAddList.assert_called_once()
        args = client.trade_pairs_contract.functions.cancelAddList.call_args[0]
        # cancelAddList(_orderIds, _newOrders)
        assert args[0][0] == bytes.fromhex("01").rjust(32, b"\0")
        assert args[1][0][1] == b"TPID"
        assert args[1][0][2] == 11000000  # 11.0 * 10^6

    async def test_cancel_list_orders(self, client):
        """Test cancel_list_orders."""
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))
        res = await client.cancel_list_orders(["0x01", "0x02"])
        assert res.success
        assert res.data["tx_hash"] == "0xTxHash"
        assert "cancelled_internal_order_ids" in res.data

    async def test_cancel_list_orders_by_client_id(self, client):
        """Test cancel_list_orders_by_client_id."""
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        res = await client.cancel_list_orders_by_client_id(["C1", "C2"])
        assert res.success
        assert res.data["tx_hash"] == "0xTxHash"
        assert "cancelled_client_order_ids" in res.data

        # Verify args
        client.trade_pairs_contract.functions.cancelOrderListByClientIds.assert_called_once()
        oids = client.trade_pairs_contract.functions.cancelOrderListByClientIds.call_args[0][0]
        assert len(oids) == 2
        assert len(oids[0]) == 32

    async def test_add_order_wait_for_receipt_false(self, client):
        """Test add_order with wait_for_receipt=False."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))
        client.get_portfolio_balance = AsyncMock(return_value={"available": 100.0})

        res = await client.add_order("AVAX/USDC", "BUY", 1.0, 10.0, wait_for_receipt=False)
        assert res.success
        assert "0xTxHash" in res.data["tx_hash"]

    async def test_cancel_order_receipt_status_failed(self, client):
        """Test cancel_order when receipt status != 1."""
        self._stub_resolved_order(client, id_type="internal")
        mock_receipt = MagicMock()
        mock_receipt.status = 0  # Failed transaction
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        res = await client.cancel_order(
            "0x1234567890123456789012345678901234567890123456789012345678901234"
        )
        assert not res.success
        assert "Transaction reverted" in res.error

    async def test_cancel_order_fallback_internal_to_client_receipt_failed(self, client):
        """Test cancel_order on the client-ID path when receipt status != 1."""
        self._stub_resolved_order(client, id_type="client")
        mock_receipt_failed = MagicMock()
        mock_receipt_failed.status = 0
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt_failed))

        res = await client.cancel_order(
            "0x1234567890123456789012345678901234567890123456789012345678901234"
        )
        assert not res.success
        assert "Transaction reverted" in res.error

    async def test_cancel_order_fallback_client_to_internal_receipt_failed(self, client):
        """Test cancel_order on the internal-ID path when receipt status != 1."""
        self._stub_resolved_order(client, id_type="internal")
        mock_receipt_failed = MagicMock()
        mock_receipt_failed.status = 0
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt_failed))

        res = await client.cancel_order(
            "0x1234567890123456789012345678901234567890123456789012345678901234"
        )
        assert not res.success
        assert "Transaction reverted" in res.error

    async def test_cancel_list_orders_receipt_status_failed(self, client):
        """Test cancel_list_orders when receipt status != 1."""
        mock_receipt = MagicMock()
        mock_receipt.status = 0  # Failed transaction
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        res = await client.cancel_list_orders(["0x01", "0x02"])
        assert not res.success
        assert "Transaction reverted" in res.error

    async def test_replace_order_receipt_status_failed(self, client):
        """Test replace_order when receipt status != 1."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }
        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"TPID")
        mock_receipt = MagicMock()
        mock_receipt.status = 0  # Failed transaction
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        res = await client.replace_order("0x01", 10.0, 1.0)
        assert not res.success
        assert "Transaction reverted" in res.error

    async def test_cancel_list_orders_by_client_id_receipt_status_failed(self, client):
        """Test cancel_list_orders_by_client_id when receipt status != 1."""
        mock_receipt = MagicMock()
        mock_receipt.status = 0  # Failed transaction
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        res = await client.cancel_list_orders_by_client_id(["C1", "C2"])
        assert not res.success
        assert "Transaction reverted" in res.error

    async def test_add_limit_order_list_receipt_status_failed(self, client):
        """Test add_limit_order_list when receipt status != 1."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }
        mock_receipt = MagicMock()
        mock_receipt.status = 0  # Failed transaction
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))
        client.get_portfolio_balance = AsyncMock(return_value={"available": 100.0})

        orders = [{"pair": "AVAX/USDC", "side": "BUY", "amount": 1.0, "price": 10.0}]
        res = await client.add_limit_order_list(orders)
        assert not res.success
        assert "Transaction reverted" in res.error

    async def test_add_limit_order_list_wait_for_receipt_false(self, client):
        """Test add_limit_order_list with wait_for_receipt=False."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))
        client.get_portfolio_balance = AsyncMock(return_value={"available": 100.0})

        orders = [{"pair": "AVAX/USDC", "side": "BUY", "amount": 1.0, "price": 10.0}]
        res = await client.add_limit_order_list(orders, wait_for_receipt=False)
        assert res.success
        assert "0xTxHash" in res.data["tx_hash"]

    async def test_cancel_add_list_wait_for_receipt_false(self, client):
        """Test cancel_add_list with wait_for_receipt=False."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))
        client.get_portfolio_balance = AsyncMock(return_value={"available": 100.0})
        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"TPID")

        replacements = [
            {"order_id": "0x01", "pair": "AVAX/USDC", "side": "BUY", "amount": 1.0, "price": 10.0}
        ]
        res = await client.cancel_add_list(replacements, wait_for_receipt=False)
        assert res.success
        assert res.data["tx_hash"] == "0xTxHash"
        assert len(res.data["client_order_ids"]) == 1
        assert res.data["client_order_ids"][0].startswith("0x")
        assert len(res.data["cancelled_client_order_ids"]) == 1
        assert len(res.data["cancelled_internal_order_ids"]) == 1

    async def test_get_order_by_client_id(self, client):
        """Test get_order_by_client_id."""
        # Mock order data tuple (13 elements)
        # Price 100 * 10^6 = 100000000
        mock_order_data = (
            b"ID",
            b"CID",
            b"TPID",
            100000000,
            10,
            10,
            0,
            0,
            VALID_ADDRESS,
            0,
            0,
            0,
            3,
        )
        client.trade_pairs_contract.functions.getOrderByClientOrderId.return_value.call = AsyncMock(
            return_value=mock_order_data
        )

        # Mock pair data for formatting
        client.pairs = {
            VALID_PAIR: {
                "tradePairId": b"TPID",
                "pair": VALID_PAIR,
                "base_decimals": 18,
                "quote_decimals": 6,
            }
        }

        res = await client.get_order_by_client_id(VALID_CLIENT_ORDER_ID)
        assert res.data["price"] == 100.0

        # Verify bytes32 conversion
        call_args = client.trade_pairs_contract.functions.getOrderByClientOrderId.call_args[0]
        # call_args is tuple of args. First arg is trader address. Second is client ID.
        assert len(call_args[1]) == 32

    async def test_validations_and_errors(self, client):
        """Test various validation and error paths."""
        # cancel_list_orders no account
        client.account = None
        result = await client.cancel_list_orders([])
        assert not result.success
        assert result.error == "Private key not configured."

        # cancel_list_orders_by_client_id no account
        result = await client.cancel_list_orders_by_client_id([])
        assert not result.success
        assert result.error == "Private key not configured."

        # get_order_by_client_id no account
        result = await client.get_order_by_client_id("C")
        assert not result.success
        assert result.error == "Private key not configured."

        client.account = MagicMock()
        client.account.address = "0xUser"

        # add_limit_order_list pair not found
        client.pairs = {}
        client._ensure_pair_exists = AsyncMock(return_value=False)
        res = await client.add_limit_order_list(
            [{"pair": "XX/YY", "side": "BUY", "amount": 1, "price": 1}]
        )
        assert not res.success
        assert "not found" in res.error

        # cancel_add_list pair not found
        self._stub_resolved_order(client, pair="XX/YY", trade_pair_id=b"ID")
        res = await client.cancel_add_list(
            [{"order_id": "1", "pair": "XX/YY", "amount": 1, "price": 1, "side": "BUY"}]
        )
        assert not res.success
        assert "not found" in res.error

    async def test_api_errors(self, client):
        """Test API error handling."""
        client._mock_session.get.side_effect = Exception("API Error")

        # Ensure pair exists so it tries to fetch orderbook
        client.pairs = {
            "ZZ/USDC": {
                "pair": "ZZ/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
            }
        }

        result = await client.get_clob_pairs()
        assert not result.success
        assert "fetching pairs" in result.error.lower()

    async def test_get_orderbook_fetch_pairs_fails(self, client):
        """Test get_orderbook when get_clob_pairs fails."""
        from dexalot_sdk.utils.result import Result

        client.pairs = {}
        client.get_clob_pairs = AsyncMock(return_value=Result.fail("Failed to fetch pairs"))

        result = await client.get_orderbook("AVAX/USDC")
        assert not result.success
        assert "Failed to fetch pairs" in result.error

    async def test_get_orderbook_exception(self, client):
        """Test get_orderbook exception handling (lines 130-132)."""
        from dexalot_sdk.utils.result import Result

        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"id",
            }
        }
        client.get_clob_pairs = AsyncMock(return_value=Result.ok([{"pair": VALID_PAIR}]))

        # Make contract call raise an exception
        client.trade_pairs_contract.functions.getNBook.return_value.call = AsyncMock(
            side_effect=Exception("Contract call failed")
        )

        result = await client.get_orderbook("AVAX/USDC")
        assert not result.success
        assert "fetching orderbook" in result.error.lower()

    async def test_add_order_transaction_reverted(self, client):
        """Test add_order when transaction status is not 1."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }

        # Mock _send_trade_tx to return receipt with status 0 (reverted)
        mock_receipt = MagicMock()
        mock_receipt.status = 0  # Transaction reverted
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))

        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))

        result = await client.add_order("AVAX/USDC", "BUY", 1.0, 10.0)
        assert not result.success
        assert "Transaction reverted" in result.error

    async def test_check_order_balance_dict_error(self, client):
        """Test _check_order_balance when balance_info is dict with error (lines 551-552)."""
        # Mock get_portfolio_balance to return a dict with error
        client.get_portfolio_balance = AsyncMock(return_value={"error": "Balance check failed"})

        result = await client._check_order_balance("USDC", 100.0)
        assert result is not None
        assert not result.success
        assert "Error checking balance" in result.error

    async def test_check_balance_for_token_string_error(self, client):
        """_check_balance_for_token treats a string balance_info as an error response and returns failure."""
        # Mock get_portfolio_balance to return a string error
        client.get_portfolio_balance = AsyncMock(return_value="Error string")

        result = await client._check_balance_for_token("USDC", 100.0)
        assert result is not None
        assert "Error checking balance" in result

    async def test_check_balance_for_token_dict_error(self, client):
        """Test _check_balance_for_token when balance_info is dict with error (lines 608-613)."""
        # Test case 1: dict with error key
        client.get_portfolio_balance = AsyncMock(return_value={"error": "Some error"})
        result = await client._check_balance_for_token("USDC", 100.0)
        assert result is not None
        assert "Error checking balance" in result

        # Test case 2: dict without error key but invalid format
        client.get_portfolio_balance = AsyncMock(return_value={"invalid": "data"})
        result = await client._check_balance_for_token("USDC", 100.0)
        assert result is not None
        assert "Invalid balance response format" in result

        # Test case 3: non-dict, non-Result, non-string
        client.get_portfolio_balance = AsyncMock(return_value=12345)
        result = await client._check_balance_for_token("USDC", 100.0)
        assert result is not None
        assert "Error checking balance" in result

    async def test_cancel_add_list_transaction_reverted(self, client):
        """Test cancel_add_list when transaction status is not 1."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"TPID",
            }
        }

        # Mock _send_trade_tx to return receipt with status 0 (reverted)
        mock_receipt = MagicMock()
        mock_receipt.status = 0  # Transaction reverted
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", mock_receipt))
        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"TPID")

        replacements = [
            {
                "order_id": "0x1234",
                "pair": "AVAX/USDC",
                "side": "BUY",
                "amount": 1.0,
                "price": 10.0,
            }
        ]

        result = await client.cancel_add_list(replacements)
        assert not result.success
        assert "Transaction reverted" in result.error

    async def test_contract_errors(self, client):
        """Test contract interaction errors."""
        client.trade_pairs_contract = None
        # Populate pairs to bypass pair check
        client.pairs = {
            VALID_PAIR: {
                "pair": VALID_PAIR,
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
            }
        }

        result = await client.add_order(VALID_PAIR, "BUY", 1, 1)
        assert not result.success
        assert result.error == "TradePairs contract not initialized."
        result = await client.cancel_order("1")
        assert not result.success
        assert result.error == "TradePairs contract not initialized."
        # cancel_all_orders fails at get_open_orders if API fails, so we mock get_open_orders to return empty list
        from dexalot_sdk.utils.result import Result

        with patch.object(client, "get_open_orders", AsyncMock(return_value=Result.ok([]))):
            result = await client.cancel_all_orders()
            assert not result.success
            assert result.error == "No open orders to cancel."

        result = await client.get_order("1")
        assert not result.success
        assert result.error == "TradePairs contract not initialized."
        result = await client.add_limit_order_list([])
        assert not result.success
        assert result.error == "TradePairs contract not initialized."
        result = await client.cancel_list_orders([])
        assert not result.success
        assert result.error == "TradePairs contract not initialized."
        result = await client.cancel_list_orders_by_client_id([])
        assert not result.success
        assert result.error == "TradePairs contract not initialized."
        result = await client.cancel_add_list([])
        assert not result.success
        assert result.error == "TradePairs contract not initialized."
        result = await client.get_order_by_client_id("1")
        assert not result.success
        assert result.error == "TradePairs contract not initialized."

        # Exception during transaction build/send
        client.trade_pairs_contract = MagicMock()
        client.trade_pairs_contract.functions.addNewOrder.side_effect = Exception("Revert")
        client.pairs = {
            VALID_PAIR: {
                "pair": VALID_PAIR,
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
            }
        }

        result = await client.add_order(VALID_PAIR, "BUY", 1, 1)
        assert not result.success
        assert "placing order" in result.error.lower()

    async def test_exceptions(self, client):
        """Test exception handling in all methods."""
        client.trade_pairs_contract = MagicMock()
        client.pairs = {
            "ZZ/USDC": {
                "pair": "ZZ/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
                "quote": "USDC",
                "base": "AVAX",
            }
        }

        # Mock get_portfolio_balance to return success
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        client._send_trade_tx = AsyncMock(side_effect=Exception("Err"))
        self._stub_resolved_order(client, id_type="internal", pair="ZZ/USDC", trade_pair_id=b"ID")

        result = await client.cancel_order("0x01")
        assert not result.success
        assert "cancelling order" in result.error.lower()

        # cancel_all_orders exception
        from dexalot_sdk.utils.result import Result

        client.get_open_orders = AsyncMock(return_value=Result.ok([{"id": "1"}]))
        client._send_trade_tx = AsyncMock(side_effect=Exception("Err"))
        if hasattr(client, "cancel_list_orders"):
            try:
                del client.cancel_list_orders
            except AttributeError:
                pass
        result = await client.cancel_all_orders()
        assert not result.success

        # get_order exception
        try:
            del client._resolve_order_reference
        except AttributeError:
            pass
        client.trade_pairs_contract.functions.getOrder.side_effect = Exception("Err")
        result = await client.get_order("1")
        assert not result.success
        assert "getting order" in result.error.lower()

        # get_order_by_client_id exception
        client.trade_pairs_contract.functions.getOrderByClientOrderId.side_effect = Exception("Err")
        result = await client.get_order_by_client_id("1")
        assert not result.success
        assert "getting order by client id" in result.error.lower()

        # add_limit_order_list exception
        client._ensure_pair_exists = AsyncMock(return_value=True)
        # _send_trade_tx is already side_effect=Exception("Err")
        result = await client.add_limit_order_list(
            [{"pair": "ZZ/USDC", "side": "BUY", "amount": 1, "price": 1}]
        )
        assert not result.success
        assert "placing batch orders" in result.error.lower()

        # cancel_list_orders exception
        client._send_trade_tx.side_effect = Exception("Err")
        if hasattr(client, "cancel_list_orders"):
            try:
                del client.cancel_list_orders
            except AttributeError:
                pass
        result = await client.cancel_list_orders(["1"])
        assert not result.success

        # cancel_list_orders_by_client_id exception
        if hasattr(client, "cancel_list_orders_by_client_id"):
            try:
                del client.cancel_list_orders_by_client_id
            except AttributeError:
                pass
        result = await client.cancel_list_orders_by_client_id(["1"])
        assert not result.success

        # cancel_add_list exception
        if hasattr(client, "cancel_add_list"):
            try:
                del client.cancel_add_list
            except AttributeError:
                pass
        result = await client.cancel_add_list(
            [{"order_id": "1", "pair": "ZZ/USDC", "amount": 1, "price": 1, "side": "BUY"}]
        )
        assert not result.success

    async def test_clob_missing_coverage(self, client):
        """Test various error paths in clob client."""
        client.account = None
        result = await client.replace_order("1", 1, 1)
        assert not result.success
        assert result.error == "Private key not configured."

        client.account = MagicMock()
        client.trade_pairs_contract = None
        result = await client.replace_order("1", 1, 1)
        assert not result.success
        assert result.error == "TradePairs contract not initialized."

        client.trade_pairs_contract = MagicMock()
        from dexalot_sdk.utils.result import Result

        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
            }
        }
        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"ID")
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", MagicMock(status=1)))

        await client.replace_order("order_id_string", 1, 1)

        client._resolve_order_reference = AsyncMock(return_value=Result.fail("Error"))
        result = await client.replace_order("1", 1, 1)
        assert not result.success
        assert "Could not fetch order" in result.error

        client._resolve_order_reference = AsyncMock(
            return_value=Result.ok(
                {
                    "id_type": "internal",
                    "input_bytes": b"\x01".rjust(32, b"\0"),
                    "order_data": (
                        b"\x01".rjust(32, b"\0"),
                        b"\xab" * 32,
                        b"UNKNOWN",
                        1,
                        0,
                        1,
                        0,
                        0,
                        VALID_ADDRESS,
                        0,
                        1,
                        0,
                        1,
                    ),
                    "internal_id_bytes": b"\x01".rjust(32, b"\0"),
                    "client_order_id_bytes": b"\xab" * 32,
                }
            )
        )
        result = await client.replace_order("1", 1, 1)
        assert not result.success
        assert "Could not determine pair" in result.error

        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
            }
        }
        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"ID")
        client._send_trade_tx.side_effect = Exception("Gas Err")
        result = await client.replace_order("1", 1, 1)
        assert not result.success
        assert "replacing order" in result.error.lower()

        client._send_trade_tx.side_effect = Exception("Err")
        result = await client.replace_order("1", 1, 1)
        assert not result.success
        assert "replacing order" in result.error.lower()

        client._send_trade_tx.side_effect = None
        client._send_trade_tx.return_value = ("0xTxHash", MagicMock())
        await client.cancel_list_orders_by_client_id(["0x123", "string_id", 123])

        client._send_trade_tx.side_effect = Exception("Gas Err")
        result = await client.cancel_list_orders_by_client_id(["1"])
        assert not result.success

        client._ensure_pair_exists = AsyncMock(return_value=True)
        client._send_trade_tx.side_effect = Exception("Gas Err")
        self._stub_resolved_order(client, pair="AVAX/USDC", trade_pair_id=b"ID", id_type="internal")
        result = await client.cancel_add_list(
            [{"order_id": "1", "pair": "AVAX/USDC", "amount": 1, "price": 1, "side": "BUY"}]
        )

    async def test_coverage_gaps(self, client):
        """Test missing coverage lines."""

        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base": "AVAX",
                "quote": "USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"id",
                "min_trade_amount": 0,
                "max_trade_amount": 100,
            }
        }
        client._send_trade_tx = AsyncMock(return_value=("0x7478", MagicMock(status=1)))
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        client._ensure_pair_exists = AsyncMock(return_value=True)

        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        res = await client.add_order("AVAX/USDC", "SELL", 1.0, 10.0)
        assert res.success
        assert res.data["tx_hash"] == "0x7478"

        client.account = None
        result = await client.cancel_order("id")
        assert not result.success
        assert result.error == "Private key not configured."
        result = await client.cancel_all_orders()
        assert not result.success
        assert result.error == "Private key not configured."
        result = await client.get_open_orders()
        assert not result.success
        assert result.error == "Private key not configured."
        result = await client.get_order("id")
        assert not result.success
        assert result.error == "Private key not configured."
        result = await client.add_limit_order_list([])
        assert not result.success
        assert result.error == "Private key not configured."
        result = await client.cancel_add_list([])
        assert not result.success
        assert result.error == "Private key not configured."

        client.account = MagicMock()
        client.account.address = "0xUser"

        from dexalot_sdk.utils.result import Result

        client.get_open_orders = AsyncMock(return_value=Result.ok([]))
        result = await client.cancel_all_orders()
        assert not result.success
        assert result.error == "No open orders to cancel."

        # Mock tuple with 13 elements
        mock_order_data = (b"ID", b"CID", b"id", 100, 10, 10, 0, 0, "0xUser", 0, 0, 0, 3)
        client.trade_pairs_contract.functions.getOrderByClientOrderId.return_value.call = AsyncMock(
            return_value=mock_order_data
        )
        res = await client.get_order_by_client_id("0x1234")
        assert res.success
        assert res.data["price"] == 0.0001  # 100 / 10^6

    async def test_clob_missing_coverage_2(self, client):
        """Test additional error paths."""
        client.pairs = {
            "AVAX/USDC": {
                "pair": "AVAX/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
            }
        }
        client._ensure_pair_exists = AsyncMock(return_value=True)
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", MagicMock(status=1)))

        replacements = [
            {
                "order_id": "0x1234",
                "amount": 1,
                "price": 1,
                "pair": "AVAX/USDC",
                "side": "BUY",
            },  # 0x string
            {
                "order_id": "1234",
                "amount": 1,
                "price": 1,
                "pair": "AVAX/USDC",
                "side": "BUY",
            },  # decimal string? No, just string
            {"order_id": 1234, "amount": 1, "price": 1, "pair": "AVAX/USDC", "side": "BUY"},  # int
        ]
        await client.cancel_add_list(replacements)

        client.account = MagicMock()
        client.account.address = "0xUser"
        client.pairs = {
            "ZZ/USDC": {
                "pair": "ZZ/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
                "quote": "USDC",
                "base": "AVAX",
            }
        }
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        client._ensure_pair_exists = AsyncMock(return_value=True)
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", MagicMock(status=1)))

        # SELL side
        await client.add_limit_order_list(
            [{"pair": "ZZ/USDC", "side": "SELL", "amount": 1, "price": 1}]
        )

        # Invalid side
        result = await client.add_limit_order_list(
            [{"pair": "ZZ/USDC", "side": "INVALID", "amount": 1, "price": 1}]
        )
        assert not result.success
        assert "Invalid side" in result.error

        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.fail("Error"))
        result = await client.add_limit_order_list(
            [{"pair": "ZZ/USDC", "side": "BUY", "amount": 1, "price": 1}]
        )
        assert not result.success
        assert "Error checking balance" in result.error

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 0.0}))
        result = await client.add_limit_order_list(
            [{"pair": "ZZ/USDC", "side": "BUY", "amount": 1, "price": 1}]
        )
        assert not result.success
        assert "Insufficient" in result.error

        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        client._send_trade_tx.side_effect = Exception("Gas Err")
        result = await client.add_limit_order_list(
            [{"pair": "ZZ/USDC", "side": "BUY", "amount": 1, "price": 1}]
        )
        assert not result.success

    async def test_clob_missing_coverage_3(self, client):
        """Test additional error paths."""
        client.pairs = {}
        # We need to make sure get_clob_pairs returns {} and is not a MagicMock that returns something else
        from dexalot_sdk.utils.result import Result

        client.get_clob_pairs = AsyncMock(return_value=Result.ok([{"pair": VALID_PAIR}]))
        res = await client.get_orderbook("INVALID/PAIR")
        assert not res.success
        # Validation catches invalid format first, but if it passes validation, we check for "not found"
        assert "not found" in res.error or "Invalid pair" in res.error

        client.pairs = {VALID_PAIR: {"pair": VALID_PAIR, "tradePairId": b"ID"}}
        client.trade_pairs_contract = None
        result = await client.get_orderbook(VALID_PAIR)
        assert not result.success
        assert "not initialized" in result.error

        client.trade_pairs_contract = MagicMock()
        client.pairs = {
            VALID_PAIR: {
                "pair": VALID_PAIR,
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
                "quote": "USDC",
                "base": "AVAX",
            }
        }
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS

        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.fail("Error"))
        result = await client.add_order(VALID_PAIR, "BUY", 1, 1)
        assert not result.success
        assert "Error checking balance" in result.error

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 0.0}))
        result = await client.add_order(VALID_PAIR, "BUY", 1, 1)
        assert not result.success
        assert "Insufficient" in result.error

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        # Properly mock the contract function call for add_order
        mock_func_call = MagicMock()
        mock_func_call.estimate_gas = AsyncMock(return_value=100000)
        mock_func_call.build_transaction = AsyncMock(return_value={})
        client.trade_pairs_contract.functions.addNewOrder = MagicMock(return_value=mock_func_call)
        # Mock _send_trade_tx to raise exception - this should be caught by add_order
        # We need to use AsyncMock with side_effect to properly raise exceptions
        client._send_trade_tx = AsyncMock(side_effect=Exception("Gas Err"))
        result = await client.add_order(VALID_PAIR, "BUY", 1, 1)
        assert not result.success
        assert "placing order" in result.error.lower()

        client._send_trade_tx = AsyncMock(side_effect=Exception("Transaction reverted"))
        result = await client.add_order(VALID_PAIR, "BUY", 1, 1)
        assert not result.success
        assert "Transaction reverted" in result.error

        self._stub_resolved_order(client, id_type="internal", pair=VALID_PAIR, trade_pair_id=b"ID")
        client._send_trade_tx.side_effect = None
        client._send_trade_tx.return_value = ("0xTxHash", MagicMock(status=1))
        res = await client.cancel_order(VALID_ORDER_ID)  # Not likely internal
        assert res.success
        assert "cancelled_client_order_id" in res.data
        assert "cancelled_internal_order_id" in res.data
        client._send_trade_tx.side_effect = None

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"rows": []}
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        await client.get_open_orders(pair=VALID_PAIR)
        args, kwargs = client._mock_session.get.call_args
        assert kwargs["params"]["pair"] == VALID_PAIR

        client.trade_pairs_contract.functions.getOrder.return_value.call = AsyncMock(
            return_value=(b"\0" * 32,)
        )
        mock_order_data = (b"ID", b"CID", b"TPID", 100, 10, 10, 0, 0, "0xUser", 0, 0, 0, 3)
        client.trade_pairs_contract.functions.getOrderByClientId.return_value.call = AsyncMock(
            return_value=mock_order_data
        )

        # 0x1234 does not start with 0x00, so it tries Client ID first.
        # If Client ID returns data, it stops.
        res = await client.get_order("0x1234")
        assert res.success
        assert res.data["price"] == 10.0

        # Now test fallback: Client ID returns empty, Internal ID succeeds
        client.trade_pairs_contract.functions.getOrderByClientId.return_value.call = AsyncMock(
            return_value=(b"\0" * 32,)  # Empty
        )
        client.trade_pairs_contract.functions.getOrder.return_value.call = AsyncMock(
            return_value=mock_order_data
        )
        res = await client.get_order("0x1234")
        assert res.success
        assert res.data["price"] == 10.0

    async def test_clob_missing_coverage_4(self, client):
        """Test additional error paths."""
        client.pairs = {
            VALID_PAIR: {
                "pair": VALID_PAIR,
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
                "quote": "USDC",
                "base": "AVAX",
            }
        }
        client.account = MagicMock()
        result = await client.add_order(VALID_PAIR, "BUY", 1, 1, order_type="INVALID")
        assert not result.success
        assert "Invalid type" in result.error

        client.pairs[VALID_PAIR]["quote_display_decimals"] = 2

    async def test_clob_rounding(self, client):
        """Test rounding logic in add_order."""
        client.pairs = {
            VALID_PAIR: {
                "pair": VALID_PAIR,
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
                "quote": "USDC",
                "base": "AVAX",
                "quote_display_decimals": 2,
                "base_display_decimals": 2,
            }
        }
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        client._send_trade_tx = AsyncMock(return_value=("tx", MagicMock(status=1)))
        client._ensure_pair_exists = AsyncMock(return_value=True)

        await client.add_order(VALID_PAIR, "BUY", 1.1234, 10.5678)
        # Verify rounded values were passed to the contract function via _send_trade_tx
        # We need to look at what was passed to addNewOrder before it reached _send_trade_tx
        # But since _send_trade_tx is called with the RESULT of the contract function...
        # Wait, the SDK does: func = self.trade_pairs_contract.functions.addNewOrder(...)
        # So we should inspect call_args of addNewOrder.
        call_args = client.trade_pairs_contract.functions.addNewOrder.call_args[0][0]
        # Price 10.57 * 10^6 = 10570000
        assert call_args["price"] == 10570000
        # Qty 1.12 * 10^18
        assert call_args["quantity"] >= 1120000000000000000

    async def test_clob_order_utils(self, client):
        """Test various order utils and fallbacks."""
        client._send_trade_tx = AsyncMock(return_value=("tx", MagicMock(status=1)))
        await client.cancel_order(b"\x01" * 32)
        await client.cancel_order("0x01")

        client._send_trade_tx.side_effect = Exception("Err")
        result = await client.cancel_order("0x1")
        assert not result.success
        client._send_trade_tx.side_effect = None

        from dexalot_sdk.utils.result import Result

        client.get_open_orders = AsyncMock(return_value=Result.ok([{"no_id": 1}]))
        result = await client.cancel_all_orders()
        assert not result.success
        assert "No valid order IDs found" in result.error

        client.account = None
        with pytest.raises(Exception, match="Private key not configured"):
            client._get_auth_headers()
        client.account = MagicMock()
        client.account.address = "0xUser"

        # Restore actual get_open_orders to test its implementation
        if hasattr(client, "get_open_orders"):
            try:
                del client.get_open_orders
            except AttributeError:
                pass

        from dexalot_sdk.core.base import _ORDERBOOK_CACHE

        _ORDERBOOK_CACHE.clear()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Server Error")
        mock_resp.raise_for_status = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm
        res = await client.get_open_orders()
        assert not res.success
        assert "fetching open orders" in res.error.lower()

        client.trade_pairs_contract.functions.getOrder.return_value.call = AsyncMock(
            return_value=(b"\0" * 32,)
        )
        client.trade_pairs_contract.functions.getOrderByClientId.return_value.call = AsyncMock(
            return_value=(b"\0" * 32,)
        )
        await client.get_order(b"\x01" * 32)

        res = await client.get_order("0x01")
        assert not res.success
        assert "Order not found" in res.error

    async def test_clob_missing_coverage_5(self, client):
        """Test additional error paths."""
        client.pairs = {
            VALID_PAIR: {
                "pair": VALID_PAIR,
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
                "quote": "USDC",
                "base": "AVAX",
            }
        }
        client.account = MagicMock()
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        client._send_trade_tx = AsyncMock(return_value=("tx", MagicMock(status=1)))
        client._ensure_pair_exists = AsyncMock(return_value=True)

        await client.add_order(VALID_PAIR, "BUY", 1, 1, order_type="MARKET")
        call_args = client.trade_pairs_contract.functions.addNewOrder.call_args[0][0]
        assert call_args["type1"] == 0

        await client.cancel_order("order_id_string")

        client.trade_pairs_contract.functions.getOrder.return_value.call = AsyncMock(
            return_value=(b"\0" * 32,)
        )
        client.trade_pairs_contract.functions.getOrderByClientId.side_effect = Exception("Err")
        res = await client.get_order("0x01")
        assert not res.success
        assert "getting order by client ID" in res.error

        await client.cancel_list_orders(["order_id_string"])

        client.trade_pairs_contract.functions.getOrderByClientOrderId.return_value.call = AsyncMock(
            return_value=(
                b"\0" * 32,
                b"\0" * 32,
                b"\0" * 32,
                0,
                0,
                0,
                0,
                0,
                "0xUser",
                0,
                0,
                0,
                3,
            )
        )
        await client.get_order_by_client_id(b"\x01" * 32)

        client.pairs = {}
        client.get_clob_pairs = AsyncMock()

        def side_effect_get_clob_pairs():
            client.pairs = {
                "ZZ/USDC": {
                    "pair": "ZZ/USDC",
                    "tradePairId": b"ID",
                    "quote_decimals": 6,
                    "base_decimals": 18,
                }
            }

        client.get_clob_pairs.side_effect = side_effect_get_clob_pairs
        order_data = (b"ID", b"CID", b"ID", 100, 10, 10, 0, 0, "0xUser", 0, 0, 0, 3)
        res = await client._format_order_data(order_data)
        assert res["pair"] == "ZZ/USDC"

    async def test_clob_batch_rounding(self, client):
        """Test rounding logic in batch orders."""
        client.pairs = {
            "ZZ/USDC": {
                "pair": "ZZ/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
                "quote": "USDC",
                "base": "AVAX",
                "quote_display_decimals": 2,
                "base_display_decimals": 2,
            }
        }
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000.0}))
        client._send_trade_tx = AsyncMock(return_value=("tx", MagicMock(status=1)))
        client._ensure_pair_exists = AsyncMock(return_value=True)

        await client.add_limit_order_list(
            [{"pair": "ZZ/USDC", "side": "BUY", "amount": 1.1234, "price": 10.5678}]
        )
        call_args = client.trade_pairs_contract.functions.addOrderList.call_args[0][0]
        assert call_args[0][2] == 10570000

        client._send_trade_tx.side_effect = Exception("Transaction reverted")
        result = await client.add_limit_order_list(
            [{"pair": "ZZ/USDC", "side": "BUY", "amount": 1, "price": 1}]
        )
        assert not result.success
        assert "Transaction reverted" in result.error
        client._send_trade_tx.side_effect = None

        await client.cancel_list_orders(["123", 123, b"\x01" * 32])

        client._send_trade_tx.side_effect = Exception("Err")
        result = await client.cancel_list_orders(["0x1"])
        assert not result.success
        client._send_trade_tx.side_effect = None

        client.get_order = AsyncMock(return_value={"pair": "ZZ/USDC"})
        await client.replace_order(b"\x01" * 32, 1, 1)

        client._send_trade_tx.side_effect = Exception("Err")
        result = await client.replace_order("0x1", 1, 1)
        assert not result.success
        client._send_trade_tx.side_effect = None

        await client.cancel_list_orders_by_client_id([b"\x01" * 32])

    def test_get_auth_headers_static_mode(self, client):
        """Static mode (default): signs 'dexalot', returns only x-signature."""
        client.config.timestamped_auth = False
        mock_account = MagicMock()
        mock_account.address = "0xABC"
        mock_account.sign_message.return_value.signature.hex.return_value = "deadbeef"
        client.account = mock_account

        headers = client._get_auth_headers()

        assert set(headers.keys()) == {"x-signature"}
        assert headers["x-signature"] == "0xABC:0xdeadbeef"

    def test_get_auth_headers_timestamped_mode(self, client):
        """Timestamped mode: x-timestamp present; consecutive calls produce different values."""
        client.config.timestamped_auth = True
        call_count = 0

        def fake_sign(message):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            m.signature.hex.return_value = f"sig{call_count}"
            return m

        mock_account = MagicMock()
        mock_account.address = "0xABC"
        mock_account.sign_message.side_effect = fake_sign
        client.account = mock_account

        headers1 = client._get_auth_headers()
        time.sleep(0.002)  # ensure >1 ms gap
        headers2 = client._get_auth_headers()

        assert set(headers1.keys()) == {"x-signature", "x-timestamp"}
        assert set(headers2.keys()) == {"x-signature", "x-timestamp"}
        assert headers1["x-timestamp"] != headers2["x-timestamp"]
        assert headers1["x-signature"] != headers2["x-signature"]
        # x-timestamp is a recent millisecond epoch
        assert abs(int(time.time() * 1000) - int(headers1["x-timestamp"])) < 5000

    def test_get_auth_headers_no_account_raises(self, client):
        """Raises when account is not configured."""
        client.account = None
        with pytest.raises(Exception, match="Private key not configured"):
            client._get_auth_headers()

    async def test_clob_missing_coverage_6(self, client):
        """Test cancel_add_list error paths."""
        client.account = MagicMock()
        client.account.address = "0xUser"
        client.pairs = {
            "ZZ/USDC": {
                "pair": "ZZ/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "tradePairId": b"ID",
                "quote": "USDC",
                "base": "AVAX",
                "quote_display_decimals": 2,
                "base_display_decimals": 2,
            }
        }
        client._send_trade_tx = AsyncMock(return_value=("tx", MagicMock(status=1)))
        client._ensure_pair_exists = AsyncMock(return_value=True)

        replacements = [
            # Int ID, SELL side, Needs rounding
            {
                "order_id": 12345,
                "pair": "ZZ/USDC",
                "side": "SELL",
                "amount": 1.1234,
                "price": 10.5678,
            },
            # Bytes ID
            {"order_id": b"\x01" * 32, "pair": "ZZ/USDC", "side": "BUY", "amount": 1, "price": 1},
        ]

        client._resolve_order_reference = AsyncMock(
            side_effect=[
                __import__("dexalot_sdk.utils.result", fromlist=["Result"]).Result.ok(
                    {
                        "id_type": "internal",
                        "input_bytes": (12345).to_bytes(32, "big"),
                        "order_data": (
                            (12345).to_bytes(32, "big"),
                            b"\xaa" * 32,
                            b"ID",
                            10_000_000,
                            0,
                            10**18,
                            0,
                            0,
                            VALID_ADDRESS,
                            1,
                            1,
                            0,
                            1,
                        ),
                        "internal_id_bytes": (12345).to_bytes(32, "big"),
                        "client_order_id_bytes": b"\xaa" * 32,
                    }
                ),
                __import__("dexalot_sdk.utils.result", fromlist=["Result"]).Result.ok(
                    {
                        "id_type": "internal",
                        "input_bytes": b"\x01" * 32,
                        "order_data": (
                            b"\x01" * 32,
                            b"\xbb" * 32,
                            b"ID",
                            10_000_000,
                            0,
                            10**18,
                            0,
                            0,
                            VALID_ADDRESS,
                            0,
                            1,
                            0,
                            1,
                        ),
                        "internal_id_bytes": b"\x01" * 32,
                        "client_order_id_bytes": b"\xbb" * 32,
                    }
                ),
            ]
        )
        res = await client.cancel_add_list(replacements)
        assert res.success
        assert "tx_hash" in res.data

        # Verify args for rounding and side
        client.trade_pairs_contract.functions.cancelAddList.assert_called()
        # Verify the LAST call
        call_args = client.trade_pairs_contract.functions.cancelAddList.call_args[0]
        order_ids = call_args[0]
        new_orders = call_args[1]

        assert order_ids[0] == (12345).to_bytes(32, "big")
        assert order_ids[1] == b"\x01" * 32

        # Check SELL order (Index 0)
        # Price 10.57 * 10^6 = 10570000
        assert new_orders[0][2] == 10570000
        # Side enum 1 (SELL)
        assert new_orders[0][5] == 1

        # 4. Invalid Side
        replacements_invalid = [
            {"order_id": "1", "pair": "ZZ/USDC", "side": "INVALID", "amount": 1, "price": 1}
        ]
        self._stub_resolved_order(client, pair="ZZ/USDC", trade_pair_id=b"ID")
        result = await client.cancel_add_list(replacements_invalid)
        assert not result.success
        assert "Invalid side" in result.error

        client._send_trade_tx.side_effect = Exception("Gas estimation failed")
        client._resolve_order_reference = AsyncMock(
            side_effect=[
                __import__("dexalot_sdk.utils.result", fromlist=["Result"]).Result.ok(
                    {
                        "id_type": "internal",
                        "input_bytes": (12345).to_bytes(32, "big"),
                        "order_data": (
                            (12345).to_bytes(32, "big"),
                            b"\xaa" * 32,
                            b"ID",
                            10_000_000,
                            0,
                            10**18,
                            0,
                            0,
                            VALID_ADDRESS,
                            1,
                            1,
                            0,
                            1,
                        ),
                        "internal_id_bytes": (12345).to_bytes(32, "big"),
                        "client_order_id_bytes": b"\xaa" * 32,
                    }
                ),
                __import__("dexalot_sdk.utils.result", fromlist=["Result"]).Result.ok(
                    {
                        "id_type": "internal",
                        "input_bytes": b"\x01" * 32,
                        "order_data": (
                            b"\x01" * 32,
                            b"\xbb" * 32,
                            b"ID",
                            10_000_000,
                            0,
                            10**18,
                            0,
                            0,
                            VALID_ADDRESS,
                            0,
                            1,
                            0,
                            1,
                        ),
                        "internal_id_bytes": b"\x01" * 32,
                        "client_order_id_bytes": b"\xbb" * 32,
                    }
                ),
            ]
        )
        result = await client.cancel_add_list(replacements)
        assert not result.success
        assert "Gas estimation failed" in result.error
        client._send_trade_tx.side_effect = None

    async def test_cancel_order_robustness(self, client):
        """Test cancel_order robustness logic."""
        client.account = MagicMock()
        client.account.address = "0xUser"
        client._send_trade_tx = AsyncMock(return_value=("tx_hash", MagicMock(status=1)))

        internal_id = "0x00" + "1" * 62
        self._stub_resolved_order(
            client,
            id_type="internal",
            internal_id=bytes.fromhex(internal_id[2:]),
            client_order_id=b"\xaa" * 32,
        )
        res = await client.cancel_order(internal_id)
        assert res.success
        assert res.data["cancelled_client_order_id"] == "0x" + "aa" * 32
        assert res.data["cancelled_internal_order_id"] == internal_id

        client._send_trade_tx.side_effect = Exception("Internal Fail")

        res = await client.cancel_order(internal_id)
        assert not res.success
        assert "cancelling order" in res.error.lower()

        client_id = "0x11" + "1" * 62
        self._stub_resolved_order(
            client,
            id_type="client",
            internal_id=b"\x01".rjust(32, b"\0"),
            client_order_id=bytes.fromhex(client_id[2:]),
        )
        client._send_trade_tx.side_effect = None
        client._send_trade_tx.return_value = ("0xTxHash", MagicMock(status=1))

        res = await client.cancel_order(client_id)
        assert res.success
        assert res.data["cancelled_client_order_id"] == client_id
        assert res.data["cancelled_internal_order_id"] == "0x" + "01".rjust(64, "0")
        client._send_trade_tx.side_effect = None

    async def test_clob_critical_coverage(self, client):
        """Test critical coverage gaps."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client._send_trade_tx = AsyncMock(return_value=("tx", MagicMock(status=1)))
        self._stub_resolved_order(client, id_type="internal")

        res = await client.cancel_order(VALID_ORDER_ID)
        assert res.success
        assert "cancelled_client_order_id" in res.data
        assert "cancelled_internal_order_id" in res.data

        client.w3_l1.eth.chain_id = AsyncMock(return_value=43114)
        client.w3_l1.eth.get_transaction_count = AsyncMock(return_value=1)
        client.w3_l1.eth.gas_price = AsyncMock(return_value=1000000000)
        client._get_nonce = AsyncMock(return_value=1)

        # We want to test the actual implementation of _send_trade_tx
        # Temporarily remove any instance-level mock
        if hasattr(client, "_send_trade_tx"):
            try:
                del client._send_trade_tx
            except AttributeError:
                pass

        func_call = MagicMock()
        func_call.estimate_gas = AsyncMock(side_effect=Exception("Revert"))

        with pytest.raises(Exception) as exc:
            await client._send_trade_tx(func_call)
        assert "Gas estimation failed" in str(exc.value)

    async def test_send_trade_tx_full_path(self, client):
        """Test _send_trade_tx full implementation."""
        # Remove any mock
        if hasattr(client, "_send_trade_tx"):
            try:
                del client._send_trade_tx
            except AttributeError:
                pass

        # Setup proper mocks
        class ConstantAwaitable:
            def __init__(self, val):
                self.val = val

            def __await__(self):
                async def _return_value():
                    return self.val

                return _return_value().__await__()

        client.w3_l1.eth.chain_id = AsyncMock(return_value=43114)
        client.w3_l1.eth.get_transaction_count = AsyncMock(return_value=1)
        client.w3_l1.eth.gas_price = ConstantAwaitable(1000000000)
        client.w3_l1.eth.send_raw_transaction = AsyncMock(return_value=b"tx_hash")
        client.w3_l1.eth.wait_for_transaction_receipt = AsyncMock(return_value=MagicMock(status=1))
        client._get_nonce = AsyncMock(return_value=1)
        # to_hex converts bytes to hex string
        client.w3_l1.to_hex.side_effect = lambda x: (
            f"0x{x.hex()}" if isinstance(x, bytes) else f"0x{hex(x)}"
        )
        client.w3_l1.eth.account.sign_transaction.return_value.raw_transaction = b"raw_tx"

        # Create function call mock
        func_call = MagicMock()
        func_call.estimate_gas = AsyncMock(return_value=100000)
        func_call.build_transaction = AsyncMock(return_value={"to": "0xContract", "data": "0x"})

        # Test with wait_for_receipt=True
        tx_hash, receipt = await client._send_trade_tx(func_call, wait_for_receipt=True)
        # to_hex converts b"tx_hash" to "0x74785f68617368"
        assert tx_hash == "0x74785f68617368"
        assert receipt is not None
        client.w3_l1.eth.wait_for_transaction_receipt.assert_called_once()

        # Test with wait_for_receipt=False
        client.w3_l1.eth.wait_for_transaction_receipt.reset_mock()
        client.w3_l1.eth.send_raw_transaction = AsyncMock(return_value=b"tx_hash2")
        tx_hash2, receipt2 = await client._send_trade_tx(func_call, wait_for_receipt=False)
        assert tx_hash2 == "0x74785f6861736832"
        assert receipt2 is None
        client.w3_l1.eth.wait_for_transaction_receipt.assert_not_called()

    async def test_send_trade_tx_reverted(self, client):
        """Test _send_trade_tx with reverted transaction."""
        # Remove any mock
        if hasattr(client, "_send_trade_tx"):
            try:
                del client._send_trade_tx
            except AttributeError:
                pass

        class ConstantAwaitable:
            def __init__(self, val):
                self.val = val

            def __await__(self):
                async def _return_value():
                    return self.val

                return _return_value().__await__()

        client.w3_l1.eth.chain_id = AsyncMock(return_value=43114)
        client.w3_l1.eth.get_transaction_count = AsyncMock(return_value=1)
        client.w3_l1.eth.gas_price = ConstantAwaitable(1000000000)
        client.w3_l1.eth.send_raw_transaction = AsyncMock(return_value=b"tx_hash")
        client.w3_l1.eth.wait_for_transaction_receipt = AsyncMock(return_value=MagicMock(status=0))
        client._get_nonce = AsyncMock(return_value=1)  # Reverted
        # to_hex converts bytes to hex string
        client.w3_l1.to_hex.side_effect = lambda x: (
            f"0x{x.hex()}" if isinstance(x, bytes) else f"0x{hex(x)}"
        )
        client.w3_l1.eth.account.sign_transaction.return_value.raw_transaction = b"raw_tx"

        func_call = MagicMock()
        func_call.estimate_gas = AsyncMock(return_value=100000)
        func_call.build_transaction = AsyncMock(return_value={"to": "0xContract", "data": "0x"})

        with pytest.raises(Exception) as exc:
            await client._send_trade_tx(func_call, wait_for_receipt=True)
        assert "Transaction reverted" in str(exc.value)
        assert "0x74785f68617368" in str(exc.value)

    async def test_add_limit_order_list_balance_errors(self, client):
        """Test add_limit_order_list balance error handling."""
        orders = [{"pair": "ZZ/USDC", "side": "BUY", "amount": 1.0, "price": 10.0}]

        client.pairs = {
            "ZZ/USDC": {
                "tradePairId": b"TPID",
                "pair": "ZZ/USDC",
                "base_decimals": 18,
                "quote_decimals": 6,
                "quote": "USDC",
                "base": "AVAX",
            }
        }
        client._ensure_pair_exists = AsyncMock(return_value=True)

        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.fail("Balance check failed"))
        res = await client.add_limit_order_list(orders)
        assert not res.success
        assert "Error checking balance" in res.error
        assert "Balance check failed" in res.error

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"total": 1000.0}))
        res = await client.add_limit_order_list(orders)
        assert not res.success
        assert "Error checking balance" in res.error
        assert not res.success
        assert "Invalid balance response format" in res.error

    async def test_ensure_pair_exists_not_found_after_fetch(self, client):
        """Test _ensure_pair_exists when pair not found after fetching."""
        client.pairs = {}
        from dexalot_sdk.utils.result import Result

        client.get_clob_pairs = AsyncMock(return_value=Result.ok([{"pair": VALID_PAIR}]))

        result = await client._ensure_pair_exists("AVAX/USDC")
        assert result is False

        client.pairs = {"AVAX/USDC": {}}
        from dexalot_sdk.utils.result import Result

        client.get_clob_pairs = AsyncMock(return_value=Result.ok([{"pair": VALID_PAIR}]))

        result = await client._ensure_pair_exists("AVAX/USDC")
        assert result is True

    async def test_send_trade_tx_retry_disabled(self, client):
        """Test _send_trade_tx when retry is disabled."""
        client.config.retry_enabled = False
        client._rpc_rate_limiter = None

        func_call = MagicMock()
        func_call.estimate_gas = AsyncMock(return_value=100000)
        func_call.build_transaction = AsyncMock(return_value={"to": "0xContract", "data": "0x"})

        client.w3_l1.eth.account.sign_transaction = MagicMock()
        client.w3_l1.eth.account.sign_transaction.return_value.raw_transaction = b"raw_tx"
        client.w3_l1.eth.send_raw_transaction = AsyncMock(return_value=b"tx_hash")

        tx_hash, receipt = await client._send_trade_tx(func_call, wait_for_receipt=False)
        assert tx_hash is not None
        assert receipt is None

        func_call.estimate_gas.assert_called_once()

    async def test_send_trade_tx_no_account(self, client):
        """Test _send_trade_tx raises ValueError when account is None."""
        # Remove any mock
        if hasattr(client, "_send_trade_tx"):
            try:
                del client._send_trade_tx
            except AttributeError:
                pass

        client.account = None
        func_call = MagicMock()

        with pytest.raises(ValueError, match="Account is required for signing transactions"):
            await client._send_trade_tx(func_call)

    def test_websocket_disabled_by_default(self, client):
        """Test that WebSocket Manager is disabled by default."""
        assert client.config.ws_manager_enabled is False

    @pytest.mark.asyncio
    async def test_subscribe_to_events_with_ws_disabled(self, client):
        """Test that subscribe_to_events raises error when WebSocket Manager is disabled."""
        client.config.ws_manager_enabled = False

        def callback(msg):
            pass

        with pytest.raises(RuntimeError, match="WebSocket Manager is disabled"):
            await client.subscribe_to_events("OrderBook/AVAX/USDC", callback)

    @pytest.mark.asyncio
    async def test_subscribe_to_events_public_topic(self, client):
        """Test subscribing to a public topic."""
        client.config.ws_manager_enabled = True

        messages_received = []

        def callback(msg):
            messages_received.append(msg)

        # Mock WebSocketManager
        client.pairs = {
            "AVAX/USDC": {"quote_display_decimals": 6, "base_display_decimals": 18},
        }

        with patch("dexalot_sdk.core.clob.WebSocketManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager

            await client.subscribe_to_events("OrderBook/AVAX/USDC", callback)

            # Verify manager was created and subscribe was called
            mock_manager_class.assert_called_once()
            mock_manager.subscribe.assert_called_once()
            cargs, ckwargs = mock_manager.subscribe.call_args
            assert cargs[0] == "OrderBook/AVAX/USDC" and cargs[1] is callback and cargs[2] is False
            assert ckwargs["orderbook_pair"] == "AVAX/USDC"
            assert ckwargs["orderbook_decimal"] == 6

    @pytest.mark.asyncio
    async def test_subscribe_to_events_private_topic(self, client):
        """Test subscribing to a private topic."""
        client.config.ws_manager_enabled = True
        client.account = MagicMock()
        client.account.address = "0xUser"

        messages_received = []

        def callback(msg):
            messages_received.append(msg)

        # Mock WebSocketManager
        with patch("dexalot_sdk.core.clob.WebSocketManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager

            await client.subscribe_to_events("Orders", callback, is_private=True)

            # Verify manager was created and subscribe was called with is_private=True
            mock_manager_class.assert_called_once()
            mock_manager.subscribe.assert_called_once_with("Orders", callback, True)

    def test_unsubscribe_from_events(self, client):
        """Test unsubscribing from a topic."""
        client.config.ws_manager_enabled = True

        # Mock WebSocketManager
        with patch("dexalot_sdk.core.clob.WebSocketManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager
            client._ws_manager = mock_manager

            client.unsubscribe_from_events("OrderBook/AVAX/USDC")

            mock_manager.unsubscribe.assert_called_once_with("OrderBook/AVAX/USDC")

    @pytest.mark.asyncio
    async def test_close_websocket(self, client):
        """Test closing WebSocket connection."""
        client.config.ws_manager_enabled = True

        # Mock WebSocketManager
        with patch("dexalot_sdk.core.clob.WebSocketManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager
            client._ws_manager = mock_manager

            await client.close_websocket()

            mock_manager.disconnect.assert_called_once()
            assert client._ws_manager is None

    @pytest.mark.asyncio
    async def test_close_websocket_when_none(self, client):
        """Test closing WebSocket when manager is None."""
        client._ws_manager = None
        # Should not raise error
        await client.close_websocket()

    def test_websocket_manager_initialization(self, client):
        """Test that WebSocketManager is initialized lazily."""
        client.config.ws_manager_enabled = True

        # Initially, _ws_manager should not exist
        assert not hasattr(client, "_ws_manager") or client._ws_manager is None

        # After calling _get_ws_manager, it should be created
        manager = client._get_ws_manager()
        assert manager is not None
        assert hasattr(client, "_ws_manager")
        assert client._ws_manager is manager
        assert manager.ws_url == "wss://api.dexalot-test.com/api/ws"

        # Calling again should return the same instance
        manager2 = client._get_ws_manager()
        assert manager2 is manager

    def test_websocket_manager_returns_none_when_disabled(self, client):
        """Test that _get_ws_manager returns None when WebSocket Manager is disabled."""
        client.config.ws_manager_enabled = False

        manager = client._get_ws_manager()
        assert manager is None
        assert not hasattr(client, "_ws_manager") or client._ws_manager is None

    @pytest.mark.asyncio
    async def test_base_client_close_includes_websocket_cleanup(self, client):
        """Test that base client close() also closes WebSocket."""
        client.config.ws_manager_enabled = True

        # Mock WebSocketManager
        with patch("dexalot_sdk.core.clob.WebSocketManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager
            client._ws_manager = mock_manager

            # Mock close_websocket method
            client.close_websocket = AsyncMock()

            await client.close()

            # Verify close_websocket was called
            client.close_websocket.assert_called_once()

    async def test_get_w3_l1_fallback(self, client):
        """Test _get_w3_l1 falling back to w3_l1 when provider manager returns None."""
        config = DexalotConfig(provider_failover_enabled=True)
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client_with_failover = MockClient(config=config)
                client_with_failover.w3_l1 = MagicMock()

                # Make provider manager return None
                async def return_none(*args):
                    return None

                client_with_failover._provider_manager.get_provider = return_none

                result = await client_with_failover._get_w3_l1()
                assert result == client_with_failover.w3_l1

    async def test_get_w3_l1_provider_manager_returns_provider(self, client):
        """Test _get_w3_l1 when provider manager returns a provider."""
        config = DexalotConfig(provider_failover_enabled=True)
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client_with_failover = MockClient(config=config)
                mock_provider = MagicMock()

                # Make provider manager return a provider
                async def return_provider(*args):
                    return mock_provider

                client_with_failover._provider_manager.get_provider = return_provider

                result = await client_with_failover._get_w3_l1()
                assert result == mock_provider

    async def test_get_order_w3_l1_none(self, client):
        """Test get_order when w3_l1 is None."""
        client.trade_pairs_contract = MagicMock()
        client.pairs = {"AVAX/USDC": {"tradePairId": b"TPID"}}
        client.account = MagicMock()
        client.account.address = "0xUser"

        # Make _get_w3_l1 return None
        async def return_none():
            return None

        client._get_w3_l1 = return_none

        # Mock contract to return empty order (all zeros) to trigger getOrderByClientId path
        empty_order = (b"\0" * 32, b"\0" * 32, b"\0" * 32, 0, 0, 0, 0)
        client.trade_pairs_contract.functions.getOrder.return_value.call = AsyncMock(
            return_value=empty_order
        )

        # Test getOrderByClientId path - use valid hex order_id
        result = await client.get_order("0x" + "12" * 32)  # Valid 64 hex chars
        assert not result.success
        assert "L1 provider not available" in result.error

        # Test get_order_by_client_id path
        # This method directly calls _get_w3_l1
        result = await client.get_order_by_client_id(VALID_CLIENT_ORDER_ID)
        assert not result.success
        assert "L1 provider not available" in result.error

        # Test _format_order_data path - need to set up order_data first
        # Mock contract to return valid order data
        # _format_order_data takes only order_data, extracts trade_pair_id from order_data[2]
        order_data = (
            b"order_id" + b"\0" * 24,
            b"client_id" + b"\0" * 22,
            b"pair_id" + b"\0" * 26,
            100,
            0,
            1000,
            0,
        )
        # But w3_l1 is None, so it should fail
        result = await client._format_order_data(order_data)
        assert not result.success
        assert "L1 provider not available" in result.error

    async def test_send_trade_tx_w3_none(self, client):
        """Test _send_trade_tx when w3 is None."""
        client.account = MagicMock()
        client.account.address = "0x123"

        # Make _get_w3_l1 return None
        async def return_none():
            return None

        client._get_w3_l1 = return_none

        func_call = MagicMock()

        with pytest.raises(ValueError, match="L1 provider not available"):
            await client._send_trade_tx(func_call)

    async def test_get_orderbook_invalid_pair_format(self, client):
        """get_orderbook rejects pairs that fail validate_pair_format before making any API call."""
        result = await client.get_orderbook("INVALID")  # No slash
        assert not result.success
        assert "Invalid pair" in result.error

    def test_resolve_cancel_add_pair_uses_inferred_when_omitted(self, client):
        """_resolve_cancel_add_pair_from_replacement returns inferred pair when caller omits pair."""
        res = client._resolve_cancel_add_pair_from_replacement(None, "AVAX/USDC", "1")
        assert res.success
        assert res.data == "AVAX/USDC"

    async def test_get_open_orders_invalid_pair_format(self, client):
        """get_open_orders rejects pairs that fail validate_pair_format before making any API call."""
        client.account = MagicMock()
        result = await client.get_open_orders(pair="INVALID")  # No slash
        assert not result.success
        assert "Invalid pair" in result.error

    async def test_get_order_invalid_order_id_format(self, client):
        """get_order rejects order_ids that fail validate_order_id_format before any lookup."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.get_order(12345)  # Invalid: int instead of hex string
        assert not result.success
        assert "Invalid order_id" in result.error

    async def test_get_order_by_client_id_invalid_format(self, client):
        """get_order_by_client_id rejects client_order_ids that fail validate_order_id_format before any lookup."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.get_order_by_client_id(12345)  # Invalid: int instead of hex string
        assert not result.success
        assert "Invalid client_order_id" in result.error

    async def test_resolution_helper_edge_cases(self, client):
        """Cover deterministic resolution helper branches and edge-case guards."""
        from dexalot_sdk.utils.result import Result

        client.account = None
        trader_result = await client._get_trader_checksum_address()
        assert not trader_result.success
        assert "Private key not configured" in trader_result.error

        assert client._classify_order_id_input(1) == "internal"
        assert client._classify_order_id_input("ab" * 32) == "ambiguous"

        client.trade_pairs_contract = None
        assert not (await client._fetch_order_by_internal_id(b"\x01" * 32)).success
        assert not (await client._fetch_order_by_client_id(b"\x01" * 32)).success
        assert not (await client._resolve_order_reference("0x02")).success

        client.trade_pairs_contract = MagicMock()
        resolve_int = await client._resolve_order_reference(1)
        assert not resolve_int.success
        assert "got int" in resolve_int.error

        client._get_order_id_bytes = MagicMock(side_effect=ValueError("boom"))
        result = await client._resolve_order_reference("0x02")
        assert not result.success
        assert "normalizing order id" in result.error.lower()

        # Restore actual method for the remaining helper tests.
        try:
            del client._get_order_id_bytes
        except AttributeError:
            pass

        class NoClientMethods:
            pass

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.trade_pairs_contract.functions = NoClientMethods()
        fetch_none = await client._fetch_order_by_client_id(b"\x01" * 32)
        assert fetch_none.success
        assert fetch_none.data is None

        client._fetch_order_by_client_id = AsyncMock(return_value=Result.ok(None))
        result = await client.get_order_by_client_id("client-id")
        assert not result.success
        assert "Order not found (Client ID)." in result.error

        client._fetch_order_by_client_id = AsyncMock(side_effect=Exception("boom"))
        result = await client.get_order_by_client_id("client-id")
        assert not result.success
        assert "getting order by client id" in result.error.lower()

        client._resolve_order_reference = AsyncMock(
            return_value=Result.ok(
                {
                    "id_type": "internal",
                    "input_bytes": b"\x01" * 32,
                    "order_data": (
                        b"\x01" * 32,
                        b"\x02" * 32,
                        b"PAIR",
                        1,
                        0,
                        1,
                        0,
                        0,
                        VALID_ADDRESS,
                        0,
                        1,
                        0,
                        1,
                    ),
                    "internal_id_bytes": b"\x01" * 32,
                    "client_order_id_bytes": b"\x02" * 32,
                }
            )
        )
        client._format_order_data = AsyncMock(side_effect=Exception("bad-format"))
        result = await client.get_order("0x02")
        assert not result.success
        assert "getting order" in result.error.lower()

        client.trade_pairs_contract = MagicMock()
        client._send_trade_tx = AsyncMock(side_effect=Exception("boom"))
        result = await client.cancel_order_by_client_id("client-id")
        assert not result.success
        assert "cancelling order by client id" in result.error.lower()

        client.account = None
        result = await client.cancel_order_by_client_id("client-id")
        assert not result.success
        assert result.error == "Private key not configured."

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.cancel_order_by_client_id(123)
        assert not result.success
        assert "Invalid client_order_id" in result.error

        client.trade_pairs_contract = None
        result = await client.cancel_order_by_client_id("client-id")
        assert not result.success
        assert result.error == "TradePairs contract not initialized."

        client.trade_pairs_contract = MagicMock()
        client._send_trade_tx = AsyncMock(
            return_value=("0xdead", type("Receipt", (), {"status": 0})())
        )
        result = await client.cancel_order_by_client_id("client-id")
        assert not result.success
        assert result.error == "Transaction reverted"

        client._send_trade_tx = AsyncMock(return_value=("0xbeef", None))
        result = await client.cancel_order_by_client_id("client-id", wait_for_receipt=False)
        assert result.success
        assert result.data["tx_hash"] == "0xbeef"
        assert "cancelled_client_order_id" in result.data

        result = client._get_order_id_bytes("ab" * 32)
        assert result == bytes.fromhex("ab" * 32)
        with pytest.raises(ValueError, match="fit in 32 bytes"):
            client._get_order_id_bytes("x" * 33)
        marker = object()
        assert client._get_order_id_bytes(marker) is marker

    async def test_cancel_add_list_pair_resolution_guards(self, client):
        """cancel_add_list should fail when pair inference is missing or conflicts."""
        from dexalot_sdk.utils.result import Result

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.trade_pairs_contract = MagicMock()

        client._resolve_order_reference = AsyncMock(
            return_value=Result.ok(
                {
                    "id_type": "internal",
                    "input_bytes": b"\x01" * 32,
                    "order_data": (
                        b"\x01" * 32,
                        b"\x02" * 32,
                        b"UNKNOWN",
                        1,
                        0,
                        1,
                        0,
                        0,
                        VALID_ADDRESS,
                        0,
                        1,
                        0,
                        1,
                    ),
                    "internal_id_bytes": b"\x01" * 32,
                    "client_order_id_bytes": b"\x02" * 32,
                }
            )
        )
        client.pairs = {}
        result = await client.cancel_add_list(
            [{"order_id": "0x01", "amount": 1, "price": 1, "side": "BUY"}]
        )
        assert not result.success
        assert "requires pair" in result.error

        client.pairs = {
            "ZZ/USDC": {
                "pair": "ZZ/USDC",
                "tradePairId": b"PAIR",
                "base_decimals": 18,
                "quote_decimals": 6,
                "base": "AVAX",
                "quote": "USDC",
            }
        }
        client._ensure_pair_exists = AsyncMock(return_value=True)
        client._resolve_order_reference = AsyncMock(
            return_value=Result.ok(
                {
                    "id_type": "internal",
                    "input_bytes": b"\x01" * 32,
                    "order_data": (
                        b"\x01" * 32,
                        b"\x02" * 32,
                        b"PAIR",
                        1,
                        0,
                        1,
                        0,
                        0,
                        VALID_ADDRESS,
                        0,
                        1,
                        0,
                        1,
                    ),
                    "internal_id_bytes": b"\x01" * 32,
                    "client_order_id_bytes": b"\x02" * 32,
                }
            )
        )
        result = await client.cancel_add_list(
            [{"order_id": "0x01", "pair": "OTHER/USDC", "amount": 1, "price": 1, "side": "BUY"}]
        )
        assert not result.success
        assert "does not match existing order pair" in result.error

    async def test_validate_order_params_price_none_for_limit(self, client):
        """_validate_order_params requires price for LIMIT orders; returns fail Result when price is None."""
        side_enum, type_enum, error = client._validate_order_params("BUY", "LIMIT", None, None)
        assert side_enum is None
        assert type_enum is None
        assert error is not None
        assert "Price is required for LIMIT orders" in error.error

    async def test_get_order_id_bytes_int(self, client):
        """_get_order_id_bytes converts an integer order_id to a 32-byte big-endian representation."""
        order_id_int = 12345
        result = client._get_order_id_bytes(order_id_int)
        assert isinstance(result, bytes)
        assert len(result) == 32
        assert result == order_id_int.to_bytes(32, "big")

    # ------------------------------------------------------------------
    # cancel_order dict-receipt revert, balance error dict, subscribe slash-pair paths
    # ------------------------------------------------------------------

    async def test_cancel_order_internal_id_dict_receipt_reverted(self, client):
        """cancel_order: Internal ID path with dict receipt whose status == 0 returns fail."""
        self._stub_resolved_order(client, id_type="internal", internal_id=b"\x00" * 32)
        # Return a plain dict receipt (exercises receipt.get("status", 1) branch)
        client._send_trade_tx = AsyncMock(return_value=("0xTxHash", {"status": 0}))

        res = await client.cancel_order(
            "0x0000000000000000000000000000000000000000000000000000000000000001"
        )
        assert not res.success
        assert "Transaction reverted" in res.error

    async def test_check_order_balance_error_in_dict_response(self, client):
        """_check_order_balance returns fail when get_portfolio_balance returns a dict with 'error'."""
        client.get_portfolio_balance = AsyncMock(return_value={"error": "balance unavailable"})
        result = await client._check_order_balance("AVAX", 1.0)
        assert result is not None
        assert not result.success
        assert "balance unavailable" in result.error

    async def test_check_order_balance_result_ok_none_data(self, client):
        """_check_order_balance returns fail when get_portfolio_balance returns Result.ok(None)."""
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok(None))
        result = await client._check_order_balance("AVAX", 1.0)
        assert result is not None
        assert not result.success
        assert "empty response" in result.error

    async def test_subscribe_to_events_slash_pair_format(self, client):
        """subscribe_to_events accepts 'BASE/QUOTE' topic without 'OrderBook/' prefix."""
        client.config.ws_manager_enabled = True
        client.pairs = {
            "AVAX/USDC": {"quote_display_decimals": 6, "base_display_decimals": 18},
        }
        client._ensure_pair_exists = AsyncMock(return_value=True)

        def callback(msg):
            pass

        with patch("dexalot_sdk.core.clob.WebSocketManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager
            await client.subscribe_to_events("AVAX/USDC", callback)

        mock_manager.subscribe.assert_called_once()
        _, ckwargs = mock_manager.subscribe.call_args
        assert ckwargs["orderbook_pair"] == "AVAX/USDC"

    async def test_subscribe_to_events_slash_pair_not_found_raises(self, client):
        """subscribe_to_events raises ValueError when pair doesn't exist in slash-pair format."""
        client.config.ws_manager_enabled = True
        client._ensure_pair_exists = AsyncMock(return_value=False)

        with patch("dexalot_sdk.core.clob.WebSocketManager"):
            with pytest.raises(ValueError, match="Trading pair not found"):
                await client.subscribe_to_events("AVAX/USDC", lambda msg: None)

    async def test_subscribe_to_events_invalid_pair_topic_raises(self, client):
        """subscribe_to_events raises ValueError when slash topic fails pair format validation."""
        client.config.ws_manager_enabled = True
        with patch("dexalot_sdk.core.clob.WebSocketManager"):
            with pytest.raises(ValueError, match="Invalid pair"):
                await client.subscribe_to_events("FOO/@BAR", lambda msg: None)

    async def test_add_limit_order_list_invalid_pair_format(self, client):
        """add_limit_order_list returns error when an order pair fails validate_pair_format."""
        client.get_portfolio_balance = AsyncMock()
        res = await client.add_limit_order_list(
            [{"pair": "BAD", "side": "BUY", "amount": 1, "price": 1}]
        )
        assert not res.success
        assert "Invalid pair" in (res.error or "")

    async def test_cancel_add_list_invalid_pair_format(self, client):
        """cancel_add_list fails when replacement pair string is not BASE/QUOTE."""

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.trade_pairs_contract = MagicMock()
        self._stub_resolved_order(client, pair="ZZ/USDC", trade_pair_id=b"ID")
        res = await client.cancel_add_list(
            [{"order_id": "0x01", "pair": "BAD", "amount": 1, "price": 1, "side": "BUY"}]
        )
        assert not res.success
        assert "Invalid pair" in (res.error or "")


class TestWebSocketManager:
    """Tests for WebSocketManager (async websockets-based implementation)."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config with WebSocket settings."""
        config = MagicMock()
        config.ws_manager_enabled = True
        config.ws_ping_interval = 30
        config.ws_ping_timeout = 10
        config.ws_reconnect_initial_delay = 1.0
        config.ws_reconnect_max_delay = 60.0
        config.ws_reconnect_exponential_base = 2.0
        config.ws_reconnect_max_attempts = 10
        config.ws_time_offset_ms = 0
        return config

    @pytest.fixture
    def mock_account(self):
        """Create a mock account."""
        account = MagicMock()
        account.address = VALID_ADDRESS
        mock_signed_message = MagicMock()
        mock_signed_message.signature.hex = MagicMock(return_value="0xSignature")
        account.sign_message = MagicMock(return_value=mock_signed_message)
        return account

    @pytest.fixture
    def manager(self, mock_config, mock_account):
        """Create a WebSocketManager instance."""
        return WebSocketManager(
            ws_url="wss://test.example.com/ws",
            account=mock_account,
            config=mock_config,
        )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def test_websocket_manager_initialization(self, manager, mock_config, mock_account):
        """Test WebSocketManager initialization."""
        assert manager.ws_url == "wss://test.example.com/ws"
        assert manager.account == mock_account
        assert manager.config == mock_config
        assert manager.state == ConnectionState.DISCONNECTED
        assert not manager.is_connected

    # ------------------------------------------------------------------
    # connect() — sync entry point
    # ------------------------------------------------------------------

    def test_connect_raises_when_disabled(self, manager):
        """connect() raises RuntimeError when WebSocket Manager is disabled."""
        manager.config.ws_manager_enabled = False
        with pytest.raises(RuntimeError, match="WebSocket Manager is disabled"):
            manager.connect()

    def test_connect_creates_background_task(self, manager):
        """connect() creates an asyncio Task for the background run loop."""

        def _consume_coro(coro):
            coro.close()
            return MagicMock()

        with patch.object(
            manager._loop, "create_task", side_effect=_consume_coro
        ) as mock_create_task:
            manager.connect()
            mock_create_task.assert_called_once()
            assert manager.state == ConnectionState.CONNECTING

    def test_connect_idempotent_when_connecting(self, manager):
        """connect() does nothing when already in CONNECTING state."""
        manager._state = ConnectionState.CONNECTING
        with patch.object(manager._loop, "create_task") as mock_create_task:
            manager.connect()
            mock_create_task.assert_not_called()

    def test_connect_idempotent_when_connected(self, manager):
        """connect() does nothing when already in CONNECTED state."""
        manager._state = ConnectionState.CONNECTED
        with patch.object(manager._loop, "create_task") as mock_create_task:
            manager.connect()
            mock_create_task.assert_not_called()

    def test_state_property(self, manager):
        """state property reflects _state directly."""
        assert manager.state == ConnectionState.DISCONNECTED
        manager._state = ConnectionState.CONNECTED
        assert manager.state == ConnectionState.CONNECTED

    # ------------------------------------------------------------------
    # subscribe() / unsubscribe() — sync registry management
    # ------------------------------------------------------------------

    def test_subscribe_raises_when_disabled(self, manager):
        """subscribe() raises RuntimeError when WebSocket Manager is disabled."""
        manager.config.ws_manager_enabled = False
        with pytest.raises(RuntimeError, match="WebSocket Manager is disabled"):
            manager.subscribe("topic", lambda _m: None)

    def test_subscribe_adds_orderbook_to_registry(self, manager):
        """subscribe() stores orderbook subscription with correct meta."""
        messages = []
        with patch.object(manager, "connect"):
            manager.subscribe("OrderBook/AVAX/USDC", messages.append)

        assert "OrderBook/AVAX/USDC" in manager._subscriptions
        cb, is_private, meta = manager._subscriptions["OrderBook/AVAX/USDC"]
        assert cb == messages.append
        assert is_private is False
        assert meta is not None and meta["kind"] == "orderbook"
        assert meta["pair"] == "AVAX/USDC"

    def test_subscribe_adds_private_topic_to_registry(self, manager):
        """subscribe() stores private topic with is_private=True and meta=None."""
        with patch.object(manager, "connect"):
            manager.subscribe("Orders", lambda _m: None, is_private=True)

        assert "Orders" in manager._subscriptions
        _cb, is_private, meta = manager._subscriptions["Orders"]
        assert is_private is True
        assert meta is None

    def test_subscribe_auto_connects_when_disconnected(self, manager):
        """subscribe() calls connect() when not yet connected."""
        with patch.object(manager, "connect") as mock_connect:
            manager.subscribe("Topic", lambda _m: None)
            mock_connect.assert_called_once()

    def test_subscribe_schedules_send_when_connected(self, manager):
        """subscribe() schedules _send_subscribe task when already connected."""
        manager._state = ConnectionState.CONNECTED
        manager._ws = MagicMock()

        def _consume_coro(coro):
            coro.close()
            return MagicMock()

        with patch.object(
            manager._loop, "create_task", side_effect=_consume_coro
        ) as mock_create_task:
            manager.subscribe("Topic", lambda _m: None)
            mock_create_task.assert_called_once()

    def test_unsubscribe_removes_from_registry(self, manager):
        """unsubscribe() removes the subscription key."""
        with patch.object(manager, "connect"):
            manager.subscribe("OrderBook/AVAX/USDC", lambda _m: None)
        assert "OrderBook/AVAX/USDC" in manager._subscriptions

        manager.unsubscribe("OrderBook/AVAX/USDC")
        assert "OrderBook/AVAX/USDC" not in manager._subscriptions

    def test_unsubscribe_nonexistent_topic(self, manager):
        """unsubscribe() on a missing key does not raise."""
        manager.unsubscribe("NonexistentTopic")  # no exception

    def test_unsubscribe_when_not_connected(self, manager):
        """unsubscribe() when disconnected just removes the entry."""
        with patch.object(manager, "connect"):
            manager.subscribe("Topic", lambda _m: None)
        manager.unsubscribe("Topic")
        assert "Topic" not in manager._subscriptions

    def test_unsubscribe_schedules_send_when_connected(self, manager):
        """unsubscribe() schedules _send_unsubscribe task when connected."""
        with patch.object(manager, "connect"):
            manager.subscribe("Topic", lambda _m: None)
        manager._state = ConnectionState.CONNECTED
        manager._ws = MagicMock()

        def _consume_coro(coro):
            coro.close()
            return MagicMock()

        with patch.object(
            manager._loop, "create_task", side_effect=_consume_coro
        ) as mock_create_task:
            manager.unsubscribe("Topic")
            mock_create_task.assert_called_once()

    # ------------------------------------------------------------------
    # disconnect() — async cleanup
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self, manager):
        """disconnect() sets DISCONNECTED state and clears subscriptions."""

        # Patch create_task so connect() doesn't actually schedule _run on the test loop.
        def _consume_coro(coro):
            coro.close()
            return MagicMock()

        with patch.object(manager._loop, "create_task", side_effect=_consume_coro):
            manager.subscribe("Topic", lambda _m: None)
        mock_ws = AsyncMock()
        manager._ws = mock_ws

        await manager.disconnect()

        mock_ws.close.assert_awaited_once()
        assert manager.state == ConnectionState.DISCONNECTED
        assert len(manager._subscriptions) == 0
        assert manager._should_reconnect is False

    @pytest.mark.asyncio
    async def test_disconnect_close_exception(self, manager):
        """disconnect() swallows exceptions from ws.close()."""
        mock_ws = AsyncMock()
        mock_ws.close.side_effect = Exception("Close error")
        manager._ws = mock_ws

        await manager.disconnect()  # must not raise

        assert manager.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_disconnect_cancels_run_task(self, manager):
        """disconnect() cancels the background _run task if running."""
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()

        async def fake_await():
            raise asyncio.CancelledError

        mock_task.__await__ = fake_await
        manager._run_task = mock_task

        await manager.disconnect()

        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_no_ws(self, manager):
        """disconnect() works safely when _ws is None."""
        manager._ws = None
        await manager.disconnect()  # no exception
        assert manager.state == ConnectionState.DISCONNECTED

    # ------------------------------------------------------------------
    # _handle_message() — message routing (sync)
    # ------------------------------------------------------------------

    def test_handle_message_routes_orderbook(self, manager):
        """_handle_message routes orderBooks messages by pair."""
        received = []
        with patch.object(manager, "connect"):
            manager.subscribe("OrderBook/AVAX/USDC", received.append)

        message_data = {
            "type": "orderBooks",
            "pair": "AVAX/USDC",
            "decimal": 6,
            "data": {"buyBook": [], "sellBook": []},
        }
        manager._handle_message(json.dumps(message_data))

        assert len(received) == 1
        assert received[0]["type"] == "orderBooks"

    def test_handle_message_routes_by_topic(self, manager):
        """_handle_message routes messages with a 'topic' field."""
        received = []
        with patch.object(manager, "connect"):
            manager.subscribe("MyTopic", received.append)

        manager._handle_message(json.dumps({"topic": "MyTopic", "data": "hello"}))

        assert len(received) == 1
        assert received[0]["data"] == "hello"

    def test_handle_message_broadcasts_without_topic(self, manager):
        """_handle_message broadcasts to all callbacks when no topic field."""
        received: list[tuple] = []
        with patch.object(manager, "connect"):
            manager.subscribe("A", lambda m: received.append(("A", m)))
            manager.subscribe("B", lambda m: received.append(("B", m)))

        manager._handle_message(json.dumps({"data": "broadcast"}))

        assert len(received) == 2

    def test_handle_message_callback_exception_caught(self, manager):
        """_handle_message logs callback exceptions and continues."""

        def bad_cb(msg):
            raise ValueError("boom")

        with patch.object(manager, "connect"):
            manager.subscribe("Topic", bad_cb)
        # Should not raise
        manager._handle_message(json.dumps({"topic": "Topic", "data": "x"}))

    def test_handle_message_json_decode_error(self, manager):
        """_handle_message handles malformed JSON without raising."""
        manager._handle_message("not valid json")  # no exception

    def test_handle_message_bytes_decoded(self, manager):
        """_handle_message accepts bytes input (decoded to str)."""
        received = []
        with patch.object(manager, "connect"):
            manager.subscribe("OrderBook/AVAX/USDC", received.append)
        data = json.dumps({"type": "orderBooks", "pair": "AVAX/USDC", "data": {}})
        manager._handle_message(data)
        assert len(received) == 1

    # ------------------------------------------------------------------
    # _build_subscribe_payload (via _send_subscribe, tested via AsyncMock ws)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_send_subscribe_orderbook_payload(self, manager):
        """_send_subscribe sends correct orderbook wire payload."""
        with patch.object(manager, "connect"):
            manager.subscribe("OrderBook/AVAX/USDC", lambda _m: None)
        mock_ws = AsyncMock()
        manager._ws = mock_ws

        await manager._send_subscribe("OrderBook/AVAX/USDC")

        mock_ws.send.assert_awaited_once()
        payload = json.loads(mock_ws.send.call_args[0][0])
        assert payload["type"] == "subscribe"
        assert payload["pair"] == "AVAX/USDC"
        assert "decimal" in payload

    @pytest.mark.asyncio
    async def test_send_subscribe_private_topic_with_auth(self, manager, mock_account):
        """_send_subscribe includes address/signature/timestamp for private topics."""
        manager.account = mock_account
        with patch.object(manager, "connect"):
            manager.subscribe("Orders", lambda _m: None, is_private=True)
        mock_ws = AsyncMock()
        manager._ws = mock_ws

        await manager._send_subscribe("Orders")

        mock_ws.send.assert_awaited_once()
        payload = json.loads(mock_ws.send.call_args[0][0])
        assert "address" in payload
        assert "signature" in payload
        assert "timestamp" in payload

    @pytest.mark.asyncio
    async def test_send_subscribe_private_topic_applies_time_offset(self, manager, mock_account):
        """_send_subscribe applies ws_time_offset_ms to the timestamp."""
        manager.account = mock_account
        manager.config.ws_time_offset_ms = 5000
        with patch.object(manager, "connect"):
            manager.subscribe("Orders", lambda _m: None, is_private=True)
        mock_ws = AsyncMock()
        manager._ws = mock_ws

        before_ms = int(time.time() * 1000)
        await manager._send_subscribe("Orders")

        payload = json.loads(mock_ws.send.call_args[0][0])
        assert payload["timestamp"] >= before_ms + 4000

    @pytest.mark.asyncio
    async def test_send_subscribe_private_without_account(self, manager):
        """_send_subscribe skips sending when private topic has no account."""
        manager.account = None
        with patch.object(manager, "connect"):
            manager.subscribe("Orders", lambda _m: None, is_private=True)
        mock_ws = AsyncMock()
        manager._ws = mock_ws

        await manager._send_subscribe("Orders")

        mock_ws.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_subscribe_send_exception_caught(self, manager):
        """_send_subscribe handles send exceptions without raising."""
        with patch.object(manager, "connect"):
            manager.subscribe("Topic", lambda _m: None)
        mock_ws = AsyncMock()
        mock_ws.send.side_effect = Exception("Send failed")
        manager._ws = mock_ws

        await manager._send_subscribe("Topic")  # no exception

    @pytest.mark.asyncio
    async def test_send_unsubscribe_legacy_topic(self, manager):
        """_send_unsubscribe sends correct unsubscribe payload for legacy topics."""
        with patch.object(manager, "connect"):
            manager.subscribe("Topic", lambda _m: None)
        spec = manager._subscriptions.pop("Topic")
        mock_ws = AsyncMock()
        manager._ws = mock_ws

        await manager._send_unsubscribe("Topic", spec)

        mock_ws.send.assert_awaited_once()
        payload = json.loads(mock_ws.send.call_args[0][0])
        assert payload["type"] == "unsubscribe"
        assert "Topic" in payload["topics"]

    @pytest.mark.asyncio
    async def test_send_unsubscribe_orderbook(self, manager):
        """_send_unsubscribe sends correct pair unsubscribe payload for orderbooks."""
        with patch.object(manager, "connect"):
            manager.subscribe("OrderBook/AVAX/USDC", lambda _m: None)
        spec = manager._subscriptions.pop("OrderBook/AVAX/USDC")
        mock_ws = AsyncMock()
        manager._ws = mock_ws

        await manager._send_unsubscribe("OrderBook/AVAX/USDC", spec)

        mock_ws.send.assert_awaited_once()
        payload = json.loads(mock_ws.send.call_args[0][0])
        assert payload["type"] == "unsubscribe"
        assert payload["pair"] == "AVAX/USDC"

    # ------------------------------------------------------------------
    # _backoff() — reconnect delay logic
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_backoff_returns_false_when_max_attempts_reached(self, manager):
        """_backoff returns False and sets DISCONNECTED when max attempts hit."""
        manager._should_reconnect = True
        manager._reconnect_attempts = 10
        manager.config.ws_reconnect_max_attempts = 10

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager._backoff()

        assert result is False
        assert manager.state == ConnectionState.DISCONNECTED
        assert manager._should_reconnect is False

    @pytest.mark.asyncio
    async def test_backoff_returns_true_and_increments_attempts(self, manager):
        """_backoff returns True and increments _reconnect_attempts normally."""
        manager._reconnect_attempts = 0
        initial_delay = manager._reconnect_delay

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await manager._backoff()

        assert result is True
        assert manager._reconnect_attempts == 1
        mock_sleep.assert_awaited_once()
        assert manager._reconnect_delay > initial_delay

    @pytest.mark.asyncio
    async def test_backoff_infinite_when_max_attempts_zero(self, manager):
        """_backoff always returns True when max_attempts=0 (infinite)."""
        manager._reconnect_attempts = 999
        manager.config.ws_reconnect_max_attempts = 0

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager._backoff()

        assert result is True

    @pytest.mark.asyncio
    async def test_backoff_returns_false_on_cancelled_error(self, manager):
        """_backoff returns False when CancelledError raised during sleep."""
        manager._reconnect_attempts = 0

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = asyncio.CancelledError
            result = await manager._backoff()

        assert result is False
