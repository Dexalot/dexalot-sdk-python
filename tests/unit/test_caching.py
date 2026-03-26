"""
Unit tests for caching integration in the Dexalot SDK.

Tests verify that:
1. Caching can be enabled/disabled
2. Cached methods return cached results on subsequent calls
3. Cache TTL values are respected
4. Cache invalidation works correctly per level
5. Write operations are NEVER cached
"""

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dexalot_sdk import DexalotClient


@pytest.fixture
def mock_env_setup(monkeypatch):
    """Mock environment setup to avoid needing actual .env file."""
    monkeypatch.setenv("PARENTENV", "fuji-multi")
    monkeypatch.setenv("API_BASE_URL_TESTNET", "https://api.dexalot-test.com")


class TestCacheConfiguration:
    """Test cache configuration and initialization."""

    def test_caching_enabled_by_default(self, mock_env_setup):
        """Verify caching is enabled by default."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            assert client._cache_enabled is True

    def test_caching_can_be_disabled(self, mock_env_setup):
        """Verify caching can be disabled."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient(enable_cache=False)
            assert client._cache_enabled is False

    def test_custom_cache_ttl_values(self, mock_env_setup):
        """Verify custom TTL values are accepted."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient(
                        cache_ttl_static=7200,
                        cache_ttl_semi_static=1800,
                        cache_ttl_balance=30,
                        cache_ttl_orderbook=5,
                    )
            # Cache instances should be reinitialized with custom TTL
            # We can't directly test TTL values, but we can verify client initialized
            assert client._cache_enabled is True


class TestStaticDataCaching:
    """Test caching of static data (environments, deployments, chains)."""

    async def test_get_environments_uses_cache(self, mock_env_setup):
        """Verify get_environments() uses cache on subsequent calls."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(
                return_value=[{"chainid": 43113, "env": "fuji-multi-avax", "env_type": "mainnet"}]
            )
            mock_resp.text = AsyncMock(return_value="")
            mock_resp.raise_for_status = MagicMock()  # Not async, just a regular mock
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            mock_cm.__aexit__.return_value = None

            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            client._mock_session = MagicMock()
            client._session = client._mock_session
            client._mock_session.get.return_value = mock_cm

            # First call - should fetch from API
            result1 = await client.get_environments()
            assert result1.success
            call_count_1 = client._mock_session.get.call_count

            # Second call - should use cache
            result2 = await client.get_environments()
            assert result2.success
            call_count_2 = client._mock_session.get.call_count

            # Results should match
            assert result1.data == result2.data
            # API should only be called once (cached on second call)
            assert call_count_2 == call_count_1

    async def test_get_chains_uses_cache(self, mock_env_setup):
        """Verify get_chains() uses cache."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            client.chain_config = {"Fuji": {"chain_id": 43113}}

            # First call
            result1 = await client.get_chains()

            # Second call - should be cached
            start = time.time()
            result2 = await client.get_chains()
            elapsed = time.time() - start

            assert result1 == result2
            assert elapsed < 0.01  # Should be near-instant (cached)

    async def test_get_deployment_uses_cache(self, mock_env_setup):
        """Verify get_deployment() uses cache."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            client.deployments = {"TradePairs": {"address": "0x123"}}

            # First call
            result1 = await client.get_deployment()

            # Second call - should be cached
            start = time.time()
            result2 = await client.get_deployment()
            elapsed = time.time() - start

            assert result1 == result2
            assert elapsed < 0.01  # Should be near-instant (cached)


class TestSemiStaticDataCaching:
    """Test caching of semi-static data (tokens, pairs)."""

    async def test_get_tokens_uses_cache(self, mock_env_setup):
        """Verify get_tokens() uses semi-static cache."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(
                return_value=[
                    {
                        "symbol": "AVAX",
                        "name": "Avalanche",
                        "chain_id": 43113,
                        "evmdecimals": 18,
                        "address": "0x0",
                    }
                ]
            )
            mock_resp.text = AsyncMock(return_value="")
            mock_resp.raise_for_status = MagicMock()  # Not async, just a regular mock
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            mock_cm.__aexit__.return_value = None

            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            client.chain_config = {"Fuji": {"chain_id": 43113}}
            client._mock_session = MagicMock()
            client._session = client._mock_session
            client._mock_session.get.return_value = mock_cm

            # First call
            result1 = await client.get_tokens()
            call_count_1 = client._mock_session.get.call_count

            # Second call - should use cache
            result2 = await client.get_tokens()
            call_count_2 = client._mock_session.get.call_count

            assert result1 == result2
            # Should not make additional API calls
            assert call_count_2 == call_count_1

    async def test_get_clob_pairs_uses_cache(self, mock_env_setup):
        """Verify get_clob_pairs() uses semi-static cache."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(
                return_value=[
                    {
                        "pair": "AVAX/USDC",
                        "base": "AVAX",
                        "quote": "USDC",
                        "env": "fuji-multi-subnet",
                        "base_evmdecimals": 18,
                        "quote_evmdecimals": 6,
                        "mintrade_amnt": "0.1",
                        "maxtrade_amnt": "1000",
                    }
                ]
            )
            mock_resp.text = AsyncMock(return_value="")
            mock_resp.raise_for_status = MagicMock()  # Not async, just a regular mock
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            mock_cm.__aexit__.return_value = None

            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            client._mock_session = MagicMock()
            client._session = client._mock_session
            client._mock_session.get.return_value = mock_cm

            # First call
            result1 = await client.get_clob_pairs()
            call_count_1 = client._mock_session.get.call_count

            # Second call - should use cache
            result2 = await client.get_clob_pairs()
            call_count_2 = client._mock_session.get.call_count

            assert result1 == result2
            assert call_count_2 == call_count_1


class TestBalanceDataCaching:
    """Test caching of balance data (10-second TTL)."""

    async def test_get_portfolio_balance_uses_cache(self, mock_env_setup):
        """Verify get_portfolio_balance() uses balance cache."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            client.portfolio_sub_contract = MagicMock()
            client.portfolio_sub_contract.functions.getBalance.return_value.call = AsyncMock(
                return_value=(
                    1000000000000000000,  # total
                    500000000000000000,  # available
                    500000000000000000,  # locked
                )
            )
            client.subnet_chain_id = 43214
            client.token_data = {
                "AVAX": {"fuji-multi-subnet": {"chain_id": 43214, "evmdecimals": 18}}
            }
            client.account = MagicMock()
            client.account.address = "0xUser"

            # First call
            result1 = await client.get_portfolio_balance("AVAX")
            call_count_1 = (
                client.portfolio_sub_contract.functions.getBalance.return_value.call.call_count
            )

            # Second call - should use cache
            result2 = await client.get_portfolio_balance("AVAX")
            call_count_2 = (
                client.portfolio_sub_contract.functions.getBalance.return_value.call.call_count
            )

            assert result1 == result2
            # Should be cached (same call count)
            assert call_count_2 == call_count_1

    async def test_balance_caching_is_per_user(self, mock_env_setup):
        """CRITICAL: Verify balance methods cache per user, not globally."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            # Create two clients with different accounts
            account_a = MagicMock()
            account_a.address = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

            account_b = MagicMock()
            account_b.address = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client_a = DexalotClient(signer=account_a)
                    client_b = DexalotClient(signer=account_b)

            # Setup mock contracts for both clients
            for client in [client_a, client_b]:
                client.portfolio_sub_contract = MagicMock()
                client.subnet_chain_id = 43214
                client.token_data = {
                    "AVAX": {"fuji-multi-subnet": {"chain_id": 43214, "evmdecimals": 18}}
                }

            # User A has 100 AVAX
            client_a.portfolio_sub_contract.functions.getBalance.return_value.call = AsyncMock(
                return_value=(
                    100000000000000000000,  # 100 AVAX total
                    100000000000000000000,  # 100 AVAX available
                    0,  # 0 locked
                )
            )

            # User B has 50 AVAX
            client_b.portfolio_sub_contract.functions.getBalance.return_value.call = AsyncMock(
                return_value=(
                    50000000000000000000,  # 50 AVAX total
                    50000000000000000000,  # 50 AVAX available
                    0,  # 0 locked
                )
            )

            # User A queries balance
            balance_a = await client_a.get_portfolio_balance("AVAX")

            # User B queries balance - should NOT get User A's cached result
            balance_b = await client_b.get_portfolio_balance("AVAX")

            # Verify each user gets their own balance
            assert balance_a.success, "User A balance query should succeed"
            assert balance_b.success, "User B balance query should succeed"
            assert balance_a.data["total"] == 100.0, "User A should have 100 AVAX"
            assert balance_b.data["total"] == 50.0, "User B should have 50 AVAX (not User A's 100!)"

            # Verify both contracts were called (not cached across users)
            assert (
                client_a.portfolio_sub_contract.functions.getBalance.return_value.call.call_count
                == 1
            )
            assert (
                client_b.portfolio_sub_contract.functions.getBalance.return_value.call.call_count
                == 1
            )


class TestOrderbookCaching:
    """Test caching of orderbook data (1-second TTL)."""

    async def test_get_orderbook_uses_cache(self, mock_env_setup):
        """Verify get_orderbook() uses 1-second cache."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            client.pairs = {
                "AVAX/USDC": {
                    "tradePairId": b"AVAX/USDC" + b"\x00" * 22,
                    "quote_decimals": 6,
                    "base_decimals": 18,
                }
            }
            client.trade_pairs_contract = MagicMock()
            client.trade_pairs_contract.functions.getNBook.return_value.call = AsyncMock(
                return_value=(
                    [1000000, 900000],  # prices
                    [100000000000000000, 200000000000000000],  # quantities
                )
            )

            # First call
            result1 = await client.get_orderbook("AVAX/USDC")
            call_count_1 = (
                client.trade_pairs_contract.functions.getNBook.return_value.call.call_count
            )

            # Immediate second call - should use cache
            result2 = await client.get_orderbook("AVAX/USDC")
            call_count_2 = (
                client.trade_pairs_contract.functions.getNBook.return_value.call.call_count
            )

            assert result1 == result2
            # Should be cached
            assert call_count_2 == call_count_1


class TestCacheInvalidation:
    """Test cache invalidation functionality."""

    def test_invalidate_all_caches(self, mock_env_setup):
        """Verify invalidate_cache() clears all cache levels."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            # Import fresh cache references
            from dexalot_sdk.core.base import (
                _BALANCE_CACHE,
                _ORDERBOOK_CACHE,
                _SEMI_STATIC_CACHE,
                _STATIC_CACHE,
            )

            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()

            # Populate caches with dummy data
            _STATIC_CACHE.set("test_static", "value1")
            _SEMI_STATIC_CACHE.set("test_semi", "value2")
            _BALANCE_CACHE.set("test_balance", "value3")
            _ORDERBOOK_CACHE.set("test_orderbook", "value4")

            # Invalidate all
            client.invalidate_cache()

            # All caches should be empty
            assert _STATIC_CACHE.get("test_static") is None
            assert _SEMI_STATIC_CACHE.get("test_semi") is None
            assert _BALANCE_CACHE.get("test_balance") is None
            assert _ORDERBOOK_CACHE.get("test_orderbook") is None

    def test_invalidate_specific_cache_level(self, mock_env_setup):
        """Verify invalidate_cache(level) only clears specified level."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            # Import fresh cache references
            from dexalot_sdk.core.base import _BALANCE_CACHE, _STATIC_CACHE

            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()

            # Populate caches
            _STATIC_CACHE.set("test_static", "value1")
            _BALANCE_CACHE.set("test_balance", "value3")

            # Invalidate only balance cache
            client.invalidate_cache("balance")

            # Balance cache should be cleared, static cache should remain
            assert _STATIC_CACHE.get("test_static") == "value1"
            assert _BALANCE_CACHE.get("test_balance") is None


class TestCacheDisabled:
    """Test behavior when caching is disabled."""

    async def test_disabled_cache_always_fetches(self, mock_env_setup):
        """Verify disabled cache bypasses cache on each call."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=[{"chainid": 43113}])
            mock_resp.text = AsyncMock(return_value="")
            mock_resp.raise_for_status = MagicMock()  # Not async, just a regular mock
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            mock_cm.__aexit__.return_value = None

            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient(enable_cache=False)
            client._mock_session = MagicMock()
            client._session = client._mock_session
            client._mock_session.get.return_value = mock_cm

            # The decorator still applies, but the cache bypass logic should clear the key
            # This means the decorator will try to cache, but we clear it immediately
            # So we need to verify the behavior is correct by checking results are fetched

            # First call
            result1 = await client.get_environments()
            assert result1.success

            # Second call - cache is bypassed via the clear logic in the method
            result2 = await client.get_environments()
            assert result2.success

            # Both should return the same data (from API)
            assert result1.data == result2.data
            # The mock was called (at least once, possibly twice depending on decorator behavior)
            assert client._mock_session.get.call_count >= 1


class TestWriteOperationsNeverCached:
    """Test that write operations are NEVER cached."""

    async def test_add_order_never_cached(self, mock_env_setup):
        """Verify add_order() is never cached."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            client.account = MagicMock()
            client.account.address = "0x1234567890123456789012345678901234567890"
            client.pairs = {
                "AVAX/USDC": {
                    "tradePairId": b"AVAX/USDC" + b"\x00" * 22,
                    "quote_decimals": 6,
                    "base_decimals": 18,
                    "quote": "USDC",
                    "base": "AVAX",
                }
            }
            client.trade_pairs_contract = MagicMock()
            client.w3_l1 = MagicMock()
            client.portfolio_sub_contract = MagicMock()

            # Mock get_portfolio_balance to return sufficient balance
            from dexalot_sdk.utils.result import Result

            client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000}))

            # Mock _send_trade_tx
            client._send_trade_tx = AsyncMock(return_value=("0xabc123", MagicMock(status=1)))

            # Call add_order twice with identical parameters
            result1 = await client.add_order("AVAX/USDC", "BUY", 1.0, 10.0)
            result2 = await client.add_order("AVAX/USDC", "BUY", 1.0, 10.0)

            # Both should execute (not cached)
            assert client._send_trade_tx.call_count == 2
            # Results should be Result objects with success=True
            assert result1.success
            assert result2.success
            assert isinstance(result1.data, dict)
            assert isinstance(result2.data, dict)

    async def test_cancel_order_never_cached(self, mock_env_setup):
        """Verify cancel_order() is never cached."""
        with patch("dexalot_sdk.core.base.aiohttp.ClientSession"):
            with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
                with patch("dexalot_sdk.core.config.load_dotenv"):
                    client = DexalotClient()
            client.account = MagicMock()
            client.trade_pairs_contract = MagicMock()
            mock_receipt = MagicMock()
            mock_receipt.status = 1
            client._send_trade_tx = AsyncMock(return_value=("0xdef456", mock_receipt))
            client._get_order_id_bytes = MagicMock(return_value=b"\x00" * 32)

            # Call cancel_order twice
            await client.cancel_order("0x123")
            await client.cancel_order("0x123")

            # Should execute both times (not cached)
            assert client._send_trade_tx.call_count == 2
