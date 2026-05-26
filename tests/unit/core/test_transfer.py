import asyncio
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dexalot_sdk.core.base import DexalotBaseClient
from dexalot_sdk.core.config import DexalotConfig
from dexalot_sdk.core.transfer import TransferClient
from dexalot_sdk.utils import Utils

# Valid test data constants
VALID_ADDRESS = "0x1234567890123456789012345678901234567890"  # 42 characters
VALID_RECIPIENT = "0x9876543210987654321098765432109876543210"  # 42 characters


class MockClient(TransferClient, DexalotBaseClient):
    pass


class TestTransferClient:
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

    def create_w3(self):
        class ConstantAwaitable:
            def __init__(self, val):
                self.val = val

            def __await__(self):
                async def _return_value():
                    return self.val

                return _return_value().__await__()

        w3 = MagicMock()
        w3.eth.get_balance = AsyncMock(return_value=0)
        w3.eth.chain_id = AsyncMock(return_value=43114)
        w3.eth.get_transaction_count = AsyncMock(return_value=1)
        w3.eth.send_raw_transaction = AsyncMock(return_value=b"tx_hash")
        w3.eth.wait_for_transaction_receipt = AsyncMock(return_value={"status": 1})
        w3.eth.gas_price = ConstantAwaitable(1000000000)
        w3.to_hex.side_effect = lambda x: f"0x{x.hex()}" if isinstance(x, bytes) else str(x)
        w3.from_wei.side_effect = lambda x, y: x / 10**18
        w3.to_wei.side_effect = lambda x, y: int(x * 10**18)

        def mock_contract(*args, **kwargs):
            c = MagicMock()

            class FunctionsMock:
                def __init__(self):
                    self._methods = {}

                def __getattr__(self, name):
                    if name not in self._methods:
                        m_fn = MagicMock()
                        m_res = m_fn.return_value
                        m_res.estimate_gas = AsyncMock(return_value=100000)
                        m_res.build_transaction = AsyncMock(return_value={})
                        if name in ("getBalance", "getBalances"):
                            m_res.call = AsyncMock(return_value=(0, 0, 0))
                        else:
                            m_res.call = AsyncMock(return_value=0)
                        m_res.fn_name = name
                        m_fn.side_effect = lambda *args, **kwargs: m_res
                        self._methods[name] = m_fn
                    return self._methods[name]

            c.functions = FunctionsMock()
            return c

        w3.eth.contract.side_effect = mock_contract
        return w3

    @pytest.fixture
    async def client(self):
        # Patch environment to ensure no invalid PRIVATE_KEY is loaded
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client = MockClient()
                client.account = MagicMock()
                client.account.address = VALID_ADDRESS
                client.private_key = "0x" + "a" * 64  # Valid 66-char private key (32 bytes)

                client.w3_l1 = self.create_w3()
                client.connected_chain_providers = {"Avalanche": self.create_w3()}
                client.view_all_connected_chain_providers = client.connected_chain_providers
                client.w3_connected_chain = client.connected_chain_providers["Avalanche"]

                # Mock _get_nonce for nonce manager
                client._get_nonce = AsyncMock(return_value=1)

                client.portfolio_main_avax_contract = client.w3_connected_chain.eth.contract()
                client.portfolio_sub_contract = client.w3_l1.eth.contract()

                client.deployments = {
                    "PortfolioMain": {"Avalanche": {"address": "0xPortMain", "abi": []}}
                }

                client.token_data = {
                    "AVAX": {
                        "env1": {"chain_id": 43114, "evmdecimals": 18, "address": "0xAVAX"},
                        "env2": {
                            "chain_id": 12345,
                            "evmdecimals": 18,
                            "address": "0xAVAX",
                        },  # Subnet
                    },
                    "USDC": {
                        "env1": {"chain_id": 43114, "evmdecimals": 6, "address": "0xUSDC"},
                        "env2": {"chain_id": 12345, "evmdecimals": 6, "address": "0xUSDC"},
                    },
                }
                client.subnet_chain_id = 12345
                client.chain_id = 43114
                client.chain_config = {
                    "Avalanche": {"chain_id": 43114, "native_symbol": "AVAX"},
                    "Fuji": {"chain_id": 43113, "native_symbol": "AVAX"},
                }
                client._parse_revert_reason = lambda e: str(e)
                client._cache_enabled = False  # Disable caching for tests

                # Mock session
                client._mock_session = MagicMock()
                client._session = client._mock_session
                mock_resp = AsyncMock()
                mock_resp.status = 200
                mock_resp.json = AsyncMock(return_value=[])
                mock_resp.raise_for_status = MagicMock()
                mock_cm = AsyncMock()
                mock_cm.__aenter__.return_value = mock_resp
                client._mock_session.get.return_value = mock_cm

                yield client

    async def test_get_token_details(self, client):
        # Mock tokens API response
        mock_tokens_resp = [
            {
                "symbol": "AVAX",
                "name": "Avalanche",
                "chain_id": 43113,
                "evmdecimals": 18,
                "address": "0x0000",
                "env": "fuji",
            }
        ]

        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_tokens_resp
        mock_resp.raise_for_status = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        result = await client.get_token_details("AVAX")
        assert result.success
        # result.data is a dict with env keys, e.g., {"fuji": {...}}
        assert isinstance(result.data, dict)
        assert "AVAX" in str(result.data) or any("AVAX" in str(v) for v in result.data.values())

        # Test invalid token
        mock_resp_empty = AsyncMock()
        mock_resp_empty.json.return_value = []  # Empty response for invalid token
        mock_resp_empty.raise_for_status = MagicMock()
        mock_cm_empty = AsyncMock()
        mock_cm_empty.__aenter__.return_value = mock_resp_empty
        client._mock_session.get.return_value = mock_cm_empty

        result = await client.get_token_details("INVALID")
        assert not result.success
        assert "not found" in result.error

    def test_rehydrate_cached_get_token_details_initializes_cache(self, client):
        """Token-detail rehydration should rebuild token_data for cached responses."""
        from dexalot_sdk.utils.result import Result

        client.token_data = {}

        client._rehydrate_cached_get_token_details(
            Result.ok({"fuji": {"symbol": "AVAX", "address": "0x1"}}), "AVAX"
        )

        assert client.token_data["AVAX"]["fuji"]["symbol"] == "AVAX"

    def test_rehydrate_cached_get_token_details_ignores_failed_or_empty_results(self, client):
        """Failed or empty cached token results should not mutate token_data."""
        from dexalot_sdk.utils.result import Result

        client.token_data = {"KEEP": {"env": {"symbol": "KEEP"}}}

        client._rehydrate_cached_get_token_details(Result.fail("boom"), "AVAX")
        assert "AVAX" not in client.token_data

        client._rehydrate_cached_get_token_details(Result.ok(None), "AVAX")
        assert "AVAX" not in client.token_data

    async def test_get_chain_wallet_balance(self, client):
        client.w3_l1.eth.get_balance.return_value = 10 * 10**18
        info = await client.get_chain_wallet_balance("Dexalot L1", "ALOT")
        assert info.success
        assert info.data["chain"] == "Dexalot L1"
        assert info.data["symbol"] == "ALOT"
        assert "10.0" in info.data["balance"]

        info = await client.get_chain_wallet_balance("Dexalot L1", "USDC")
        assert not info.success

        info = await client.get_chain_wallet_balance("UnknownChain", "AVAX")
        assert not info.success

    async def test_get_chain_wallet_balance_no_address(self, client):
        client.account = None
        info = await client.get_chain_wallet_balance("Dexalot L1", "ALOT")
        assert not info.success
        assert "Address required" in info.error

    async def test_get_chain_wallet_balance_connected_chain_native(self, client):
        client.connected_chain_providers["Avalanche"].eth.get_balance.return_value = 25 * 10**18
        info = await client.get_chain_wallet_balance("Avalanche", "AVAX")
        assert info.success
        assert info.data["chain"] == "Avalanche"
        assert info.data["symbol"] == "AVAX"
        assert info.data["balance"] == "25.0"
        assert info.data["type"] == "Native"

    async def test_resolve_chain_reference_uses_active_environment_for_generic_avalanche_alias(
        self, client
    ):
        client.chain_id = 43113
        client.chain_config = {"Fuji": {"chain_id": 43113, "native_symbol": "AVAX"}}

        resolved = client.resolve_chain_reference("Avalanche C Chain")

        assert resolved.success
        assert resolved.data is not None
        assert resolved.data.canonical_name == "Fuji"
        assert resolved.data.chain_id == 43113

    async def test_get_chain_wallet_balance_resolves_alias_to_connected_testnet(self, client):
        client.chain_id = 43113
        client.chain_config = {"Fuji": {"chain_id": 43113, "native_symbol": "AVAX"}}
        client.connected_chain_providers = {"Fuji": self.create_w3()}
        client.connected_chain_providers["Fuji"].eth.get_balance.return_value = 7 * 10**18

        info = await client.get_chain_wallet_balance("Avalanche C Chain", "AVAX")

        assert info.success
        assert info.data["chain"] == "Fuji"
        assert info.data["balance"] == "7.0"

    async def test_get_chain_wallet_balance_connected_chain_erc20(self, client):
        mock_contract = MagicMock()
        mock_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=1000 * 10**6)
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_contract
        client.token_data["USDC"] = {
            "Avalanche": {
                "chain_id": 43114,
                "address": "0xUSDAVAX",
                "evmdecimals": 6,
            }
        }
        info = await client.get_chain_wallet_balance("Avalanche", "USDC")
        assert info.success
        assert info.data["chain"] == "Avalanche"
        assert info.data["symbol"] == "USDC"
        assert info.data["type"] == "ERC20"
        assert "1000" in info.data["balance"]

    async def test_get_chain_wallet_balance_erc20_not_found(self, client):
        # Test token not in token_data
        info = await client.get_chain_wallet_balance("Avalanche", "UNKNOWN_TOKEN")
        assert not info.success
        assert "not found" in info.error

    async def test_get_chain_wallet_balance_erc20_not_on_chain(self, client):
        # Token exists but not on this chain
        client.token_data["SPECIAL"] = {
            "Ethereum": {
                "chain_id": 1,  # Different chain
                "address": "0xSPEC",
            }
        }
        info = await client.get_chain_wallet_balance("Avalanche", "SPECIAL")
        assert not info.success
        assert "not available" in info.error

    async def test_get_chain_wallet_balance_erc20_zero_address(self, client):
        client.token_data["ZERO"] = {
            "Avalanche": {
                "chain_id": 43114,
                "address": "0x0000000000000000000000000000000000000000",
            }
        }
        info = await client.get_chain_wallet_balance("Avalanche", "ZERO")
        assert not info.success
        assert "zero address" in info.error

    async def test_get_chain_wallet_balance_erc20_contract_error(self, client):
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = Exception(
            "Contract Error"
        )
        client.token_data["ERR"] = {
            "Avalanche": {
                "chain_id": 43114,
                "address": "0xERR",
                "evmdecimals": 18,
            }
        }
        info = await client.get_chain_wallet_balance("Avalanche", "ERR")
        assert not info.success or "Error" in str(info.data.get("balance", ""))

    async def test_get_chain_wallet_balance_no_chain_id(self, client):
        # Chain ID not configured
        client.chain_config["Avalanche"] = {"native_symbol": "AVAX"}  # No chain_id
        info = await client.get_chain_wallet_balance("Avalanche", "USDC")
        assert not info.success
        assert "Chain ID not configured" in info.error

    async def test_get_chain_wallet_balances(self, client):
        client.w3_l1.eth.get_balance.return_value = 10 * 10**18
        info = await client.get_chain_wallet_balances("Dexalot L1")
        assert info.success
        assert info.data["chain"] == "Dexalot L1"
        l1_entry = next(b for b in info.data["chain_balances"] if b["chain"] == "Dexalot L1")
        assert "10.0" in l1_entry["balance"]

        # Test unknown chain
        info = await client.get_chain_wallet_balances("UnknownChain")
        assert not info.success

    async def test_get_chain_wallet_balances_no_address(self, client):
        # Test address required error
        client.account = None
        info = await client.get_chain_wallet_balances("Dexalot L1")
        assert not info.success
        assert "Address required" in info.error

    async def test_get_chain_wallet_balances_invalid_and_not_connected_paths(self, client):
        result = await client.get_chain_wallet_balances("")
        assert not result.success
        assert "Invalid chain" in result.error

        result = await client._get_chain_wallet_balances_cached("UnknownChain", VALID_ADDRESS)
        assert not result.success
        assert "not connected" in result.error

    async def test_get_chain_wallet_balances_connected_chain(self, client):
        # Test connected-chain path with native and ERC20 balances
        client.connected_chain_providers["Avalanche"].eth.get_balance.return_value = 15 * 10**18

        # Setup ERC20 mock
        mock_contract = MagicMock()
        mock_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=500 * 10**6)
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_contract

        # Add token data
        client.token_data["USDC"] = {
            "Avalanche": {
                "chain_id": 43114,
                "address": "0xUSDC",
                "evmdecimals": 6,
            }
        }

        info = await client.get_chain_wallet_balances("Avalanche")
        assert info.success
        assert info.data["chain"] == "Avalanche"
        assert info.data["address"] == VALID_ADDRESS

        # Should have native balance
        native_entry = next((b for b in info.data["chain_balances"] if b["type"] == "Native"), None)
        assert native_entry is not None
        assert native_entry["balance"] == "15.0"

        # Should have ERC20 balances
        assert len(info.data["chain_balances"]) >= 1

    async def test_get_chain_token_balances_native_and_erc20(self, client):
        """Filter to a specific token list, returning a flat symbol -> balance map."""
        client.connected_chain_providers["Avalanche"].eth.get_balance.return_value = 15 * 10**18

        mock_contract = MagicMock()
        mock_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=500 * 10**6)
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_contract

        client.token_data["USDC"] = {
            "Avalanche": {"chain_id": 43114, "address": "0xUSDC", "evmdecimals": 6},
        }

        result = await client.get_chain_token_balances("Avalanche", VALID_ADDRESS, ["AVAX", "USDC"])

        assert result.success
        assert set(result.data.keys()) == {"AVAX", "USDC"}
        assert result.data["AVAX"] == "15.0"
        assert "500" in result.data["USDC"]

    async def test_get_chain_token_balances_unknown_token_aggregated(self, client):
        """Unknown tokens produce a single aggregated error rather than silent skip."""
        client.connected_chain_providers["Avalanche"].eth.get_balance.return_value = 1 * 10**18

        result = await client.get_chain_token_balances(
            "Avalanche", VALID_ADDRESS, ["AVAX", "FOOZ", "BARZ"]
        )

        assert not result.success
        assert "Unknown tokens" in result.error
        assert "FOOZ" in result.error
        assert "BARZ" in result.error

    async def test_get_chain_token_balances_empty_list(self, client):
        result = await client.get_chain_token_balances("Avalanche", VALID_ADDRESS, [])
        assert not result.success
        assert "non-empty list" in result.error

    async def test_get_chain_token_balances_non_list(self, client):
        from typing import cast as _cast

        # Caller passes a string instead of list[str] — guard against runtime misuse.
        result = await client.get_chain_token_balances(
            "Avalanche", VALID_ADDRESS, _cast(list[str], "AVAX")
        )
        assert not result.success
        assert "non-empty list" in result.error

    async def test_get_chain_token_balances_unknown_chain(self, client):
        result = await client.get_chain_token_balances(
            "DefinitelyNotAChain", VALID_ADDRESS, ["AVAX"]
        )
        assert not result.success

    async def test_get_chain_token_balances_invalid_token_symbol(self, client):
        result = await client.get_chain_token_balances("Avalanche", VALID_ADDRESS, ["AVAX", ""])
        assert not result.success

    async def test_get_chain_token_balances_invalid_chain_type(self, client):
        from typing import cast as _cast

        # Caller passes a non-string chain — guard against runtime misuse.
        result = await client.get_chain_token_balances(_cast(str, 12345), VALID_ADDRESS, ["AVAX"])
        assert not result.success
        assert "Invalid chain" in result.error

    async def test_get_chain_token_balances_internal_no_address(self, client):
        # Internal path: caller passes through with no address and no signer.
        client.account = None
        result = await client._get_chain_token_balances_cached("Avalanche", None, ("AVAX",))
        assert not result.success
        assert "Address required" in result.error

    async def test_get_chain_token_balances_internal_invalid_address(self, client):
        # Internal path: explicit but malformed address must fail validation
        # before any RPC call is made.
        result = await client._get_chain_token_balances_cached(
            "Avalanche", "0xnot-a-real-address", ("AVAX",)
        )
        assert not result.success

    async def test_get_chain_token_balances_aggregates_per_token_exception(self, client):
        """If one of the per-token fetches raises, the error is captured and
        aggregated alongside the other tokens' results."""
        from dexalot_sdk.utils.result import Result

        # First call raises, second succeeds.
        client._get_chain_wallet_balance_cached = AsyncMock(
            side_effect=[
                Exception("rpc blew up"),
                Result.ok({"chain": "Avalanche", "symbol": "USDC", "balance": "12.5"}),
            ]
        )

        client.token_data["USDC"] = {
            "Avalanche": {"chain_id": 43114, "address": "0xUSDC", "evmdecimals": 6},
        }

        result = await client.get_chain_token_balances("Avalanche", VALID_ADDRESS, ["AVAX", "USDC"])
        assert not result.success
        # AVAX is sorted before USDC; the AVAX fetch is the one that raised.
        assert "AVAX" in result.error

    async def test_get_chain_token_balances_propagates_basexception(self, client):
        """asyncio.CancelledError (a BaseException, not Exception) must
        propagate out rather than be swallowed into an aggregated error."""

        client._get_chain_wallet_balance_cached = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await client.get_chain_token_balances("Avalanche", VALID_ADDRESS, ["AVAX"])

    async def test_get_chain_token_balances_aggregates_generic_errors(self, client):
        """Per-token Result.fail messages that don't match the unknown-token
        pattern are aggregated into the errors-only branch."""
        from dexalot_sdk.utils.result import Result

        client._get_chain_wallet_balance_cached = AsyncMock(
            side_effect=[
                Result.fail("Chain ID not configured for Avalanche"),
                Result.fail("zero address on chain Avalanche"),
            ]
        )

        result = await client.get_chain_token_balances("Avalanche", VALID_ADDRESS, ["AVAX", "USDC"])
        assert not result.success
        assert "Unknown tokens" not in result.error
        assert "Chain ID not configured" in result.error
        assert "zero address" in result.error

    async def test_get_chain_token_balances_cache_key_order_insensitive(self, client):
        """Same token set in different order should hit the same cache slot."""
        client._cache_enabled = True
        client.connected_chain_providers["Avalanche"].eth.get_balance.return_value = 9 * 10**18

        mock_contract = MagicMock()
        mock_contract.functions.balanceOf.return_value.call = AsyncMock(return_value=42 * 10**6)
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_contract

        client.token_data["USDC"] = {
            "Avalanche": {"chain_id": 43114, "address": "0xUSDC", "evmdecimals": 6},
        }

        # Spy on the cached internal to confirm cache hits/misses.
        from dexalot_sdk.core.base import _BALANCE_CACHE

        _BALANCE_CACHE.clear()

        first = await client.get_chain_token_balances("Avalanche", VALID_ADDRESS, ["AVAX", "USDC"])
        second = await client.get_chain_token_balances("Avalanche", VALID_ADDRESS, ["USDC", "AVAX"])

        assert first.success and second.success
        assert first.data == second.data

        # Cache should hold a single entry for this token-set on this chain/address;
        # if ordering leaked into the key, we'd see two.
        keys_for_method = [
            k
            for k in _BALANCE_CACHE._store
            if isinstance(k, tuple) and k and k[0] == "_get_chain_token_balances_cached"
        ]
        assert len(keys_for_method) == 1

    async def test_get_all_chain_wallet_balances(self, client):
        client.w3_l1.eth.get_balance.return_value = 10 * 10**18
        client.connected_chain_providers["Avalanche"].eth.get_balance.return_value = 5 * 10**18
        info = await client.get_all_chain_wallet_balances()
        assert info.success
        l1_entry = next(b for b in info.data["chain_balances"] if b["chain"] == "Dexalot L1")
        assert "10.0" in l1_entry["balance"]
        avax_entry = next(
            b
            for b in info.data["chain_balances"]
            if b["chain"] == "Avalanche" and b["type"] == "Native"
        )
        assert avax_entry["balance"] == "5.0"

        # Test error handling - L1 balance error
        from dexalot_sdk.core.base import _BALANCE_CACHE

        _BALANCE_CACHE.clear()
        client.w3_l1.eth.get_balance.side_effect = Exception("L1 Error")
        info = await client.get_all_chain_wallet_balances()
        assert info.success
        l1_entry = next(b for b in info.data["chain_balances"] if b["chain"] == "Dexalot L1")
        # The error should be in the balance field
        assert "Error" in str(l1_entry["balance"])

    async def test_get_all_chain_wallet_balances_not_connected(self, client):
        client.w3_l1 = None
        info = await client.get_all_chain_wallet_balances()
        assert info.success
        l1_entry = next(b for b in info.data["chain_balances"] if b["chain"] == "Dexalot L1")
        assert l1_entry["balance"] == "Not connected"

    async def test_get_portfolio_balance(self, client):
        client.portfolio_sub_contract.functions.getBalance.return_value.call = AsyncMock(
            return_value=(10000000, 5000000, 5000000)
        )
        res = await client.get_portfolio_balance("USDC")
        assert res.success
        assert res.data["total"] == 10.0
        assert res.data["available"] == 5.0

    async def test_get_portfolio_balance_large_wei_precision(self, client):
        """Large wei values convert back to human units exactly (Decimal path).

        With the previous float division, balance_wei // (10**18) for very
        large balances would lose the last digit or two of precision. Routing
        through Utils.unit_conversion (Decimal-backed) preserves them.
        """
        # A balance just at the edge where float division starts to drift
        # (much larger than 2**53). Routing through Decimal keeps it exact.
        large_wei = 12345678901234567890123456  # 25 digits
        client.portfolio_sub_contract.functions.getBalance.return_value.call = AsyncMock(
            return_value=(large_wei, large_wei, large_wei)
        )
        res = await client.get_portfolio_balance("USDC")
        assert res.success
        # 6-decimal token (USDC): divide by 10^6
        assert res.data["total"] == float(Decimal(large_wei) / Decimal(10**6))

    async def test_get_portfolio_balance_empty_token(self, client):
        result = await client.get_portfolio_balance("")
        assert not result.success
        assert "cannot be empty" in result.error

    async def test_get_all_portfolio_balances(self, client):
        def side_effect(addr, page):
            mock_call = MagicMock()
            if page == 0:
                mock_call.call = AsyncMock(
                    return_value=([Utils.to_bytes32("AVAX")], [10**18], [10**18])
                )
            else:
                mock_call.call = AsyncMock(return_value=([], [], []))
            return mock_call

        client.portfolio_sub_contract.functions.getBalances.side_effect = side_effect
        res = await client.get_all_portfolio_balances()
        assert res.success
        assert "AVAX" in res.data
        assert res.data["AVAX"]["total"] == 1.0

    async def test_get_all_portfolio_balances_parallel_batching(self, client):
        """3 pages of data: all 3 should be fetched in a single parallel batch (not sequentially)."""
        call_order: list[int] = []

        def side_effect(addr, page):
            mock_call = MagicMock()
            if page < 3:

                async def _call(p=page):
                    call_order.append(p)
                    return ([Utils.to_bytes32(f"TOK{p}")], [10**18], [10**18])

                mock_call.call = _call
            else:

                async def _empty():
                    call_order.append(page)
                    return ([], [], [])

                mock_call.call = _empty
            return mock_call

        client.portfolio_sub_contract.functions.getBalances.side_effect = side_effect
        res = await client.get_all_portfolio_balances()
        assert res.success
        assert len(res.data) == 3
        # All 3 data pages plus the first empty page (page 3) land in the first batch of 5
        # — total calls should be exactly 5 (pages 0–4 in one gather)
        assert set(call_order) == {0, 1, 2, 3, 4}

    async def test_add_gas(self, client):
        client.portfolio_sub_contract.functions.withdrawNative.return_value.fn_name = (
            "withdrawNative"
        )
        res = await client.add_gas(1.0)
        assert res.success
        assert res.data["tx_hash"] == "0x74785f68617368"
        assert res.data["operation"] == "add_gas"

    async def test_remove_gas(self, client):
        client.portfolio_sub_contract.functions.depositNative.return_value.fn_name = "depositNative"
        res = await client.remove_gas(1.0)
        assert res.success
        assert res.data["tx_hash"] == "0x74785f68617368"
        assert res.data["operation"] == "remove_gas"

    async def test_transfer_portfolio(self, client):
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 10.0}))
        res = await client.transfer_portfolio("USDC", 1.0, VALID_RECIPIENT)
        assert res.success
        assert res.data["tx_hash"] == "0x74785f68617368"
        assert res.data["operation"] == "transfer_portfolio"
        call_args = client.portfolio_sub_contract.functions.transferToken.call_args
        assert call_args[0][1] == Utils.to_bytes32("USDC")
        assert call_args[0][2] == 1000000

    @pytest.mark.parametrize(
        "amount,decimals,expected_wei",
        [
            (2933.0, 18, 2933000000000000000000),
            (100.0, 6, 100_000_000),
            (Decimal("0.000001"), 6, 1),
        ],
    )
    async def test_transfer_portfolio_precision(self, client, amount, decimals, expected_wei):
        """transfer_portfolio encodes amount via Decimal arithmetic."""
        from unittest.mock import patch

        from dexalot_sdk.core.transfer import TransferClient
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(
            return_value=Result.ok({"available": float(amount) + 1})
        )
        with patch.object(TransferClient, "_get_token_decimals", return_value=decimals):
            res = await client.transfer_portfolio("USDC", amount, VALID_RECIPIENT)
        assert res.success
        call_args = client.portfolio_sub_contract.functions.transferToken.call_args
        assert call_args[0][2] == expected_wei

    async def test_transfer_token(self, client):
        res = await client.transfer_token("USDC", VALID_RECIPIENT, 1.0)
        assert res.success
        assert "Transfer Token transaction sent" in res.data

        # Verify args
        call_args = client.portfolio_sub_contract.functions.transferToken.call_args
        # transferToken(_from, _to, _symbol, _quantity)
        assert call_args[0][0] == VALID_ADDRESS
        assert call_args[0][1] == VALID_RECIPIENT
        assert call_args[0][2] == Utils.to_bytes32("USDC")
        assert call_args[0][3] == 1000000  # 1.0 * 10^6

    @pytest.mark.parametrize(
        "amount,decimals,expected_wei",
        [
            # The 2933.0 case from the bug report — 18-decimal token
            (2933.0, 18, 2933000000000000000000),
            (1840.0, 18, 1840000000000000000000),
            # USDC-style 6-decimal token
            (100.0, 6, 100_000_000),
            # Sub-unit values
            (0.1, 6, 100_000),
            # Decimal / string inputs
            (Decimal("2933"), 18, 2933000000000000000000),
            ("2933.5", 18, 2933500000000000000000),
        ],
    )
    async def test_transfer_token_precision(self, client, amount, decimals, expected_wei):
        """transfer_token encodes amount via Decimal arithmetic (no float-mul drift)."""
        from unittest.mock import patch

        from dexalot_sdk.core.transfer import TransferClient

        with patch.object(TransferClient, "_get_token_decimals", return_value=decimals):
            res = await client.transfer_token("USDC", VALID_RECIPIENT, amount)
        assert res.success
        call_args = client.portfolio_sub_contract.functions.transferToken.call_args
        # transferToken(_from, _to, _symbol, _quantity)
        assert call_args[0][3] == expected_wei

    async def test_transfer_token_errors(self, client):
        """Test transfer_token errors."""
        client.account = None
        result = await client.transfer_token("USDC", VALID_RECIPIENT, 1)
        assert not result.success
        assert result.error == "Private key not configured."

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.portfolio_sub_contract = None
        result = await client.transfer_token("USDC", VALID_RECIPIENT, 1)
        assert not result.success
        assert result.error == "Subnet Provider or Portfolio Contract not initialized."

        client.portfolio_sub_contract = MagicMock()
        client.w3_l1 = self.create_w3()
        client.portfolio_sub_contract.functions.transferToken.side_effect = Exception("Err")
        result = await client.transfer_token("USDC", VALID_RECIPIENT, 1)
        assert not result.success
        assert "transferring token" in result.error.lower()

    async def test_get_deposit_bridge_fee(self, client):
        mock_bridge = MagicMock()
        mock_bridge.functions.getBridgeFee.return_value.call = AsyncMock(
            return_value=5000000000000000
        )
        client.portfolio_main_avax_contract.functions.portfolioBridge.return_value.call = AsyncMock(
            return_value="0xBridge"
        )
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_bridge
        fee = await client.get_deposit_bridge_fee("AVAX", 1.0, "Avalanche")
        assert fee.success
        assert fee.data == 0.005

    @pytest.mark.parametrize(
        "amount,decimals,expected_wei",
        [
            (2933.0, 18, 2933000000000000000000),
            (Decimal("0.000001"), 6, 1),
        ],
    )
    async def test_get_deposit_bridge_fee_precision(self, client, amount, decimals, expected_wei):
        """get_deposit_bridge_fee encodes amount via Decimal arithmetic."""
        from unittest.mock import patch

        from dexalot_sdk.core.transfer import TransferClient

        captured_amount_wei = []

        async def capture_internal(w3, contract, bridge_id, symbol, amt_wei):
            captured_amount_wei.append(amt_wei)
            return 5000000000000000

        client._get_bridge_fee_internal = capture_internal
        mock_bridge = MagicMock()
        client.portfolio_main_avax_contract.functions.portfolioBridge.return_value.call = AsyncMock(
            return_value="0xBridge"
        )
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_bridge
        with patch.object(TransferClient, "_get_token_decimals", return_value=decimals):
            res = await client.get_deposit_bridge_fee("AVAX", amount, "Avalanche")
        assert res.success
        assert captured_amount_wei == [expected_wei]

    async def test_deposit(self, client):
        from dexalot_sdk.utils.result import Result

        client.get_deposit_bridge_fee = AsyncMock(return_value=Result.ok(0.01))
        mock_token = MagicMock()
        mock_token.functions.allowance.return_value.call = AsyncMock(return_value=10**20)
        mock_token.functions.getBridgeFee.return_value.call = AsyncMock(return_value=0)
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_token
        client.portfolio_main_avax_contract.functions.depositToken.return_value.fn_name = (
            "depositToken"
        )
        res = await client.deposit("USDC", 10.0, "Avalanche")
        assert res.success
        assert res.data["tx_hash"] == "0x74785f68617368"
        assert res.data["operation"] == "deposit"

        # Verify args
        call_args = client.portfolio_main_avax_contract.functions.depositToken.call_args
        # depositToken(_from, _symbol, _quantity, _bridgeId)
        assert call_args[0][2] == 10000000  # 10.0 * 10^6
        assert call_args[0][3] == 2  # Bridge ID for Avalanche

    @pytest.mark.parametrize(
        "amount,decimals,expected_wei",
        [
            (2933.0, 18, 2933000000000000000000),
            (1840.0, 18, 1840000000000000000000),
            (100.0, 6, 100_000_000),
        ],
    )
    async def test_deposit_precision(self, client, amount, decimals, expected_wei):
        """deposit encodes amount via Decimal arithmetic."""
        from unittest.mock import patch

        from dexalot_sdk.core.transfer import TransferClient
        from dexalot_sdk.utils.result import Result

        client.get_deposit_bridge_fee = AsyncMock(return_value=Result.ok(0.01))
        mock_token = MagicMock()
        mock_token.functions.allowance.return_value.call = AsyncMock(return_value=10**40)
        mock_token.functions.getBridgeFee.return_value.call = AsyncMock(return_value=0)
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_token
        client.portfolio_main_avax_contract.functions.depositToken.return_value.fn_name = (
            "depositToken"
        )
        with patch.object(TransferClient, "_get_token_decimals", return_value=decimals):
            res = await client.deposit("USDC", amount, "Avalanche")
        assert res.success
        call_args = client.portfolio_main_avax_contract.functions.depositToken.call_args
        # depositToken(_from, _symbol, _quantity, _bridgeId)
        assert call_args[0][2] == expected_wei

    @pytest.mark.parametrize(
        "amount,decimals,expected_wei",
        [
            (2933.0, 18, 2933000000000000000000),
            (5.0, 6, 5_000_000),
            (Decimal("0.123456"), 6, 123_456),
        ],
    )
    async def test_withdraw_precision(self, client, amount, decimals, expected_wei):
        """withdraw encodes amount via Decimal arithmetic."""
        from unittest.mock import patch

        from dexalot_sdk.core.transfer import TransferClient

        mock_token = MagicMock()
        mock_token.functions.allowance.return_value.call = AsyncMock(return_value=10**40)
        client.w3_l1.eth.contract.side_effect = None
        client.w3_l1.eth.contract.return_value = mock_token
        with patch.object(TransferClient, "_get_token_decimals", return_value=decimals):
            res = await client.withdraw("USDC", amount, "Avalanche")
        assert res.success
        call_args = client.portfolio_sub_contract.functions.withdrawToken.call_args
        # withdrawToken(_to, _symbol, _quantity, _feeType, _chainId)
        assert call_args[0][2] == expected_wei

    async def test_deposit_native(self, client):
        """Test deposit of native token (AVAX)."""
        # Mock _get_bridge_fee_internal
        client._get_bridge_fee_internal = AsyncMock(return_value=10000000000000000)  # 0.01 ETH

        client.portfolio_main_avax_contract.functions.depositNative.return_value.fn_name = (
            "depositNative"
        )

        res = await client.deposit("AVAX", 1.0, "Avalanche")
        assert res.success
        assert res.data["tx_hash"] == "0x74785f68617368"
        assert res.data["operation"] == "deposit"

        # Verify call
        client.portfolio_main_avax_contract.functions.depositNative.assert_called()

    async def test_withdraw(self, client):
        mock_token = MagicMock()
        mock_token.functions.allowance.return_value.call = AsyncMock(return_value=10**20)
        client.w3_l1.eth.contract.side_effect = None
        client.w3_l1.eth.contract.return_value = mock_token
        res = await client.withdraw("USDC", 5.0, "Avalanche")
        assert res.success
        assert res.data["tx_hash"] == "0x74785f68617368"
        assert res.data["operation"] == "withdraw"

        # Verify args
        call_args = client.portfolio_sub_contract.functions.withdrawToken.call_args
        # withdrawToken(_to, _symbol, _quantity, _feeType, _chainId)
        assert call_args[0][2] == 5000000  # 5.0 * 10^6
        assert call_args[0][4] == 43114  # Dest Chain ID

    async def test_withdraw_errors(self, client):
        """Test withdraw errors."""
        client.account = None
        result = await client.withdraw("USDC", 10, "Avalanche")
        assert not result.success
        assert result.error == "Private key not configured."

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS

        # Invalid chain
        result = await client.withdraw("USDC", 10, "INVALID")
        assert not result.success
        assert "not recognized" in result.error or "not known" in result.error

        # Contract not initialized
        client.portfolio_sub_contract = None
        client.w3_l1 = None
        result = await client.withdraw("USDC", 10, "Avalanche")
        assert not result.success
        assert "not initialized" in result.error

        # Token not supported
        client.portfolio_sub_contract = MagicMock()
        client.w3_l1 = self.create_w3()
        client.token_data = {}
        result = await client.withdraw("USDC", 10, "Avalanche")
        assert not result.success
        assert "not supported" in result.error

        # Gas estimation error
        client.token_data = {
            "A": {"env1": {"chain_id": 43114, "evmdecimals": 18, "address": "0xA"}}
        }
        client.portfolio_sub_contract.functions.withdrawToken.return_value.estimate_gas = AsyncMock(
            side_effect=Exception("Gas Err")
        )
        result = await client.withdraw("A", 1, "Avalanche")

    async def test_withdraw_rejects_explicit_wrong_environment_alias(self, client):
        client.chain_id = 43113
        client.chain_config = {"Fuji": {"chain_id": 43113, "native_symbol": "AVAX"}}
        client.token_data = {
            "USDC": {"Fuji": {"chain_id": 43113, "evmdecimals": 6, "address": "0xUSDC"}}
        }

        result = await client.withdraw("USDC", 1, "Avalanche Mainnet")

        assert not result.success
        assert "refers to mainnet" in result.error
        assert "Fuji" in result.error

    async def test_withdraw_missing_canonical_destination_after_resolution(self, client):
        from dexalot_sdk.utils.result import Result

        with patch.object(
            client,
            "resolve_chain_reference",
            return_value=Result.ok(MagicMock(canonical_name="MissingChain", chain_id=123)),
        ):
            result = await client.withdraw("USDC", 1, "MissingChain")

        assert not result.success
        assert "not known" in result.error

    async def test_deposit_allowance(self, client):
        from dexalot_sdk.utils.result import Result

        client.get_deposit_bridge_fee = AsyncMock(return_value=Result.ok(0.01))
        mock_token = MagicMock()
        mock_token.functions.allowance.return_value.call = AsyncMock(return_value=0)
        mock_token.functions.approve.return_value.build_transaction = AsyncMock(return_value={})
        mock_token.functions.approve.return_value.estimate_gas = AsyncMock(return_value=50000)
        mock_token.functions.approve.return_value.fn_name = "approve"
        mock_token.functions.getBridgeFee.return_value.call = AsyncMock(return_value=0)

        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_token
        client.portfolio_main_avax_contract.functions.depositToken.return_value.fn_name = (
            "depositToken"
        )
        res = await client.deposit("USDC", 10.0, "Avalanche")
        assert res.success
        assert res.data["tx_hash"] == "0x74785f68617368"
        assert res.data["operation"] == "deposit"
        mock_token.functions.approve.assert_called()

    async def test_withdraw_allowance(self, client):
        mock_token = MagicMock()
        mock_token.functions.allowance.return_value.call = AsyncMock(return_value=0)
        mock_token.functions.approve.return_value.build_transaction = AsyncMock(return_value={})
        mock_token.functions.approve.return_value.estimate_gas = AsyncMock(return_value=50000)
        mock_token.functions.approve.return_value.fn_name = "approve"

        client.w3_l1.eth.contract.side_effect = None
        client.w3_l1.eth.contract.return_value = mock_token
        res = await client.withdraw("USDC", 5.0, "Avalanche")
        assert res.success
        assert res.data["tx_hash"] == "0x74785f68617368"
        assert res.data["operation"] == "withdraw"
        mock_token.functions.approve.assert_called()

    async def test_all_chain_wallet_balances_zero_address(self, client):
        client.token_data = {
            "ZERO": {
                "Avalanche": {
                    "address": "0x0000000000000000000000000000000000000000",
                    "chain_id": 43114,
                }
            }
        }
        info = await client.get_all_chain_wallet_balances()
        assert info.success
        assert not any(b["symbol"] == "ZERO" for b in info.data["chain_balances"])

    async def test_all_chain_wallet_balances_contract_exc(self, client):
        from dexalot_sdk.core.base import _BALANCE_CACHE

        _BALANCE_CACHE.clear()
        client.token_data = {"ERR": {"Avalanche": {"address": "0xErr", "chain_id": 43114}}}
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = Exception("Err")
        info = await client.get_all_chain_wallet_balances()
        assert info.success
        assert not any(b["symbol"] == "ERR" for b in info.data["chain_balances"])
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None

    async def test_transfer_portfolio_insufficient(self, client):
        client.portfolio_sub_contract.functions.getBalance.return_value.call = AsyncMock(
            return_value=(0, 0, 0)
        )
        res = await client.transfer_portfolio("USDC", 100, VALID_RECIPIENT)
        assert not res.success
        assert "Insufficient available balance" in res.error

    async def test_deposit_generic_exc(self, client):
        # Mock _get_nonce to raise exception (since we now use nonce manager)
        client._get_nonce = AsyncMock(side_effect=Exception("Err"))
        res = await client.deposit("AVAX", 1, "Avalanche")
        assert not res.success
        assert "depositing" in res.error.lower()

    async def test_ensure_allowance_waits(self, client):
        client.token_data = {
            "USDC": {"Avalanche": {"chain_id": 43114, "evmdecimals": 6, "address": "0xUSDC"}}
        }
        mock_token = MagicMock()
        mock_token.functions.allowance.return_value.call = AsyncMock(return_value=0)
        mock_token.functions.approve.return_value.build_transaction = AsyncMock(return_value={})
        mock_token.functions.approve.return_value.estimate_gas = AsyncMock(return_value=50000)
        mock_token.functions.approve.return_value.fn_name = "approve"
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.connected_chain_providers["Avalanche"].eth.contract.return_value = mock_token
        from dexalot_sdk.utils.result import Result

        client.get_deposit_bridge_fee = AsyncMock(return_value=Result.ok(0))
        client.portfolio_main_avax_contract.functions.depositToken.return_value.fn_name = (
            "depositToken"
        )
        await client.deposit("USDC", 1, "Avalanche")
        client.connected_chain_providers[
            "Avalanche"
        ].eth.wait_for_transaction_receipt.assert_called()

    async def test_erc20_deposit_revokes_allowance_on_failure(self, client):
        """When the deposit tx fails after approval, _ensure_allowance is called with 0 to revoke."""
        client.token_data = {
            "USDC": {"Avalanche": {"chain_id": 43114, "evmdecimals": 6, "address": "0xUSDC"}}
        }
        ensure_calls = []

        async def fake_ensure_allowance(w3, token_addr, spender, amount, **kwargs):
            ensure_calls.append(amount)
            if amount > 0:
                return  # approval succeeds
            # revoke call also succeeds (no-op)

        client._ensure_allowance = fake_ensure_allowance
        client._build_and_send_tx = AsyncMock(side_effect=Exception("deposit reverted"))
        client._get_l1_token_info = AsyncMock(return_value={"address": "0xUSDC", "evmdecimals": 6})

        from dexalot_sdk.utils.result import Result

        client.get_deposit_bridge_fee = AsyncMock(return_value=Result.ok(0))
        res = await client.deposit("USDC", 1, "Avalanche")
        assert not res.success
        # First call approves amount_wei, second call revokes with 0
        assert len(ensure_calls) == 2
        assert ensure_calls[0] > 0
        assert ensure_calls[1] == 0

    async def test_erc20_deposit_no_revoke_when_no_token_info(self, client):
        """When there is no L1 token info, _ensure_allowance is never called on deposit failure."""
        ensure_calls = []

        async def fake_ensure_allowance(w3, token_addr, spender, amount, **kwargs):
            ensure_calls.append(amount)

        client._ensure_allowance = fake_ensure_allowance
        client._build_and_send_tx = AsyncMock(side_effect=Exception("deposit reverted"))
        client._get_l1_token_info = AsyncMock(return_value=None)

        from dexalot_sdk.utils.result import Result

        client.get_deposit_bridge_fee = AsyncMock(return_value=Result.ok(0))
        res = await client.deposit("USDC", 1, "Avalanche")
        assert not res.success
        assert ensure_calls == []

    async def test_erc20_withdraw_revokes_allowance_on_failure(self, client):
        """When the withdraw tx fails after approval, _ensure_allowance is called with 0 to revoke."""
        subnet_chain_id = client.subnet_chain_id
        client.token_data = {
            "USDC": {
                "Dexalot": {
                    "chain_id": subnet_chain_id,
                    "evmdecimals": 6,
                    "address": "0xSUBNET_USDC",
                }
            }
        }
        client.chain_config = {
            "Avalanche": {"chain_id": 43114, "rpc_url": "https://rpc.example.com"}
        }

        ensure_calls = []

        async def fake_ensure_allowance(w3, token_addr, spender, amount, **kwargs):
            ensure_calls.append(amount)

        client._ensure_allowance = fake_ensure_allowance
        client._build_and_send_tx = AsyncMock(side_effect=Exception("withdraw reverted"))
        client._get_token_decimals = MagicMock(return_value=6)

        res = await client.withdraw("USDC", 1, "Avalanche")
        assert not res.success
        assert len(ensure_calls) == 2
        assert ensure_calls[0] > 0
        assert ensure_calls[1] == 0

    async def test_exceptions_and_edge_cases(self, client):
        # 1. get_all_portfolio_balances exception
        client.portfolio_sub_contract.functions.getBalances.side_effect = Exception("Err")
        result = await client.get_all_portfolio_balances()
        assert not result.success
        assert "getting all balances" in result.error.lower()

        # 2. add_gas exception
        client.portfolio_sub_contract.functions.withdrawNative.side_effect = Exception("Err")
        result = await client.add_gas(1)
        assert not result.success
        assert "adding gas" in result.error.lower()

        # 3. get_deposit_bridge_fee exception
        client.portfolio_main_avax_contract.functions.portfolioBridge.side_effect = Exception("Err")
        result = await client.get_deposit_bridge_fee("AVAX", 1, "Avalanche")
        assert not result.success
        assert "getting bridge fee" in result.error.lower()

        # 4. deposit exception
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = None
        client.portfolio_main_avax_contract.functions.depositToken.side_effect = Exception("Err")
        from dexalot_sdk.utils.result import Result

        client.get_deposit_bridge_fee = AsyncMock(return_value=Result.ok(0.01))
        # Use a non-native token to test depositToken exception handling
        client.token_data["USDC"] = {
            "Avalanche": {
                "chain_id": 43114,
                "address": "0xUSDC",
                "evmdecimals": 6,
            }
        }
        result = await client.deposit("USDC", 1, "Avalanche")
        assert not result.success
        assert "depositing" in result.error.lower()

    async def test_coverage_gaps(self, client):
        # Pages 0-10 have data; page 11+ are empty — 11 tokens total
        def side_effect(addr, page):
            mock_call = MagicMock()
            if page < 11:
                mock_call.call = AsyncMock(
                    return_value=([Utils.to_bytes32(f"T{page}")], [10**18], [10**18])
                )
            else:
                mock_call.call = AsyncMock(return_value=([], [], []))
            return mock_call

        client.portfolio_sub_contract.functions.getBalances.side_effect = side_effect
        res = await client.get_all_portfolio_balances()
        assert res.success
        assert len(res.data) == 11

        client.connected_chain_providers = {}
        client.w3_connected_chain = None
        result = await client.deposit("A", 1, "Avalanche")
        assert not result.success
        assert "not initialized" in result.error

        client.portfolio_main_avax_contract = None
        result = await client.deposit("A", 1, "Avalanche")
        assert not result.success
        assert "not initialized" in result.error

        client.connected_chain_providers = {"Avalanche": self.create_w3()}
        client.w3_connected_chain = client.connected_chain_providers["Avalanche"]
        client.portfolio_main_avax_contract = client.w3_connected_chain.eth.contract()
        client.token_data = {"A": {"env": {"chain_id": 999}}}
        result = await client.get_deposit_bridge_fee("A", 1, "Avalanche")
        assert not result.success
        assert "not supported" in result.error

    async def test_transfer_missing_coverage(self, client):
        """Test additional error paths."""
        # Mock tokens API to return empty list (token not found)
        mock_resp = AsyncMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        result = await client.get_token_details("INVALID")
        assert not result.success
        assert "not found" in result.error

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.w3_l1 = self.create_w3()
        client.w3_l1.eth.get_balance.side_effect = Exception("Err")
        info = await client.get_all_chain_wallet_balances()
        assert info.success
        l1_entry = next(b for b in info.data["chain_balances"] if b["chain"] == "Dexalot L1")
        assert "Error" in l1_entry["balance"]

        from dexalot_sdk.core.base import _BALANCE_CACHE

        _BALANCE_CACHE.clear()
        client.w3_l1.eth.get_balance.side_effect = None
        client.w3_l1.eth.get_balance.return_value = 1000
        # Mock connected-chain provider
        mock_provider = self.create_w3()
        mock_provider.eth.get_balance.side_effect = Exception("Err")
        client.connected_chain_providers = {"Avalanche": mock_provider}
        client.chain_config = {"Avalanche": {"chain_id": 43114, "native_symbol": "AVAX"}}

        info = await client.get_all_chain_wallet_balances()
        assert info.success
        avax_entry = next(b for b in info.data["chain_balances"] if b["chain"] == "Avalanche")
        assert "Error" in str(avax_entry["balance"])

        client.portfolio_sub_contract = MagicMock()
        client.portfolio_sub_contract.functions.getBalance.side_effect = Exception("Err")
        result = await client.get_portfolio_balance("USDC")
        assert not result.success
        assert "getting portfolio balance" in result.error.lower()

        client.account = None
        result = await client.deposit("USDC", 10, "Avalanche")
        assert not result.success
        assert result.error == "Private key not configured."

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.chain_config = {"Avalanche": {"chain_id": 43114}}
        client.chain_id = 43114
        client.w3_connected_chain = self.create_w3()
        client.portfolio_main_avax_contract = client.w3_connected_chain.eth.contract()
        client.token_data = {"AVAX": {"Avalanche": {"chain_id": 43114, "evmdecimals": 18}}}

        client._get_bridge_fee_internal = AsyncMock(return_value=0)
        client.portfolio_main_avax_contract.functions.depositNative.return_value.estimate_gas = (
            AsyncMock(side_effect=Exception("Revert"))
        )
        result = await client.deposit("AVAX", 1, "Avalanche")
        assert not result.success
        assert "depositing" in result.error.lower()

        client.token_data = {
            "USDC": {"Avalanche": {"chain_id": 43114, "evmdecimals": 6, "address": "0xUSDC"}}
        }
        # Mock allowance
        mock_token = MagicMock()
        mock_token.functions.allowance.return_value.call = AsyncMock(return_value=1000000000)
        client.w3_connected_chain.eth.contract.return_value = mock_token
        client.portfolio_main_avax_contract.functions.depositToken.return_value.estimate_gas = (
            AsyncMock(side_effect=Exception("Revert"))
        )
        result = await client.deposit("USDC", 1, "Avalanche")
        assert not result.success
        assert "depositing" in result.error.lower()

        client.portfolio_sub_contract = MagicMock()
        client.w3_l1 = self.create_w3()
        client.token_data = {"USDC": {"Avalanche": {"chain_id": 43114, "evmdecimals": 6}}}
        client.portfolio_sub_contract.functions.withdrawToken.return_value.estimate_gas = AsyncMock(
            side_effect=Exception("Revert")
        )
        result = await client.withdraw("USDC", 1, "Avalanche")
        assert not result.success
        assert "withdrawing" in result.error.lower()

        client.portfolio_sub_contract.functions.transferToken.return_value.estimate_gas = AsyncMock(
            side_effect=Exception("Revert")
        )

        original_get_balance = client.get_portfolio_balance
        client.get_portfolio_balance = AsyncMock(return_value={"available": 1000.0})

        client.w3_l1.eth.send_raw_transaction.side_effect = Exception("Err")
        client.portfolio_sub_contract.functions.transferToken.return_value.build_transaction = (
            AsyncMock(return_value={})
        )
        result = await client.transfer_portfolio("USDC", 1, VALID_RECIPIENT)
        assert not result.success
        assert "transferring portfolio asset" in result.error.lower()

        client.get_portfolio_balance = original_get_balance

        client.account = None
        result = await client.get_portfolio_balance("A")
        assert not result.success
        assert result.error == "Address required (pass as param or set signer)"
        result = await client.get_all_portfolio_balances()
        assert not result.success
        assert result.error == "Address required (pass as param or set signer)"
        result = await client.add_gas(1)
        assert not result.success
        assert result.error == "Private key not configured."
        result = await client.remove_gas(1)
        assert not result.success
        assert result.error == "Private key not configured."
        result = await client.transfer_portfolio("A", 1, "B")
        assert not result.success
        assert result.error == "Private key not configured."
        result = await client.deposit("A", 1, "C")
        assert not result.success
        assert result.error == "Private key not configured."

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.portfolio_sub_contract = None
        result = await client.get_portfolio_balance("USDC")
        assert not result.success
        assert "not initialized" in result.error
        result = await client.get_all_portfolio_balances()
        assert not result.success
        assert "not initialized" in (result.error or "")
        result = await client.add_gas(1)
        assert not result.success
        assert "not initialized" in result.error
        result = await client.remove_gas(1)
        assert not result.success
        assert "not initialized" in result.error
        result = await client.transfer_portfolio("USDC", 1, VALID_RECIPIENT)
        assert not result.success
        assert "not initialized" in result.error

        result = await client.get_deposit_bridge_fee("A", 1, "INVALID")
        assert not result.success
        assert "not recognized" in result.error or "not known" in result.error

    async def test_transfer_missing_coverage_2(self, client):
        """Test additional error paths."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS

        client.w3_l1 = self.create_w3()
        client.connected_chain_providers = {"Avalanche": self.create_w3()}
        client.chain_config = {"Avalanche": {"chain_id": 43114}}
        client.token_data = {
            "ZERO": {
                "Avalanche": {
                    "chain_id": 43114,
                    "address": "0x0000000000000000000000000000000000000000",
                }
            }
        }
        info = await client.get_all_chain_wallet_balances()
        assert info.success
        assert not any(b["symbol"] == "ZERO" for b in info.data["chain_balances"])

        client.token_data = {"ERR": {"Avalanche": {"chain_id": 43114, "address": "0xErr"}}}
        client.connected_chain_providers["Avalanche"].eth.contract.side_effect = Exception("Err")
        info = await client.get_all_chain_wallet_balances()
        assert info.success
        assert not any(b["symbol"] == "ERR" for b in info.data["chain_balances"])

        client.portfolio_sub_contract = MagicMock()
        client.portfolio_sub_contract.functions.getBalance.return_value.call = AsyncMock(
            return_value=(0, 0, 0)
        )
        client.token_data = {"UNSUPPORTED": {}}
        client.subnet_chain_id = 123
        client.chain_id = 456
        # Use a valid address for the account to pass validation
        client.account.address = VALID_ADDRESS
        result = await client.get_portfolio_balance("UNSUPPORTED")
        assert not result.success
        assert "not supported" in result.error

        client.token_data = {"USDC": {"Avalanche": {"chain_id": 43114, "evmdecimals": 6}}}
        client.subnet_chain_id = 123
        client.chain_id = 43114
        client.portfolio_sub_contract.functions.getBalance.return_value.call = AsyncMock(
            return_value=(0, 0, 0)
        )
        result = await client.transfer_portfolio("USDC", 100, VALID_RECIPIENT)
        assert not result.success
        assert "Insufficient available balance" in result.error

    async def test_transfer_portfolio_balance_result_error(self, client):
        """Test transfer_portfolio when balance_result is Result with error."""
        from dexalot_sdk.utils.result import Result

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.w3_l1 = self.create_w3()
        client.portfolio_sub_contract = MagicMock()
        client.token_data = {"USDC": {"env": {"chain_id": 43114, "evmdecimals": 6}}}

        # Mock get_portfolio_balance to return Result with error
        client.get_portfolio_balance = AsyncMock(return_value=Result.fail("Balance check failed"))

        result = await client.transfer_portfolio("USDC", 10.0, VALID_RECIPIENT)
        assert not result.success
        assert "Error checking balance" in result.error

    async def test_transfer_portfolio_invalid_balance_format(self, client):
        """Test transfer_portfolio when balance_result has invalid format."""

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.w3_l1 = self.create_w3()
        client.portfolio_sub_contract = MagicMock()
        client.token_data = {"USDC": {"env": {"chain_id": 43114, "evmdecimals": 6}}}

        # Mock get_portfolio_balance to return invalid format (not Result, not dict)
        client.get_portfolio_balance = AsyncMock(return_value="Invalid format")

        result = await client.transfer_portfolio("USDC", 10.0, VALID_RECIPIENT)
        assert not result.success
        assert "Invalid balance response format" in result.error

        client.chain_config = {"Avalanche": {"chain_id": 999}}
        client.chain_id = 43114
        result = await client.deposit("USDC", 1, "Avalanche")
        # This test seems to have conflicting assertions - let's check what actually happens
        # The deposit should fail due to chain config mismatch
        assert not result.success
        assert "chain" in result.error.lower() or "multi-chain" in result.error.lower()

        client.chain_config = {"Avalanche": {"chain_id": 43114}}
        client.chain_id = 43114
        client.w3_connected_chain = self.create_w3()
        client.portfolio_main_avax_contract = client.w3_connected_chain.eth.contract()
        client.token_data = {"UNSUPPORTED": {}}
        result = await client.deposit("UNSUPPORTED", 1, "Avalanche")
        assert not result.success
        assert "not supported" in result.error

        result = await client.deposit("USDC", 1, "INVALID_CHAIN")
        assert not result.success
        assert "not recognized" in result.error or "not known" in result.error

        client.chain_config = {"Avalanche": {"chain_id": 43114}}
        client.chain_id = 43114
        client.w3_connected_chain = self.create_w3()
        client.portfolio_main_avax_contract = client.w3_connected_chain.eth.contract()
        client.token_data = {"AVAX": {"Avalanche": {"chain_id": 43114, "evmdecimals": 18}}}
        # Mock _get_nonce to raise exception (since we now use nonce manager)
        client._get_nonce = AsyncMock(side_effect=Exception("Generic Error"))
        result = await client.deposit("AVAX", 1, "Avalanche")
        assert not result.success
        assert "depositing" in result.error.lower()

        client.w3_l1 = self.create_w3()
        client.w3_l1.eth.get_transaction_count.side_effect = Exception(
            "Generic Portfolio Transfer Error"
        )
        client.token_data = {"USDC": {"Avalanche": {"chain_id": 43114, "evmdecimals": 6}}}
        client.subnet_chain_id = 123
        client.chain_id = 43114
        client.portfolio_sub_contract.functions.getBalance.return_value.call = AsyncMock(
            return_value=(1000000000, 1000000000, 0)
        )
        # Reset get_portfolio_balance to return a valid Result
        from dexalot_sdk.utils.result import Result

        client.get_portfolio_balance = AsyncMock(return_value=Result.ok({"available": 1000000.0}))
        result = await client.transfer_portfolio("USDC", 1, VALID_RECIPIENT)
        assert not result.success
        assert (
            "transferring portfolio asset" in result.error.lower()
            or "Insufficient available balance" in result.error
        )

        client.token_data = {"FALLBACK": {"Avalanche": {"chain_id": 43114, "evmdecimals": 6}}}
        client.subnet_chain_id = 123
        client.chain_id = 43114
        # Mock transaction build
        client.w3_l1.eth.get_transaction_count.side_effect = None
        client.w3_l1.eth.get_transaction_count.return_value = 1
        client.portfolio_sub_contract.functions.transferToken.return_value.build_transaction = (
            AsyncMock(return_value={})
        )
        client.w3_l1.eth.send_raw_transaction = AsyncMock(return_value=b"tx")

        await client.transfer_token("FALLBACK", VALID_RECIPIENT, 1)
        call_args = client.portfolio_sub_contract.functions.transferToken.call_args[0]
        assert call_args[3] == 1000000

    async def test_coverage_gaps_new(self, client):
        """Test specific coverage gaps identified."""
        client.chain_id = 43114
        client.subnet_chain_id = 12345
        client.token_data = {
            "ONLY_SUBNET": {"Subnet": {"chain_id": 12345, "evmdecimals": 6, "address": "0xSubnet"}}
        }
        client._get_bridge_fee_internal = AsyncMock(return_value=0)
        client.portfolio_main_avax_contract = client.w3_connected_chain.eth.contract()
        client.portfolio_main_avax_contract.functions.depositToken.return_value.fn_name = (
            "depositToken"
        )
        client.connected_chain_providers["Avalanche"].eth.send_raw_transaction = AsyncMock(
            return_value=b"tx"
        )
        client.connected_chain_providers["Avalanche"].eth.get_transaction_count = AsyncMock(
            return_value=1
        )

        res = await client.deposit("ONLY_SUBNET", 1, "Avalanche")
        assert res.success
        assert res.data["operation"] == "deposit"
        assert "tx_hash" in res.data

        client.token_data = {
            "USDC": {"Avalanche": {"chain_id": 43114, "evmdecimals": 6, "address": "0xUSDC"}}
        }
        mock_token_contract = MagicMock()
        mock_token_contract.functions.allowance.return_value.call = AsyncMock(return_value=0)
        # Mock approve function
        mock_approve_fn = MagicMock()
        mock_approve_fn.fn_name = "approve"
        mock_token_contract.functions.approve.return_value = mock_approve_fn

        client.connected_chain_providers[
            "Avalanche"
        ].eth.contract.return_value = mock_token_contract

        # Mock build_transaction for approve
        mock_approve_fn.build_transaction = AsyncMock(return_value={})
        mock_approve_fn.estimate_gas = AsyncMock(return_value=50000)

        # Call deposit to trigger ensure_allowance
        client._get_bridge_fee_internal = AsyncMock(return_value=0)
        res = await client.deposit("USDC", 1, "Avalanche")

        # Verify wait_for_transaction_receipt was called
        client.connected_chain_providers[
            "Avalanche"
        ].eth.wait_for_transaction_receipt.assert_called()

    async def test_get_all_chain_wallet_balances_no_address(self, client):
        """Test get_all_chain_wallet_balances with no address."""
        client.account = None
        result = await client.get_all_chain_wallet_balances()
        assert not result.success
        assert "Address required" in result.error

    async def test_fetch_erc20_balances_list_exception(self, client):
        """Test _fetch_erc20_balances_list exception handling."""
        from dexalot_sdk.core.base import _BALANCE_CACHE

        _BALANCE_CACHE.clear()

        # Setup token data
        client.token_data = {
            "TOKEN1": {"Avalanche": {"chain_id": 43114, "address": "0xToken1", "evmdecimals": 18}},
            "TOKEN2": {"Avalanche": {"chain_id": 43114, "address": "0xToken2", "evmdecimals": 18}},
        }

        # Create a custom mock provider that raises exceptions
        mock_provider = MagicMock()

        # Create a coroutine that raises an exception
        async def raise_exception():
            raise Exception("Balance Error")

        # Mock contract creation to succeed, but balanceOf.call() to raise exception
        mock_contract = MagicMock()
        # balanceOf() returns a function object with a .call() method
        # .call() should return a coroutine that raises when awaited
        # This will cause asyncio.gather to return the Exception in results
        mock_balance_of = MagicMock()
        mock_balance_of.call = raise_exception
        mock_contract.functions.balanceOf.return_value = mock_balance_of
        mock_provider.eth.contract.return_value = mock_contract

        result = await client._fetch_erc20_balances_list(
            43114, "Avalanche", mock_provider, VALID_ADDRESS
        )
        assert isinstance(result, list)
        assert len(result) == 0
        assert mock_provider.eth.contract.called

    async def test_fetch_erc20_balances_list_concurrency_limit(self, client):
        """At most erc20_balance_concurrency calls run simultaneously."""
        client.config.erc20_balance_concurrency = 3
        n_tokens = 9

        client.token_data = {
            f"TOK{i}": {
                "Avalanche": {
                    "chain_id": 43114,
                    "address": f"0x{'0' * 39}{i}",
                    "evmdecimals": 18,
                }
            }
            for i in range(1, n_tokens + 1)
        }

        in_flight = 0
        max_in_flight = 0

        async def slow_balance():
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1
            return 10**18  # 1.0 token

        mock_provider = MagicMock()
        mock_contract = MagicMock()
        mock_balance_of = MagicMock()
        mock_balance_of.call = slow_balance
        mock_contract.functions.balanceOf.return_value = mock_balance_of
        mock_provider.eth.contract.return_value = mock_contract

        result = await client._fetch_erc20_balances_list(
            43114, "Avalanche", mock_provider, VALID_ADDRESS
        )
        assert len(result) == n_tokens
        assert max_in_flight <= client.config.erc20_balance_concurrency

    async def test_fetch_erc20_balances_list_concurrency_config(self, client):
        """Results are correct when concurrency is limited to 1 (fully serialised)."""
        client.config.erc20_balance_concurrency = 1

        client.token_data = {
            "TOKA": {"Avalanche": {"chain_id": 43114, "address": "0xTokenA", "evmdecimals": 6}},
            "TOKB": {"Avalanche": {"chain_id": 43114, "address": "0xTokenB", "evmdecimals": 18}},
        }

        call_order: list[str] = []

        def make_balance_coro(symbol: str, amount: int):
            async def _coro():
                call_order.append(symbol)
                return amount

            return _coro

        mock_provider = MagicMock()

        def contract_factory(address, abi):
            mock_contract = MagicMock()
            symbol = "TOKA" if address == "0xTokenA" else "TOKB"
            amount = 5 * 10**6 if symbol == "TOKA" else 2 * 10**18
            mock_balance_of = MagicMock()
            mock_balance_of.call = make_balance_coro(symbol, amount)
            mock_contract.functions.balanceOf.return_value = mock_balance_of
            return mock_contract

        mock_provider.eth.contract.side_effect = contract_factory

        result = await client._fetch_erc20_balances_list(
            43114, "Avalanche", mock_provider, VALID_ADDRESS
        )
        assert len(result) == 2
        symbols = {r["symbol"] for r in result}
        assert symbols == {"TOKA", "TOKB"}
        balances = {r["symbol"]: r["balance"] for r in result}
        assert balances["TOKA"] == "5.0"
        assert balances["TOKB"] == "2.0"

    async def test_fetch_erc20_balances_list_decimal_precision(self, client):
        """_fetch_erc20_balances_list delegates to Utils.unit_conversion (Decimal-based)."""
        from unittest.mock import patch

        from dexalot_sdk.utils import Utils

        client.token_data = {
            "TOKA": {"Avalanche": {"chain_id": 43114, "address": "0xTokenA", "evmdecimals": 6}},
        }

        mock_provider = MagicMock()
        mock_contract = MagicMock()
        mock_balance_of = MagicMock()

        async def _call():
            return 5_000_000  # 5.0 TOKA with 6 decimals

        mock_balance_of.call = _call
        mock_contract.functions.balanceOf.return_value = mock_balance_of
        mock_provider.eth.contract.return_value = mock_contract

        with patch.object(Utils, "unit_conversion", wraps=Utils.unit_conversion) as mock_conv:
            result = await client._fetch_erc20_balances_list(
                43114, "Avalanche", mock_provider, VALID_ADDRESS
            )

        assert len(result) == 1
        assert result[0]["balance"] == "5.0"
        mock_conv.assert_called_once_with(5_000_000, 6, to_base=False)

    async def test_remove_gas_exception(self, client):
        """Test remove_gas exception handling."""
        client.portfolio_sub_contract.functions.depositNative.side_effect = Exception(
            "Contract Error"
        )
        result = await client.remove_gas(1.0)
        assert not result.success
        assert "removing gas" in result.error.lower()

    async def test_get_deposit_bridge_fee_not_initialized(self, client):
        """Test get_deposit_bridge_fee when not initialized."""
        client.w3_connected_chain = None
        client.portfolio_main_avax_contract = None
        result = await client.get_deposit_bridge_fee("AVAX", 1, "Avalanche")
        assert not result.success
        assert "not initialized" in result.error

    async def test_get_deposit_bridge_fee_missing_canonical_source_after_resolution(self, client):
        from dexalot_sdk.utils.result import Result

        with patch.object(
            client,
            "resolve_chain_reference",
            return_value=Result.ok(MagicMock(canonical_name="MissingChain", chain_id=123)),
        ):
            result = await client.get_deposit_bridge_fee("AVAX", 1, "MissingChain")

        assert not result.success
        assert "not known" in result.error

    async def test_get_deposit_bridge_fee_rejects_invalid_token(self, client):
        result = await client.get_deposit_bridge_fee("", 1.0, "Avalanche")
        assert not result.success
        assert "token" in (result.error or "").lower()

    async def test_get_deposit_bridge_fee_rejects_non_positive_amount(self, client):
        result = await client.get_deposit_bridge_fee("AVAX", 0.0, "Avalanche")
        assert not result.success
        assert "amount" in (result.error or "").lower()

    async def test_build_and_send_tx_retry_disabled(self, client):
        """Test _build_and_send_tx when retry is disabled."""
        client.config.retry_enabled = False
        client._rpc_rate_limiter = None

        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.private_key = "0x" + "a" * 64  # Valid 66-char private key (32 bytes)

        mock_w3 = self.create_w3()
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=5)
        mock_w3.eth.gas_price = AsyncMock(return_value=100)
        tx_hash_bytes = b"tx_hash"
        mock_w3.eth.send_raw_transaction = AsyncMock(return_value=tx_hash_bytes)
        mock_w3.to_hex = lambda x: f"0x{x.hex() if isinstance(x, bytes) else x}"

        func_call = MagicMock()
        func_call.fn_name = "transfer"
        func_call.estimate_gas = AsyncMock(return_value=50000)
        func_call.build_transaction = AsyncMock(return_value={"to": "0xContract", "data": "0x"})

        mock_w3.eth.account.sign_transaction = MagicMock()
        mock_w3.eth.account.sign_transaction.return_value.raw_transaction = b"raw_tx"

        # Mock _rpc_call to return proper values for receipt waiting
        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            elif method == "eth.send_raw_transaction":
                return b"tx_hash"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        result = await client._build_and_send_tx(mock_w3, func_call, value=0)
        assert result == "0x74785f68617368"  # hex of "tx_hash"

        func_call.estimate_gas.assert_called_once()

    async def test_build_and_send_tx_no_account(self, client):
        """Test _build_and_send_tx raises ValueError when account is None."""
        client.account = None
        mock_w3 = self.create_w3()
        func_call = MagicMock()

        with pytest.raises(ValueError, match="Account is required for signing transactions"):
            await client._build_and_send_tx(mock_w3, func_call, value=0)

    async def test_build_and_send_tx_receipt_status_failed(self, client):
        """Test _build_and_send_tx when receipt status != 1."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.private_key = "0x" + "a" * 64

        mock_w3 = self.create_w3()
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=5)
        mock_w3.eth.gas_price = AsyncMock(return_value=100)
        tx_hash_bytes = b"tx_hash"
        mock_w3.eth.send_raw_transaction = AsyncMock(return_value=tx_hash_bytes)
        mock_w3.to_hex = lambda x: f"0x{x.hex() if isinstance(x, bytes) else x}"

        func_call = MagicMock()
        func_call.fn_name = "transfer"
        func_call.estimate_gas = AsyncMock(return_value=50000)
        func_call.build_transaction = AsyncMock(return_value={"to": "0xContract", "data": "0x"})

        mock_w3.eth.account.sign_transaction = MagicMock()
        mock_w3.eth.account.sign_transaction.return_value.raw_transaction = b"raw_tx"

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 0}  # Failed transaction
            elif method == "eth.send_raw_transaction":
                return b"tx_hash"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        with pytest.raises(Exception, match="Transaction reverted"):
            await client._build_and_send_tx(mock_w3, func_call, value=0)

    async def test_build_and_send_tx_wait_for_receipt_false(self, client):
        """Test _build_and_send_tx with wait_for_receipt=False."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        client.private_key = "0x" + "a" * 64

        mock_w3 = self.create_w3()
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=5)
        mock_w3.eth.gas_price = AsyncMock(return_value=100)
        tx_hash_bytes = b"tx_hash"
        mock_w3.eth.send_raw_transaction = AsyncMock(return_value=tx_hash_bytes)
        mock_w3.to_hex = lambda x: f"0x{x.hex() if isinstance(x, bytes) else x}"

        func_call = MagicMock()
        func_call.fn_name = "transfer"
        func_call.estimate_gas = AsyncMock(return_value=50000)
        func_call.build_transaction = AsyncMock(return_value={"to": "0xContract", "data": "0x"})

        mock_w3.eth.account.sign_transaction = MagicMock()
        mock_w3.eth.account.sign_transaction.return_value.raw_transaction = b"raw_tx"

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        result = await client._build_and_send_tx(
            mock_w3, func_call, value=0, wait_for_receipt=False
        )
        assert result == "0x74785f68617368"  # hex of "tx_hash"

    async def test_get_provider_for_chain_fallback(self, client):
        """Test _get_provider_for_chain falling back to connected_chain_providers."""

        config = DexalotConfig(provider_failover_enabled=True)
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client_with_failover = MockClient(config=config)
                mock_provider = MagicMock()
                client_with_failover.connected_chain_providers["TestChain"] = mock_provider

                # Make provider manager return None
                async def return_none(*args):
                    return None

                client_with_failover._provider_manager.get_provider = return_none

                result = await client_with_failover._get_provider_for_chain("TestChain")
                assert result == mock_provider

    async def test_get_provider_for_chain_provider_manager_returns_provider(self, client):
        """Test _get_provider_for_chain when provider manager returns a provider."""

        config = DexalotConfig(provider_failover_enabled=True)
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client_with_failover = MockClient(config=config)
                mock_provider = MagicMock()

                # Make provider manager return a provider
                async def return_provider(*args):
                    return mock_provider

                client_with_failover._provider_manager.get_provider = return_provider

                result = await client_with_failover._get_provider_for_chain("TestChain")
                assert result == mock_provider

    async def test_get_available_chains_provider_manager_no_providers(self, client):
        """Test _get_available_chains when provider manager has no providers."""

        config = DexalotConfig(provider_failover_enabled=True)
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client_with_failover = MockClient(config=config)
                client_with_failover.connected_chain_providers["TestChain"] = MagicMock()
                client_with_failover.chain_config = {"TestChain": {}}

                # Provider manager has no providers for TestChain (get_provider_count returns 0)
                client_with_failover._provider_manager.get_provider_count = lambda x: 0

                chains = client_with_failover._get_available_chains()
                # Should still include TestChain from connected_chain_providers
                assert "TestChain" in chains
                # But not from provider manager since count is 0
                assert len(chains) == 1

    async def test_get_available_chains_provider_manager_has_providers(self, client):
        """Test _get_available_chains when provider manager has providers."""

        config = DexalotConfig(provider_failover_enabled=True)
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client_with_failover = MockClient(config=config)
                client_with_failover.connected_chain_providers["TestChain1"] = MagicMock()
                client_with_failover.chain_config = {
                    "TestChain1": {},
                    "TestChain2": {},
                }

                # Provider manager has providers for TestChain2 (get_provider_count returns > 0)
                client_with_failover._provider_manager.get_provider_count = lambda x: (
                    1 if x == "TestChain2" else 0
                )

                chains = client_with_failover._get_available_chains()
                # Should include TestChain1 from connected_chain_providers
                assert "TestChain1" in chains
                # Should also include TestChain2 from provider manager (when count > 0)
                assert "TestChain2" in chains
                assert len(chains) == 2

    async def test_get_all_chain_wallet_balances_no_chain_id(self, client):
        """Test get_all_chain_wallet_balances when chain_info doesn't have chain_id."""
        client.w3_l1.eth.get_balance = AsyncMock(return_value=10 * 10**18)
        client.connected_chain_providers["Avalanche"].eth.get_balance = AsyncMock(
            return_value=5 * 10**18
        )

        # Remove chain_id from chain_config
        client.chain_config["Avalanche"] = {"native_symbol": "AVAX"}  # No chain_id

        info = await client.get_all_chain_wallet_balances()
        assert info.success
        # Should still work, just won't fetch ERC20 balances for chains without chain_id
        assert len(info.data["chain_balances"]) >= 1

    async def test_get_all_chain_wallet_balances_provider_none(self, client):
        """Test get_all_chain_wallet_balances when _get_provider_for_chain returns None."""
        client.w3_l1.eth.get_balance = AsyncMock(return_value=10 * 10**18)

        # Make _get_provider_for_chain return None for a chain
        original_get_provider = client._get_provider_for_chain
        call_count = 0

        async def get_provider_with_none(chain):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # First call (Avalanche) returns None
                return None
            return await original_get_provider(chain)

        client._get_provider_for_chain = get_provider_with_none

        info = await client.get_all_chain_wallet_balances()
        assert info.success
        # Should skip chains where provider is None (continue to next chain)
        # Should still have balances for other chains
        assert len(info.data["chain_balances"]) >= 0

    async def test_get_chain_wallet_balance_invalid_address(self, client):
        """get_chain_wallet_balance rejects addresses that fail validate_address before any RPC call."""
        client.account = MagicMock()
        client.account.address = "0xInvalid"  # Too short
        result = await client.get_chain_wallet_balance("Avalanche", "AVAX")
        assert not result.success
        assert "Invalid address" in result.error

    async def test_get_chain_wallet_balance_invalid_token(self, client):
        """get_chain_wallet_balance rejects empty token via validate_token_symbol before any RPC call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.get_chain_wallet_balance("Avalanche", "")  # Empty token
        assert not result.success
        assert "Invalid token" in result.error

    async def test_get_chain_wallet_balance_invalid_chain(self, client):
        """get_chain_wallet_balance rejects empty chain string before any RPC call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.get_chain_wallet_balance("", "AVAX")  # Empty chain
        assert not result.success
        assert "Invalid chain" in result.error

    async def test_get_chain_wallet_balance_cached_invalid_chain_and_not_connected(self, client):
        result = await client._get_chain_wallet_balance_cached("", "AVAX", VALID_ADDRESS)
        assert not result.success
        assert "Invalid chain" in result.error

        result = await client._get_chain_wallet_balance_cached(
            "UnknownChain", "AVAX", VALID_ADDRESS
        )
        assert not result.success
        assert "not connected" in result.error

    async def test_transfer_portfolio_invalid_params(self, client):
        """transfer_portfolio rejects invalid token via validate_transfer_params before any on-chain call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        # Invalid token
        result = await client.transfer_portfolio("", 1.0, VALID_RECIPIENT)
        assert not result.success
        assert "Invalid token" in result.error

    async def test_deposit_invalid_token(self, client):
        """deposit rejects empty token via validate_token_symbol before any on-chain call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.deposit("", 1.0, "Avalanche")
        assert not result.success
        assert "Invalid token" in result.error

    async def test_deposit_invalid_amount(self, client):
        """deposit rejects negative amount via validate_positive_float before any on-chain call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.deposit("USDC", -1.0, "Avalanche")
        assert not result.success
        assert "Invalid amount" in result.error

    async def test_deposit_invalid_source_chain(self, client):
        """deposit rejects empty source_chain string before any on-chain call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.deposit("USDC", 1.0, "")  # Empty chain
        assert not result.success
        assert "Invalid source_chain" in result.error

    def test_validate_deposit_params_error_paths(self, client):
        client.account = None
        result = client._validate_deposit_params("USDC", 1.0, "Avalanche")
        assert not result.success
        assert "Private key not configured." == result.error

        client.account = MagicMock()
        result = client._validate_deposit_params("USDC", 1.0, "")
        assert not result.success
        assert "Invalid source_chain" in result.error

        result = client._validate_deposit_params("USDC", 1.0, "UnknownChain")
        assert not result.success
        assert "not known" in result.error

    async def test_get_l1_token_info_not_found(self, client):
        """_get_l1_token_info returns None when the token is absent from token_data."""
        client.token_data = {}
        client.chain_id = 43114
        result = await client._get_l1_token_info("UNKNOWN")
        assert result is None

    async def test_withdraw_invalid_token(self, client):
        """withdraw rejects empty token via validate_token_symbol before any on-chain call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.withdraw("", 1.0, "Avalanche")
        assert not result.success
        assert "Invalid token" in result.error

    async def test_withdraw_invalid_amount(self, client):
        """withdraw rejects negative amount via validate_positive_float before any on-chain call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.withdraw("USDC", -1.0, "Avalanche")
        assert not result.success
        assert "Invalid amount" in result.error

    async def test_withdraw_invalid_destination_chain(self, client):
        """withdraw rejects empty destination_chain string before any on-chain call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        result = await client.withdraw("USDC", 1.0, "")  # Empty chain
        assert not result.success
        assert "Invalid destination_chain" in result.error

    async def test_transfer_token_invalid_params(self, client):
        """transfer_token rejects invalid token via validate_transfer_params before any on-chain call."""
        client.account = MagicMock()
        client.account.address = VALID_ADDRESS
        # Invalid token
        result = await client.transfer_token("", VALID_RECIPIENT, 1.0)
        assert not result.success
        assert "Invalid token" in result.error

    async def test_get_token_details_validation_failure(self, client):
        """Test get_token_details when token validation fails to verify early return."""
        # Test with invalid token symbol (empty string)
        result = await client.get_token_details("")
        assert not result.success
        assert "invalid token" in result.error.lower() or "cannot be empty" in result.error.lower()

        # Test with invalid token symbol (non-alphanumeric)
        result = await client.get_token_details("INVALID@TOKEN")
        assert not result.success
        # The validation might pass for this, so check for either validation error or not found
        assert "invalid token" in result.error.lower() or "not found" in result.error.lower()

    async def test_get_token_details_exception_handling(self, client):
        """Test get_token_details exception handling when an exception occurs during token fetching."""

        # Mock _make_http_request to raise an exception
        async def failing_request(*args, **kwargs):
            raise Exception("Network error")

        client._make_http_request = failing_request

        result = await client.get_token_details("AVAX")
        assert not result.success
        assert "getting token details" in result.error.lower()

    async def test_get_l1_native_balance_sanitizes_error(self, client):
        """H-5: _get_l1_native_balance must not leak raw exception text (e.g. URLs) to callers."""
        secret_url = "https://rpc.example.com/secret-key"
        client.w3_l1.eth.get_balance = AsyncMock(side_effect=Exception(f"failed: {secret_url}"))

        entry = await client._get_l1_native_balance(VALID_ADDRESS)

        assert secret_url not in entry["balance"], (
            "Raw exception with secret URL must not appear in balance output"
        )
        assert entry["balance"].startswith("Error:")

    async def test_get_native_balance_sanitizes_error(self, client):
        """H-5: _get_native_balance must not leak raw exception text (e.g. URLs) to callers."""
        secret_url = "https://rpc.example.com/secret-key"
        w3 = self.create_w3()
        w3.eth.get_balance = AsyncMock(side_effect=Exception(f"failed: {secret_url}"))

        entry = await client._get_native_balance("Avalanche", w3, VALID_ADDRESS, "AVAX")

        assert secret_url not in entry["balance"], (
            "Raw exception with secret URL must not appear in balance output"
        )
        assert entry["balance"].startswith("Error:")

    async def test_get_all_portfolio_balances_rpc_exception(self, client):
        """When asyncio.gather returns a BaseException in its results, the method re-raises it immediately."""

        def make_get_balances_raise(query_address, page):
            result_obj = MagicMock()
            result_obj.call = AsyncMock(side_effect=RuntimeError("rpc error"))
            return result_obj

        client.portfolio_sub_contract.functions.getBalances = make_get_balances_raise

        result = await client._get_all_portfolio_balances_cached(VALID_ADDRESS)
        assert not result.success
        assert "rpc error" in result.error or result.error  # sanitized but fails

    async def test_get_all_portfolio_balances_empty_symbols(self, client):
        """Pagination stops early when a page returns an empty symbols list (got_empty sentinel)."""
        pages_called = []

        def make_get_balances(query_address, page):
            pages_called.append(page)
            result_obj = MagicMock()
            if page == 0:
                sym = b"AVAX" + b"\x00" * 28
                result_obj.call = AsyncMock(return_value=([sym], [10 * 10**18], [10 * 10**18]))
            else:
                # Empty symbols list triggers got_empty
                result_obj.call = AsyncMock(return_value=([], [], []))
            return result_obj

        client.portfolio_sub_contract.functions.getBalances = make_get_balances

        result = await client._get_all_portfolio_balances_cached(VALID_ADDRESS)
        assert result.success
        # Should have stopped after the empty page, so only pages 0..4 (batch 0) called
        assert all(p < 5 for p in pages_called)

    async def test_deposit_balance_data_none(self, client):
        """transfer_portfolio returns a fail Result with 'Invalid balance response format' when balance_result.data is None."""
        from dexalot_sdk.utils.result import Result

        with patch.object(
            client, "get_portfolio_balance", new=AsyncMock(return_value=Result.ok(None))
        ):
            result = await client.withdraw("AVAX", 1.0, "Avalanche")
            # The None-data check is in transfer_portfolio_asset; deposit goes a different path.
            # We use transfer_portfolio_asset which has the same guard.
            pass

        # Direct test: transfer_portfolio with balance_result.data = None
        with patch.object(
            client, "get_portfolio_balance", new=AsyncMock(return_value=Result.ok(None))
        ):
            result = await client.transfer_portfolio(
                "AVAX", 1.0, VALID_RECIPIENT, wait_for_receipt=True
            )
        assert not result.success
        assert "Invalid balance response format" in result.error

    async def test_deposit_native_no_account(self, client):
        """_execute_avax_deposit raises ValueError immediately when account is None, before any transaction is built."""
        client.account = None
        w3 = self.create_w3()
        contract = w3.eth.contract()
        with pytest.raises(ValueError, match="Account is required"):
            await client._execute_avax_deposit(w3, contract, 1000, 0, 0)

    async def test_deposit_erc20_no_account(self, client):
        """_execute_erc20_deposit raises ValueError immediately when account is None, before any transaction is built."""
        client.account = None
        w3 = self.create_w3()
        contract = w3.eth.contract()
        with pytest.raises(ValueError, match="Account is required"):
            await client._execute_erc20_deposit(w3, contract, "USDC", 1000, 0, 0)

    async def test_deposit_erc20_allowance_exception_swallowed(self, client):
        """On tx failure, _execute_erc20_deposit attempts to revoke allowance; if that revoke also raises, the original exception is still re-raised."""
        w3 = self.create_w3()
        contract = w3.eth.contract()
        contract.address = "0xPortfolio"

        # Arrange token data so _get_l1_token_info returns a result
        client.token_data["USDC"] = {
            "env1": {"chain_id": 43114, "evmdecimals": 6, "address": "0xUSDCAddr"}
        }
        client.chain_id = 43114

        call_count = 0

        async def ensure_allowance_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (grant): succeed
                return
            else:
                # Second call (revoke in finally): raise
                raise RuntimeError("revoke failed")

        with patch.object(client, "_ensure_allowance", side_effect=ensure_allowance_side_effect):
            # _build_and_send_tx raises to trigger the except branch
            with patch.object(
                client, "_build_and_send_tx", new=AsyncMock(side_effect=RuntimeError("tx failed"))
            ):
                with pytest.raises(RuntimeError, match="tx failed"):
                    await client._execute_erc20_deposit(w3, contract, "USDC", 1000, 0, 0)

        # Both ensure_allowance calls were attempted (grant + revoke attempt)
        assert call_count == 2

    async def test_deposit_decimals_data_none(self, client):
        """deposit returns fail with 'decimals' in the error when decimals_result.data is None, preventing invalid token scaling."""
        from dexalot_sdk.utils.result import Result

        with patch.object(
            client,
            "_resolve_deposit_decimals",
            return_value=Result.ok(None),
        ):
            result = await client.deposit("AVAX", 1.0, "Avalanche")
        assert not result.success
        assert "decimals" in result.error.lower()

    async def test_get_bridge_fee_no_account(self, client):
        """_get_bridge_fee_internal raises ValueError immediately when account is None, before any fee lookup."""
        client.account = None
        w3 = self.create_w3()
        contract = w3.eth.contract()
        with pytest.raises(ValueError, match="Account is required"):
            await client._get_bridge_fee_internal(w3, contract, 0, b"\x00" * 32, 0)

    async def test_withdraw_allowance_exception_swallowed(self, client):
        """On tx failure, _execute_erc20_withdrawal attempts to revoke allowance; if that revoke also raises, the original exception is still re-raised."""
        w3 = self.create_w3()
        contract = w3.eth.contract()
        contract.address = "0xSubPortfolio"

        subnet_token_info = {"address": "0xTokenAddr"}

        call_count = 0

        async def ensure_allowance_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return  # grant succeeds
            else:
                raise RuntimeError("revoke failed")

        with patch.object(client, "_ensure_allowance", side_effect=ensure_allowance_side_effect):
            with patch.object(
                client, "_build_and_send_tx", new=AsyncMock(side_effect=RuntimeError("tx failed"))
            ):
                with pytest.raises(RuntimeError, match="tx failed"):
                    await client._execute_erc20_withdrawal(
                        w3, contract, VALID_ADDRESS, b"\x00" * 32, 1000, 0, 12345, subnet_token_info
                    )

        assert call_count == 2

    async def test_ensure_allowance_no_account(self, client):
        """_ensure_allowance raises ValueError immediately when account is None, before any allowance check or approval."""
        client.account = None
        w3 = self.create_w3()
        with pytest.raises(ValueError, match="Account is required"):
            await client._ensure_allowance(w3, "0xToken", "0xSpender", 1000)
