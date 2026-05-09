import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from web3 import Web3

from dexalot_sdk.core.base import _SEMI_STATIC_CACHE, DexalotBaseClient
from dexalot_sdk.core.config import DexalotConfig
from dexalot_sdk.core.swap import SwapClient


class MockClient(SwapClient, DexalotBaseClient):
    chain_id = 43114


class TestSwapClient:
    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear shared module-level caches between tests to ensure isolation."""
        _SEMI_STATIC_CACHE.clear()
        yield
        _SEMI_STATIC_CACHE.clear()

    @pytest.fixture
    def client(self):
        # Patch environment to ensure no invalid PRIVATE_KEY is loaded
        with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            with patch("dexalot_sdk.core.config.load_dotenv"):
                client = MockClient()
                client.api_base_url = "https://api.dexalot-test.com"
                client.account = MagicMock()
                client.account.address = "0x1234567890123456789012345678901234567890"
                client.private_key = "0x" + "a" * 64  # Valid 66-char private key (32 bytes)
                client.chain_config = {
                    "Avalanche": {"chain_id": 43114},
                    "Fuji": {"chain_id": 43113},
                }
                client.rfq_pairs = {
                    43114: {"AVAX/USDC": {}},  # Only AVAX/USDC to test reverse lookup
                    43113: {"AVAX/USDC": {}},
                }
                client.connected_chain_providers = {"Avalanche": MagicMock()}
                client.deployments = {
                    "MainnetRFQ": {
                        "Avalanche": {
                            "address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                            "abi": [],
                        }
                    }
                }
                client._parse_revert_reason = lambda e: str(e)
                client.chain_id = 43114
                client.w3_l1 = MagicMock()
                client.w3_l1.eth.chain_id = AsyncMock(return_value=43114)
                client.get_clob_pairs = AsyncMock(return_value={})
                client.pairs = {}
                # Mock _get_nonce for nonce manager
                client._get_nonce = AsyncMock(return_value=0)

                # Mock async session
                client._mock_session = MagicMock()
                client._session = client._mock_session
                mock_resp = AsyncMock()
                mock_resp.status = 200
                mock_resp.json = AsyncMock(return_value={"price": 10})
                mock_resp.text = AsyncMock(return_value="")
                mock_resp.raise_for_status = MagicMock()
                mock_cm = AsyncMock()
                mock_cm.__aenter__.return_value = mock_resp
                mock_cm.__aexit__.return_value = None
                client._mock_session.get.return_value = mock_cm

                yield client

    async def test_get_swap_pairs(self, client):
        """Test get_swap_pairs."""
        # By ID
        result = await client.get_swap_pairs(43114)
        assert result.success
        assert result.data == {"AVAX/USDC": {}}
        # By Name
        result = await client.get_swap_pairs("Avalanche")
        assert result.success
        assert result.data == {"AVAX/USDC": {}}
        # Invalid
        result = await client.get_swap_pairs("Invalid")
        assert not result.success
        assert "Could not resolve" in result.error or "not recognized" in result.error

        result = await client.get_swap_pairs(999)
        assert not result.success
        assert "Could not resolve" in result.error or "not recognized" in result.error

    async def test_get_swap_pairs_resolves_environment_relative_aliases(self, client):
        client.chain_config = {"Fuji": {"chain_id": 43113}}
        client.rfq_pairs = {43113: {"AVAX/USDC": {}}}
        client.chain_id = 43113

        result = await client.get_swap_pairs("Avalanche C Chain")

        assert result.success
        assert result.data == {"AVAX/USDC": {}}

    async def test_get_swap_pairs_rehydrates_cached_state(self, client):
        """Cached RFQ pair results should still rebuild ``client.rfq_pairs``."""
        first_result = await client.get_swap_pairs(43114)
        assert first_result.success

        cached_client = client.__class__()
        cached_client.api_base_url = client.api_base_url
        cached_client.chain_config = client.chain_config
        cached_client._cache_enabled = True
        cached_client.rfq_pairs = {}
        cached_client._session = client._session

        second_result = await cached_client.get_swap_pairs(43114)
        assert second_result.success
        assert second_result.data == {"AVAX/USDC": {}}
        assert cached_client.rfq_pairs[43114] == {"AVAX/USDC": {}}

    def test_rehydrate_cached_get_swap_pairs_ignores_failed_or_unresolved_input(self, client):
        """Rehydration should skip failed results and unknown chain identifiers."""
        from dexalot_sdk.utils.result import Result

        client.rfq_pairs = {}

        client._rehydrate_cached_get_swap_pairs(Result.fail("boom"), 43114)
        assert client.rfq_pairs == {}

        client._rehydrate_cached_get_swap_pairs(Result.ok({"AVAX/USDC": {}}), "Unknown")
        assert client.rfq_pairs == {}

    async def test_transform_quote_from_api_lowercase_aliases(self, client):
        """Lowercase API identifiers gain snake_case aliases."""
        quote = {
            "chainid": 43114,
            "quoteid": "q123",
            "signature": "0xSig",
            "order": {
                "nonceAndMeta": 1,
                "expiry": 1,
                "makerAsset": "0xM",
                "takerAsset": "0xT",
                "maker": "0xMkr",
                "taker": "0xTkr",
                "makerAmount": 100,
                "takerAmount": 200,
            },
        }

        transformed = client._transform_quote_from_api(quote)
        assert transformed["chain_id"] == 43114
        assert transformed["quote_id"] == "q123"
        # Original fields preserved.
        assert transformed["chainid"] == 43114
        assert transformed["quoteid"] == "q123"
        assert transformed["signature"] == "0xSig"
        # Inner order normalized via _transform_order_data_from_api.
        order = transformed["order"]
        assert order["nonce_and_meta"] == 1
        assert order["maker_asset"] == "0xM"
        assert order["taker_asset"] == "0xT"
        assert order["maker_amount"] == 100
        assert order["taker_amount"] == 200
        # camelCase originals retained.
        assert order["nonceAndMeta"] == 1
        assert order["makerAsset"] == "0xM"

    async def test_transform_quote_from_api_camelcase_aliases(self, client):
        """camelCase API identifiers gain snake_case aliases."""
        quote = {
            "chainId": 43114,
            "quoteId": "q123",
            "signature": "0xSig",
            "order": {"nonceAndMeta": 1, "makerAsset": "0xM"},
        }

        transformed = client._transform_quote_from_api(quote)
        assert transformed["chain_id"] == 43114
        assert transformed["quote_id"] == "q123"
        assert transformed["order"]["nonce_and_meta"] == 1
        assert transformed["order"]["maker_asset"] == "0xM"

    async def test_transform_quote_from_api_prefers_existing_snake_case(self, client):
        """Snake_case keys already on the input win over camelCase/lowercase."""
        quote = {
            "chain_id": 43114,
            "quote_id": "q123",
            "chainid": 999,  # Should be ignored.
            "quoteid": "ignored",  # Should be ignored.
            "signature": "0xSig",
            "order": {"nonce_and_meta": 1, "maker_asset": "0xM"},
        }

        transformed = client._transform_quote_from_api(quote)
        assert transformed["chain_id"] == 43114
        assert transformed["quote_id"] == "q123"
        assert transformed["order"]["nonce_and_meta"] == 1

    async def test_transform_quote_from_api_no_order(self, client):
        """Quotes without an inner order dict pass through cleanly."""
        quote = {"chainid": 43114, "signature": "0xSig"}

        transformed = client._transform_quote_from_api(quote)
        assert transformed["chain_id"] == 43114
        assert "order" not in transformed

    async def test_get_swap_soft_quote(self, client):
        """Test get_swap_soft_quote logic."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"price": 10})
        mock_resp.text = AsyncMock(return_value="")
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_cm.__aexit__.return_value = None
        client._mock_session.get.return_value = mock_cm

        # Case 1: AVAX -> USDC (Sell Base)
        client.chain_id = 43114
        # pair="AVAX/USDC" in rfq_pairs. is_buy=False. is_base=1. side=0.
        await client.get_swap_soft_quote("AVAX", "USDC", 1.0, chain_id=43114)
        call_args = client._mock_session.get.call_args
        params = call_args[1]["params"]
        assert params["pair"] == "AVAX/USDC"
        assert params["isbase"] == "1"
        assert params["side"] == "1"

        # Case 2: USDC -> AVAX (Buy Base)
        # Reset mock
        client._mock_session.get.reset_mock()
        client._mock_session.get.return_value = mock_cm

        await client.get_swap_soft_quote("USDC", "AVAX", 10.0, chain_id=43114)
        call_args = client._mock_session.get.call_args
        params = call_args[1]["params"]
        assert params["pair"] == "AVAX/USDC"
        assert params["isbase"] == "0"
        assert params["side"] == "0"

        # Unsupported Pair
        res = await client.get_swap_soft_quote("BTC", "ETH", 1.0, chain_id=43114)
        assert not res.success
        assert "not found in RFQ or CLOB pairs" in res.error

    async def test_get_swap_firm_quote(self, client):
        """Test get_swap_firm_quote."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"quoteId": 1})
        mock_resp.text = AsyncMock(return_value="")
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_cm.__aexit__.return_value = None
        client._mock_session.get.return_value = mock_cm

        # No account
        client.account = None
        # It fails on pair not found first
        result = await client.get_swap_firm_quote("A", "B", 1)
        assert not result.success
        assert "not found in RFQ or CLOB pairs" in result.error
        client.account = MagicMock()
        client.account.address = "0x0000000000000000000000000000000000000005"

        await client.get_swap_firm_quote("AVAX", "USDC", 1.0, chain_id=43114)
        call_args = client._mock_session.get.call_args
        assert call_args[1]["params"]["address"] == "0x0000000000000000000000000000000000000005"
        assert "firm" in call_args[0][0]

    async def test_get_swap_quote_transforms_fields(self, client):
        """get_swap_soft_quote applies snake_case aliases to the API response."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "chainid": 43114,
                "signature": "0xSig",
                "order": {
                    "nonceAndMeta": 1,
                    "makerAsset": "0xM",
                },
            }
        )
        mock_resp.text = AsyncMock(return_value="")
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_cm.__aexit__.return_value = None
        client._mock_session.get.return_value = mock_cm

        client.rfq_pairs[43114] = {"AVAX/USDC": {}}
        result = await client.get_swap_soft_quote("AVAX", "USDC", 1.0, chain_id=43114)
        assert result.success
        assert result.data["chain_id"] == 43114
        assert result.data["signature"] == "0xSig"
        assert result.data["order"]["nonce_and_meta"] == 1
        assert result.data["order"]["maker_asset"] == "0xM"

    async def test_execute_rfq_swap(self, client):
        """execute_rfq_swap end-to-end with a flat firm-quote dict."""
        quote = {
            "success": True,
            "signature": "0x1234",
            "order": {
                "nonceAndMeta": 123,
                "expiry": 9999999999,
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1000,
                "takerAmount": 2000,
            },
        }

        mock_w3 = client.connected_chain_providers["Avalanche"]
        mock_w3.eth.chain_id = AsyncMock(return_value=43114)
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=0)

        class ConstantAwaitable:
            def __init__(self, val):
                self.val = val

            def __await__(self):
                async def _return_value():
                    return self.val

                return _return_value().__await__()

        mock_w3.eth.gas_price = ConstantAwaitable(100)
        mock_w3.eth.account.sign_transaction.return_value.raw_transaction = b"raw"
        mock_w3.eth.send_raw_transaction = AsyncMock(return_value=b"hash")
        mock_w3.to_hex.return_value = "0xHash"

        # Properly mock the contract and its functions
        mock_contract = MagicMock()
        mock_function_call = MagicMock()
        mock_function_call.estimate_gas = AsyncMock(return_value=100000)
        # build_transaction IS async in async web3
        mock_function_call.build_transaction = AsyncMock(return_value={})
        mock_contract.functions.simpleSwap.return_value = mock_function_call
        mock_w3.eth.contract.return_value = mock_contract

        # Mock _rpc_call to return receipt with status=1
        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            elif method == "eth.send_raw_transaction":
                return b"hash"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert res.success
        assert res.data["tx_hash"] == "0xHash"
        assert res.data["operation"] == "execute_rfq_swap"

        # Verify contract call
        # simpleSwap((tuple), signature)
        expected_tuple = (
            123,
            9999999999,
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
            "0x0000000000000000000000000000000000000003",
            "0x0000000000000000000000000000000000000004",
            1000,
            2000,
        )
        # Check call args manually to avoid bytes representation issues
        args = mock_contract.functions.simpleSwap.call_args[0]
        assert args[0] == expected_tuple
        assert args[1] == b"\x12\x34"

    async def test_get_swap_quote_error_status(self, client):
        """Test _get_swap_quote_base with non-200 status."""
        mock_resp_error = AsyncMock()
        mock_resp_error.status = 500
        mock_resp_error.text = AsyncMock(return_value="Internal Server Error")
        mock_cm_error = AsyncMock()
        mock_cm_error.__aenter__.return_value = mock_resp_error
        mock_cm_error.__aexit__.return_value = None
        client._mock_session.get.return_value = mock_cm_error

        client.rfq_pairs[43114] = {"AVAX/USDC": {}}
        result = await client.get_swap_soft_quote("AVAX", "USDC", 1, chain_id=43114)
        assert not result.success
        assert "fetching quote" in result.error.lower()
        assert "500" in result.error
        assert "Internal Server Error" in result.error

    async def test_execute_rfq_swap_native(self, client):
        """execute_rfq_swap with native token (zero-address makerAsset)."""
        quote = {
            "success": True,
            "signature": "0x1234",
            "order": {
                "nonceAndMeta": 123,
                "expiry": 9999999999,
                "makerAsset": "0x0000000000000000000000000000000000000000",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1000,
                "takerAmount": 2000,
            },
        }

        mock_w3 = client.connected_chain_providers["Avalanche"]
        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract

        # Capture build_transaction kwargs
        mock_contract.functions.simpleSwap.return_value.build_transaction.side_effect = lambda x: x

        await client.execute_rfq_swap(quote)

        # takerAsset is non-zero, so the call carries value=0.

    async def test_execute_rfq_swap_errors(self, client):
        """Test execute_rfq_swap errors."""
        client.account = None
        # Now raises ValueError instead of returning string
        with pytest.raises(ValueError, match="Account is required for signing transactions"):
            await client.execute_rfq_swap({})
        client.account = MagicMock()
        client.account.address = "0x0000000000000000000000000000000000000005"

        # Provider missing
        # Ensure data is valid to pass checks
        valid_quote = {"success": True, "signature": "s", "order": {"a": 1}}
        # Strip the connected-chain provider so resolution fails.
        saved_providers = client.connected_chain_providers
        client.connected_chain_providers = {}
        result = await client.execute_rfq_swap(valid_quote)
        assert not result.success
        assert "not initialized" in result.error
        client.connected_chain_providers = saved_providers  # Restore providers

        # Contract missing (Empty dict)
        client.deployments["MainnetRFQ"] = {}
        result = await client.execute_rfq_swap(
            {"chainId": 43114, "success": True, "signature": "s", "order": {"a": 1}}
        )
        assert not result.success
        assert "not initialized" in result.error

        # MainnetRFQ key missing
        if "MainnetRFQ" in client.deployments:
            del client.deployments["MainnetRFQ"]
        result = await client.execute_rfq_swap(
            {"chainId": 43114, "success": True, "signature": "s", "order": {"a": 1}}
        )
        assert not result.success
        assert "not initialized" in result.error

        # Exception
        client.deployments["MainnetRFQ"] = {
            "Avalanche": {"address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd", "abi": []}
        }
        # We need to ensure w3.eth.contract doesn't raise, but something inside try block raises
        # Or we fix the code to wrap contract creation in try block.
        # For now, let's mock contract creation to succeed, but function call to fail.
        mock_w3 = client.connected_chain_providers["Avalanche"]
        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract
        mock_contract.functions.simpleSwap.side_effect = Exception("Err")

        result = await client.execute_rfq_swap(
            {"success": True, "signature": "s", "order": {"a": 1}}
        )
        assert not result.success
        assert "executing swap" in result.error.lower()

    async def test_api_errors(self, client):
        """Test API errors."""
        from aiohttp import ClientError

        client._mock_session.get.side_effect = ClientError("API Fail")
        # Ensure pair is supported so it reaches API call
        client.rfq_pairs[43114]["A/B"] = {}
        result = await client.get_swap_soft_quote("A", "B", 1, chain_id=43114)
        assert not result.success
        assert "fetching quote" in result.error.lower()

    async def test_coverage_gaps(self, client):
        """Test missing coverage lines."""

        # Call with chain_id=None
        # We need to mock requests.get to succeed
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={})
        mock_resp.text = AsyncMock(return_value="")
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_cm.__aexit__.return_value = None
        client._mock_session.get.return_value = mock_cm
        client.rfq_pairs[43114] = {"A/B": {}}
        await client.get_swap_soft_quote("A", "B", 1, chain_id=None)
        # Should default to 43114
        call_args = client._mock_session.get.call_args
        assert call_args[1]["params"]["chainid"] == "43114"

        client._mock_session.get.reset_mock()
        client._mock_session.get.return_value = mock_cm
        client.rfq_pairs[43114] = {"A/B": {}}
        result = await client.get_swap_soft_quote("A", "B", 1, chain_id="invalid")
        assert not result.success
        assert "Could not resolve" in result.error or "not recognized" in result.error

        # Quote without chain_id falls back to self.chain_id and routes
        # through chain_config + connected_chain_providers.
        quote = {
            "success": True,
            "signature": "0x1234",
            "order": {
                "nonceAndMeta": 1,
                "expiry": 1,
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1,
                "takerAmount": 1,
            },
        }

        # Mock provider and contract on the connected-chain provider.
        mock_w3 = client.connected_chain_providers["Avalanche"]
        mock_contract = MagicMock()
        mock_function_call = MagicMock()
        mock_function_call.estimate_gas = AsyncMock(return_value=100000)
        mock_function_call.build_transaction = AsyncMock(return_value={})
        mock_contract.functions.simpleSwap.return_value = mock_function_call
        mock_w3.eth.contract.return_value = mock_contract
        mock_w3.eth.send_raw_transaction = AsyncMock(return_value=b"tx")
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=0)

        class ConstantAwaitable:
            def __init__(self, val):
                self.val = val

            def __await__(self):
                async def _return_value():
                    return self.val

                return _return_value().__await__()

        mock_w3.eth.gas_price = ConstantAwaitable(100)
        mock_w3.to_hex.return_value = "0xHash"
        mock_w3.eth.account.sign_transaction.return_value.raw_transaction = b"raw"
        client.deployments["MainnetRFQ"]["Avalanche"] = {
            "address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
            "abi": [],
        }

        # Mock _rpc_call to return receipt with status=1
        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            elif method == "eth.send_raw_transaction":
                return b"tx"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert res.success
        assert res.data["tx_hash"] == "0xHash"
        assert res.data["operation"] == "execute_rfq_swap"

    async def test_swap_errors(self, client):
        """execute_rfq_swap rejects malformed quotes with clear messages."""
        client.account = MagicMock()
        client.account.address = "0x0000000000000000000000000000000000000005"

        # signature missing.
        result = await client.execute_rfq_swap({"order": {"a": 1}})
        assert not result.success
        assert result.error == "Invalid firm quote: missing 'signature' or 'order' field."

        # order missing.
        result = await client.execute_rfq_swap({"signature": "0xSig"})
        assert not result.success
        assert result.error == "Invalid firm quote: missing 'signature' or 'order' field."

    async def test_execute_rfq_swap_result_error(self, client):
        """Test execute_rfq_swap when quote is a Result with error (lines 189-191)."""
        from dexalot_sdk.utils.result import Result

        # Test case: quote is a Result with success=False
        failed_quote = Result.fail("Quote failed")
        result = await client.execute_rfq_swap(failed_quote)
        assert not result.success
        assert "Cannot execute failed quote" in result.error

    async def test_execute_rfq_swap_result_success(self, client):
        """Test execute_rfq_swap when quote is a Result with success=True."""
        from dexalot_sdk.utils.result import Result

        # Test case: quote is a Result with success=True, should extract data
        quote_data = {
            "success": True,
            "signature": "0x1234",
            "order": {
                "nonceAndMeta": 1,
                "expiry": 9999999999,
                "makerAsset": "0x0000000000000000000000000000000000000001",
                "takerAsset": "0x0000000000000000000000000000000000000002",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1000000,
                "takerAmount": 2000000,
            },
        }
        successful_quote = Result.ok(quote_data)

        # Mock the contract and transaction flow
        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={
                "from": "0x0000000000000000000000000000000000000005",
                "nonce": 0,
                "gas": 120000,
                "gasPrice": 100,
            }
        )
        client._get_rfq_contract = AsyncMock(return_value=(client.w3_l1, mock_contract))
        client.w3_l1.eth.gas_price = AsyncMock(return_value=100)
        client.w3_l1.eth.send_raw_transaction = AsyncMock(return_value=b"tx_hash")
        client.w3_l1.to_hex = lambda x: "0xHash"

        # Mock _rpc_call to return receipt with status=1
        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            elif method == "eth.send_raw_transaction":
                return b"tx_hash"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        result = await client.execute_rfq_swap(successful_quote)
        assert result.success
        assert result.data["tx_hash"] == "0xHash"
        assert result.data["operation"] == "execute_rfq_swap"

        quote = {
            "success": True,
            "signature": b"sig",  # Bytes signature path.
            "order": {
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
            },
        }
        client.deployments["MainnetRFQ"] = {
            "Avalanche": {"address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd", "abi": []}
        }
        mock_w3 = client.w3_l1
        mock_contract = MagicMock()
        mock_function_call = MagicMock()
        mock_function_call.estimate_gas = AsyncMock(return_value=100000)
        mock_function_call.build_transaction = AsyncMock(return_value={})
        mock_contract.functions.simpleSwap.return_value = mock_function_call
        mock_w3.eth.contract.return_value = mock_contract
        mock_w3.eth.send_raw_transaction = AsyncMock(return_value=b"tx")
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=0)

        class ConstantAwaitable:
            def __init__(self, val):
                self.val = val

            def __await__(self):
                async def _return_value():
                    return self.val

                return _return_value().__await__()

        mock_w3.eth.gas_price = ConstantAwaitable(100)
        mock_w3.to_hex.return_value = "0xHash"
        mock_w3.eth.account.sign_transaction.return_value.raw_transaction = b"raw"

        res = await client.execute_rfq_swap(quote)
        assert res.success
        assert res.data["tx_hash"] == "0xHash"
        assert res.data["operation"] == "execute_rfq_swap"

        from aiohttp import ClientError

        # Use a simpler exception that doesn't require request_info
        client._mock_session.get.side_effect = ClientError("Server Error")

        client.rfq_pairs = {43114: {"A/B": {"pair": "A/B"}}}
        result = await client.get_swap_firm_quote("A", "B", 1)
        assert not result.success
        assert "fetching quote" in result.error.lower()

        client.rfq_pairs = {43114: {"B/A": {"base": "B"}}}
        # Mock API to return success to verify we got past the check
        client._mock_session.get.side_effect = None
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"id": "1"})
        mock_resp.text = AsyncMock(return_value="")
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_cm.__aexit__.return_value = None
        client._mock_session.get.return_value = mock_cm
        await client.get_swap_firm_quote("A", "B", 1)
        # Verify params
        call_args = client._mock_session.get.call_args
        assert call_args[1]["params"]["pair"] == "B/A"
        assert call_args[1]["params"]["isbase"] == "0"

        client.rfq_pairs = {}
        client.pairs = {"A/B": {"pair": "A/B"}}
        client._mock_session.get.return_value = mock_cm
        await client.get_swap_firm_quote("A", "B", 1)
        call_args = client._mock_session.get.call_args
        assert call_args[1]["params"]["pair"] == "A/B"
        assert call_args[1]["params"]["isbase"] == "1"

        client.pairs = {"B/A": {"pair": "B/A"}}
        client._mock_session.get.return_value = mock_cm
        await client.get_swap_firm_quote("A", "B", 1)
        call_args = client._mock_session.get.call_args
        assert call_args[1]["params"]["pair"] == "B/A"
        assert call_args[1]["params"]["isbase"] == "0"

    async def test_execute_rfq_swap_retry_disabled(self, client):
        """Test execute_rfq_swap when retry is disabled."""
        client.config.retry_enabled = False
        client._rpc_rate_limiter = None

        quote = {
            "signature": "0x1234",
            "order": {
                "nonceAndMeta": 1,
                "expiry": 9999999999,
                "makerAsset": "0x0000000000000000000000000000000000000001",
                "takerAsset": "0x0000000000000000000000000000000000000002",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1000000,
                "takerAmount": 2000000,
            },
        }

        client.rfq_pairs = {43114: {"A/B": {"pair": "A/B"}}}
        client.deployments = {
            "MainnetRFQ": {
                "Avalanche": {
                    "address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                    "abi": [],
                }
            }
        }

        mock_w3 = MagicMock()
        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={"to": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd", "data": "0x"}
        )
        mock_w3.eth.contract.return_value = mock_contract
        mock_w3.eth.send_raw_transaction = AsyncMock(return_value=b"tx")
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=0)

        class ConstantAwaitable:
            def __init__(self, val):
                self.val = val

            def __await__(self):
                async def _return_value():
                    return self.val

                return _return_value().__await__()

        mock_w3.eth.gas_price = ConstantAwaitable(100)
        mock_w3.to_hex.return_value = "0xHash"
        mock_w3.eth.account.sign_transaction.return_value.raw_transaction = b"raw"

        client.w3_l1 = mock_w3
        client._get_rfq_contract = AsyncMock(return_value=(mock_w3, mock_contract))

        # Mock _rpc_call to return receipt with status=1
        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            elif method == "eth.send_raw_transaction":
                return b"tx"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert res.success
        assert res.data["tx_hash"] == "0xHash"
        assert res.data["operation"] == "execute_rfq_swap"

        mock_contract.functions.simpleSwap.return_value.estimate_gas.assert_called_once()

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

    async def test_get_swap_pairs_invalid_chain_identifier(self, client):
        """get_swap_pairs rejects empty chain_identifier via validate_chain_identifier before any API call."""
        result = await client.get_swap_pairs("")  # Empty string
        assert not result.success
        assert "Invalid chain_identifier" in result.error

    def test_resolve_chain_id_result_none(self, client):
        result = client._resolve_chain_id_result(None)
        assert not result.success
        assert "required" in result.error

    async def test_get_swap_firm_quote_invalid_params(self, client):
        """Test get_swap_firm_quote with invalid params (coverage for lines 179, 185)."""
        # Invalid from_token
        result = await client.get_swap_firm_quote("", "USDC", 1.0)
        assert not result.success
        assert "Invalid from_token" in result.error

        # Invalid chain_id
        result = await client.get_swap_firm_quote("AVAX", "USDC", 1.0, chain_id=0)
        assert not result.success
        assert "Invalid chain_id" in result.error

    async def test_get_swap_soft_quote_invalid_params(self, client):
        """Test get_swap_soft_quote with invalid params (coverage for lines 207, 213)."""
        # Invalid from_token
        result = await client.get_swap_soft_quote("", "USDC", 1.0)
        assert not result.success
        assert "Invalid from_token" in result.error

        # Invalid chain_id
        result = await client.get_swap_soft_quote("AVAX", "USDC", 1.0, chain_id=-1)
        assert not result.success
        assert "Invalid chain_id" in result.error

    async def test_get_swap_pairs_cache_disabled(self, client):
        """Test get_swap_pairs with cache disabled to verify cache bypass logic."""
        # Disable cache
        client._cache_enabled = False

        # Add something to cache using the current key format
        env_key = getattr(client, "api_base_url", "") or ""
        key = ("get_swap_pairs", env_key, (43114,), frozenset())
        _SEMI_STATIC_CACHE._store[key] = "cached_data"

        # Mock successful response
        mock_resp = AsyncMock()
        mock_resp.json.return_value = {"AVAX/USDC": {}}
        mock_resp.raise_for_status = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        client._mock_session.get.return_value = mock_cm

        result = await client.get_swap_pairs(43114)

        # Verify cache was cleared
        assert key not in _SEMI_STATIC_CACHE._store
        assert result.success

    async def test_get_swap_pairs_rfq_fetch_fails_empty(self, client):
        """Test get_swap_pairs when RFQ pairs fetch fails and rfq_pairs is empty."""
        # Set rfq_pairs to empty
        client.rfq_pairs = {}

        # Mock API to raise exception during raise_for_status
        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()

            # Make raise_for_status raise an exception
            def raise_error():
                raise Exception("API Error")

            mock_resp.raise_for_status = raise_error
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_swap_pairs(43114)
        assert not result.success
        assert "Failed to fetch RFQ pairs" in result.error

    async def test_get_swap_pairs_initializes_rfq_pairs_when_none(self, client):
        """Test get_swap_pairs initializes rfq_pairs dict when it is None."""
        # Set rfq_pairs to None to trigger initialization
        client.rfq_pairs = None

        # Mock successful API response
        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.json.return_value = {"AVAX/USDC": {}}
            mock_resp.raise_for_status = MagicMock()
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        result = await client.get_swap_pairs(43114)
        assert result.success
        assert client.rfq_pairs is not None
        assert isinstance(client.rfq_pairs, dict)
        assert 43114 in client.rfq_pairs

    async def test_get_swap_pairs_chain_id_not_found_after_fetch(self, client):
        """Test get_swap_pairs when chain_id not found in rfq_pairs after fetch attempt."""
        # Set rfq_pairs to have data for a different chain_id
        client.rfq_pairs = {999: {"OTHER/PAIR": {}}}

        # Mock fetch to succeed but then manually remove the chain_id to simulate edge case
        # where fetch succeeds but chain_id is somehow not stored
        def side_effect(url, params=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.json.return_value = {"AVAX/USDC": {}}
            mock_resp.raise_for_status = MagicMock()
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        client._mock_session.get.side_effect = side_effect

        # Patch rfq_pairs to prevent storing 43114
        client.rfq_pairs = MagicMock()
        client.rfq_pairs.__contains__ = lambda self, key: key == 999
        client.rfq_pairs.__bool__ = lambda self: True
        client.rfq_pairs.__setitem__ = MagicMock()  # Prevent storing

        result = await client.get_swap_pairs(43114)
        assert not result.success
        assert "No swap pairs found" in result.error

    # ------------------------------------------------------------------
    # order-data snake_case transform, nonceAndMeta, execute_swap edge paths
    # ------------------------------------------------------------------

    def test_transform_order_data_nonce_and_meta_camelcase(self, client):
        """_transform_order_data_from_api maps nonceAndMeta to nonce_and_meta."""
        order_data = {"nonceAndMeta": 99, "makerAsset": "A"}
        result = client._transform_order_data_from_api(order_data)
        assert result["nonce_and_meta"] == 99

    def test_transform_order_data_empty_returns_early(self, client):
        """_transform_order_data_from_api returns the input unchanged when falsy."""
        assert client._transform_order_data_from_api({}) == {}
        assert client._transform_order_data_from_api(None) is None

    async def test_execute_rfq_swap_failed_result_input(self, client):
        """execute_rfq_swap returns fail immediately when given a failed Result as quote."""
        from dexalot_sdk.utils.result import Result

        failed_quote = Result.fail("quote fetch failed")
        res = await client.execute_rfq_swap(failed_quote)
        assert not res.success
        assert "quote fetch failed" in res.error

    async def test_execute_rfq_swap_result_ok_none_data(self, client):
        """execute_rfq_swap returns fail when given Result.ok(None) as quote."""
        from dexalot_sdk.utils.result import Result

        res = await client.execute_rfq_swap(Result.ok(None))
        assert not res.success
        assert "empty data" in res.error

    async def test_execute_rfq_swap_tx_reverted(self, client):
        """Reverted swap surfaces tx hash, block, and revert reason in the error."""
        quote = {
            "success": True,
            "signature": "0xabcd",
            "order": {
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1,
                "takerAmount": 1,
                "expiry": 9999999999,
                "nonceAndMeta": 0,
            },
        }
        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={
                "from": client.account.address,
                "to": "0xrfq",
                "data": "0xdeadbeef",
                "nonce": 0,
                "gas": 120000,
                "gasPrice": 100,
                "value": 0,
            }
        )
        client._get_rfq_contract = AsyncMock(return_value=(client.w3_l1, mock_contract))
        client.w3_l1.to_hex = lambda x: "0xdeadbeef"
        client.w3_l1.eth.call = AsyncMock(
            side_effect=Exception("execution reverted: RF-EXP-01")
        )

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 0, "blockNumber": 42}
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            if method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert not res.success
        assert "Transaction reverted" in res.error
        assert "tx=0xdeadbeef" in res.error
        assert "block=42" in res.error
        assert "RF-EXP-01" in res.error

    async def test_extract_revert_reason_generic_error(self, client):
        """A non-revert exception from eth_call falls back to a sliced message."""
        client.w3_l1.eth.call = AsyncMock(side_effect=Exception("rpc error: foo"))
        reason = await client._extract_revert_reason(
            client.w3_l1, {"from": "0xa", "to": "0xb", "data": "0xc"}, {"blockNumber": 1}
        )
        assert reason == "rpc error: foo"

    async def test_extract_revert_reason_handles_outer_failure(self, client):
        """If the helper itself blows up, it returns None instead of propagating."""
        broken_tx = MagicMock()
        broken_tx.get.side_effect = RuntimeError("boom")
        reason = await client._extract_revert_reason(
            client.w3_l1, broken_tx, {"blockNumber": 1}
        )
        assert reason is None

    async def test_execute_rfq_swap_revert_without_replay(self, client):
        """When eth_call replay is unavailable, error still surfaces tx + block."""
        quote = {
            "success": True,
            "signature": "0xabcd",
            "order": {
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1,
                "takerAmount": 1,
                "expiry": 9999999999,
                "nonceAndMeta": 0,
            },
        }
        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={
                "from": client.account.address,
                "to": "0xrfq",
                "data": "0xdeadbeef",
                "nonce": 0,
                "gas": 120000,
                "gasPrice": 100,
                "value": 0,
            }
        )
        client._get_rfq_contract = AsyncMock(return_value=(client.w3_l1, mock_contract))
        client.w3_l1.to_hex = lambda x: "0xabc123"
        # eth.call returns a value (no exception) — no revert reason available
        client.w3_l1.eth.call = AsyncMock(return_value=b"")

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 0, "blockNumber": 99}
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            if method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert not res.success
        assert "tx=0xabc123" in res.error
        assert "block=99" in res.error
        assert "reason=" not in res.error

    async def test_execute_rfq_swap_no_wait_for_receipt(self, client):
        """execute_rfq_swap returns 'sent' message when wait_for_receipt=False."""
        quote = {
            "success": True,
            "signature": "0xabcd",
            "order": {
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1,
                "takerAmount": 1,
                "expiry": 9999999999,
                "nonceAndMeta": 0,
            },
        }
        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={
                "from": client.account.address,
                "nonce": 0,
                "gas": 120000,
                "gasPrice": 100,
            }
        )
        client._get_rfq_contract = AsyncMock(return_value=(client.w3_l1, mock_contract))
        client.w3_l1.to_hex = lambda x: "0xHash"

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote, wait_for_receipt=False)
        assert res.success
        assert res.data["tx_hash"] == "0xHash"
        assert res.data["operation"] == "execute_rfq_swap"

    async def test_estimate_swap_gas_no_account_raises(self, client):
        """_estimate_swap_gas raises ValueError when account is None."""
        client.account = None
        with pytest.raises(ValueError, match="Account is required for gas estimation"):
            await client._estimate_swap_gas(MagicMock(), (), b"")

    # ------------------------------------------------------------------
    # Flat firm-quote shape (signature/order at top level) — see docs/simple-swap.md
    # ------------------------------------------------------------------

    async def test_execute_rfq_swap_flat_firm_quote_succeeds(self, client):
        """execute_rfq_swap completes a swap given a flat firm-quote dict."""
        quote = {
            "signature": "0xabcd",
            "order": {
                "nonceAndMeta": 7,
                "expiry": 9999999999,
                "makerAsset": "0x0000000000000000000000000000000000000001",
                "takerAsset": "0x0000000000000000000000000000000000000002",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1000,
                "takerAmount": 2000,
            },
            "tx": {
                "to": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                "data": "0x",
                "gasLimit": 120000,
            },
        }

        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={
                "from": client.account.address,
                "nonce": 0,
                "gas": 120000,
                "gasPrice": 100,
            }
        )
        client._get_rfq_contract = AsyncMock(return_value=(client.w3_l1, mock_contract))
        client.w3_l1.to_hex = lambda x: "0xHash"

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            if method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert res.success
        assert res.data["tx_hash"] == "0xHash"
        # Order tuple matches the inner order dict.
        args = mock_contract.functions.simpleSwap.call_args[0]
        assert args[0] == (
            7,
            9999999999,
            "0x0000000000000000000000000000000000000001",
            "0x0000000000000000000000000000000000000002",
            "0x0000000000000000000000000000000000000003",
            "0x0000000000000000000000000000000000000004",
            1000,
            2000,
        )

    async def test_execute_rfq_swap_missing_signature_returns_fail(self, client):
        """execute_rfq_swap fails with the documented message when signature is absent."""
        result = await client.execute_rfq_swap({"order": {"a": 1}})
        assert not result.success
        assert result.error == "Invalid firm quote: missing 'signature' or 'order' field."

    async def test_execute_rfq_swap_missing_order_returns_fail(self, client):
        """execute_rfq_swap fails with the documented message when order is absent."""
        result = await client.execute_rfq_swap({"signature": "0xSig"})
        assert not result.success
        assert result.error == "Invalid firm quote: missing 'signature' or 'order' field."

    # ------------------------------------------------------------------
    # Envelope unwrap — RFQ HTTP returns {"success": true, "quote": {...}}
    # ------------------------------------------------------------------

    def test_transform_quote_from_api_unwraps_envelope(self, client):
        """The outer ``{"success": true, "quote": {...}}`` envelope is unwrapped."""
        envelope = {
            "success": True,
            "quote": {
                "chainId": 43114,
                "signature": "0xSig",
                "order": {
                    "nonceAndMeta": 1,
                    "makerAsset": "0xM",
                    "takerAsset": "0xT",
                    "makerAmount": 100,
                    "takerAmount": 200,
                },
            },
        }

        transformed = client._transform_quote_from_api(envelope)

        # Inner-dict fields hoisted to top level.
        assert transformed["signature"] == "0xSig"
        assert transformed["chain_id"] == 43114
        assert transformed["order"]["nonce_and_meta"] == 1
        assert transformed["order"]["maker_asset"] == "0xM"
        # Envelope keys are gone.
        assert "success" not in transformed
        assert "quote" not in transformed

    def test_transform_quote_from_api_passthrough_when_no_envelope(self, client):
        """An already-inner dict (no ``quote`` key) is processed in place."""
        inner = {
            "chainId": 43114,
            "signature": "0xSig",
            "order": {"nonceAndMeta": 1, "makerAsset": "0xM"},
        }

        transformed = client._transform_quote_from_api(inner)

        # Snake_case aliases applied.
        assert transformed["chain_id"] == 43114
        assert transformed["order"]["nonce_and_meta"] == 1
        assert transformed["order"]["maker_asset"] == "0xM"
        # Original keys preserved (no envelope to strip).
        assert transformed["signature"] == "0xSig"
        assert transformed["chainId"] == 43114

    async def test_get_swap_firm_quote_returns_failure_when_api_says_success_false(self, client):
        """HTTP 200 with ``success: false`` becomes Result.fail at the HTTP layer."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"success": False, "reason": "Insufficient liquidity"}
        )
        mock_resp.text = AsyncMock(return_value="")
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_cm.__aexit__.return_value = None
        client._mock_session.get.return_value = mock_cm

        client.rfq_pairs[43114] = {"AVAX/USDC": {}}
        result = await client.get_swap_firm_quote("AVAX", "USDC", 1.0, chain_id=43114)

        assert not result.success
        assert "Insufficient liquidity" in result.error
        assert "Cannot execute failed quote" in result.error

    async def test_get_swap_firm_quote_envelope_failure_falls_back_to_error_field(self, client):
        """If ``reason`` is absent, fall back to ``error``, then a default message."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"success": False, "error": "MM offline"})
        mock_resp.text = AsyncMock(return_value="")
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_resp
        mock_cm.__aexit__.return_value = None
        client._mock_session.get.return_value = mock_cm

        client.rfq_pairs[43114] = {"AVAX/USDC": {}}
        result = await client.get_swap_firm_quote("AVAX", "USDC", 1.0, chain_id=43114)

        assert not result.success
        assert "MM offline" in result.error

        # Now neither reason nor error — default message.
        mock_resp.json = AsyncMock(return_value={"success": False})
        result = await client.get_swap_firm_quote("AVAX", "USDC", 1.0, chain_id=43114)
        assert not result.success
        assert "Quote API returned success=false" in result.error

    async def test_execute_rfq_swap_handles_envelope_wrapped_response(self, client):
        """execute_rfq_swap unwraps the envelope when handed the raw API payload."""
        envelope = {
            "success": True,
            "quote": {
                "signature": "0xabcd",
                "order": {
                    "nonceAndMeta": 7,
                    "expiry": 9999999999,
                    "makerAsset": "0x0000000000000000000000000000000000000001",
                    "takerAsset": "0x0000000000000000000000000000000000000002",
                    "maker": "0x0000000000000000000000000000000000000003",
                    "taker": "0x0000000000000000000000000000000000000004",
                    "makerAmount": 1000,
                    "takerAmount": 2000,
                },
                "tx": {
                    "to": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                    "data": "0x",
                    "gasLimit": 120000,
                },
            },
        }

        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={
                "from": client.account.address,
                "nonce": 0,
                "gas": 120000,
                "gasPrice": 100,
            }
        )
        client._get_rfq_contract = AsyncMock(return_value=(client.w3_l1, mock_contract))
        client.w3_l1.to_hex = lambda x: "0xHash"

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            if method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(envelope)
        assert res.success
        assert res.data["tx_hash"] == "0xHash"
        # Order tuple was extracted from inner dict.
        args = mock_contract.functions.simpleSwap.call_args[0]
        assert args[0] == (
            7,
            9999999999,
            "0x0000000000000000000000000000000000000001",
            "0x0000000000000000000000000000000000000002",
            "0x0000000000000000000000000000000000000003",
            "0x0000000000000000000000000000000000000004",
            1000,
            2000,
        )

    def test_to_int_accepts_hex_string(self, client):
        """``_to_int`` parses a 0x-prefixed hex string."""
        assert client._to_int("0x10") == 16
        assert client._to_int("0xff") == 255

    def test_to_int_accepts_decimal_string(self, client):
        """``_to_int`` parses a decimal string."""
        assert client._to_int("42") == 42
        assert client._to_int("1000000000000000000") == 10**18

    def test_to_int_handles_none_and_empty(self, client):
        """``_to_int`` returns 0 for None / empty string."""
        assert client._to_int(None) == 0
        assert client._to_int("") == 0

    def test_to_int_passes_through_int(self, client):
        """``_to_int`` returns an int input unchanged."""
        assert client._to_int(99) == 99

    async def test_execute_rfq_swap_signature_without_0x_prefix(self, client):
        """``execute_rfq_swap`` accepts a signature hex string without 0x prefix."""
        quote = {
            "success": True,
            "signature": "abcd",
            "order": {
                "nonceAndMeta": 1,
                "expiry": 9999999999,
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1,
                "takerAmount": 1,
            },
        }
        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={
                "from": client.account.address,
                "nonce": 0,
                "gas": 120000,
                "gasPrice": 100,
            }
        )
        client._get_rfq_contract = AsyncMock(return_value=(client.w3_l1, mock_contract))
        client.w3_l1.to_hex = lambda x: "0xHash"

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            if method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert res.success
        assert mock_contract.functions.simpleSwap.call_args[0][1] == b"\xab\xcd"

    # ------------------------------------------------------------------
    # _get_rfq_contract chain routing — RFQ executes on the connected
    # chain (e.g. Avalanche C-Chain), not on Dexalot L1.
    # ------------------------------------------------------------------

    async def test_get_rfq_contract_uses_connected_chain_not_l1(self, client):
        """_get_rfq_contract returns the connected-chain provider for the
        target chain_id, never w3_l1."""
        avax_provider = MagicMock(name="avax_provider")
        l1_provider = MagicMock(name="l1_provider")
        client.connected_chain_providers = {"Avalanche": avax_provider}
        client.w3_l1 = l1_provider
        client.deployments = {
            "MainnetRFQ": {
                "Avalanche": {
                    "address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                    "abi": [],
                }
            }
        }

        w3, contract = await client._get_rfq_contract(chain_id=43114)

        assert w3 is avax_provider
        assert w3 is not l1_provider
        avax_provider.eth.contract.assert_called_once()

    async def test_get_rfq_contract_returns_none_when_no_connected_provider_for_chain(self, client):
        """When the target chain_id has no connected provider, return (None, None)
        rather than silently falling back to L1."""
        client.connected_chain_providers = {}  # No providers at all
        client.deployments = {
            "MainnetRFQ": {
                "Avalanche": {
                    "address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                    "abi": [],
                }
            }
        }

        w3, contract = await client._get_rfq_contract(chain_id=43114)

        assert w3 is None
        assert contract is None

    async def test_get_rfq_contract_falls_back_to_self_chain_id_when_arg_none(self, client):
        """If chain_id arg is None, fall back to self.chain_id; if that is
        also None, return (None, None)."""
        client.chain_id = None
        client.deployments = {
            "MainnetRFQ": {
                "Avalanche": {
                    "address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                    "abi": [],
                }
            }
        }

        w3, contract = await client._get_rfq_contract(chain_id=None)

        assert w3 is None
        assert contract is None

    async def test_get_rfq_contract_supports_chain_id_keyed_deployments(self, client):
        """Deployments keyed directly by chain_id (int or str) resolve through
        the connected-chain provider just like name-keyed entries."""
        avax_provider = MagicMock(name="avax_provider")
        client.connected_chain_providers = {"Avalanche": avax_provider}
        # int-keyed deployment
        client.deployments = {
            "MainnetRFQ": {
                43114: {
                    "address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                    "abi": [],
                }
            }
        }
        w3, contract = await client._get_rfq_contract(chain_id=43114)
        assert w3 is avax_provider
        assert contract is not None

        # str-keyed deployment (same chain_id, different key form)
        client.deployments = {
            "MainnetRFQ": {
                "43114": {
                    "address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                    "abi": [],
                }
            }
        }
        w3, contract = await client._get_rfq_contract(chain_id=43114)
        assert w3 is avax_provider
        assert contract is not None

    async def test_get_rfq_contract_returns_none_when_deployment_lacks_address(self, client):
        """A deployment entry without an 'address' key is treated as missing."""
        avax_provider = MagicMock(name="avax_provider")
        client.connected_chain_providers = {"Avalanche": avax_provider}
        client.deployments = {"MainnetRFQ": {"Avalanche": {"abi": []}}}

        w3, contract = await client._get_rfq_contract(chain_id=43114)

        assert w3 is None
        assert contract is None

    async def test_execute_rfq_swap_routes_to_chain_id_from_quote(self, client):
        """execute_rfq_swap forwards quote['chain_id'] to _get_rfq_contract so
        the swap targets the connected chain, not L1."""
        avax_provider = MagicMock(name="avax_provider")
        l1_provider = MagicMock(name="l1_provider")
        client.connected_chain_providers = {"Avalanche": avax_provider}
        client.w3_l1 = l1_provider
        client.deployments = {
            "MainnetRFQ": {
                "Avalanche": {
                    "address": "0xeed3c159f3a96ab8d41c8b9ca49ee1e5071a7cdd",
                    "abi": [],
                }
            }
        }

        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={
                "from": client.account.address,
                "nonce": 0,
                "gas": 120000,
                "gasPrice": 100,
            }
        )
        avax_provider.eth.contract.return_value = mock_contract
        avax_provider.to_hex = lambda x: "0xHash"

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            if method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        quote = {
            "success": True,
            "chain_id": 43114,
            "signature": "0xabcd",
            "order": {
                "nonceAndMeta": 1,
                "expiry": 9999999999,
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 1,
                "takerAmount": 1,
            },
        }

        res = await client.execute_rfq_swap(quote)

        assert res.success
        # Contract was created against the Avalanche provider, not L1.
        avax_provider.eth.contract.assert_called_once()
        l1_provider.eth.contract.assert_not_called()

    # ------------------------------------------------------------------
    # _compute_msg_value — MainnetRFQ requires msg.value == takerAmount
    # for native sells (takerAsset == zero address) and 0 otherwise.
    # ------------------------------------------------------------------

    def test_compute_msg_value_native_taker(self, client):
        """Zero-address takerAsset returns takerAmount as int (lowercase + checksum)."""
        # Lowercase zero address.
        order = {
            "takerAsset": "0x0000000000000000000000000000000000000000",
            "takerAmount": "1000000000000000000",
        }
        assert client._compute_msg_value(order) == 10**18

        # Checksummed (mixed-case API output) zero address — case-insensitive match.
        order_checksum = {
            "takerAsset": Web3.to_checksum_address("0x" + "0" * 40),
            "takerAmount": 5,
        }
        assert client._compute_msg_value(order_checksum) == 5

    def test_compute_msg_value_erc20_taker(self, client):
        """Non-zero takerAsset returns 0 regardless of takerAmount."""
        order = {
            "takerAsset": "0x2222222222222222222222222222222222222222",
            "takerAmount": "999",
        }
        assert client._compute_msg_value(order) == 0

    def test_compute_msg_value_handles_camelcase_and_snake_case(self, client):
        """Helper accepts both takerAsset/takerAmount and taker_asset/taker_amount."""
        camel = {
            "takerAsset": "0x" + "0" * 40,
            "takerAmount": 7,
        }
        snake = {
            "taker_asset": "0x" + "0" * 40,
            "taker_amount": 7,
        }
        assert client._compute_msg_value(camel) == 7
        assert client._compute_msg_value(snake) == 7

    async def test_execute_rfq_swap_passes_value_for_native_taker(self, client):
        """Native sell (taker_asset == 0x0) sets value=takerAmount on both calls."""
        quote = {
            "success": True,
            "signature": "0xabcd",
            "order": {
                "nonceAndMeta": 1,
                "expiry": 9999999999,
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x0000000000000000000000000000000000000000",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 2000,
                "takerAmount": 10**18,
            },
        }

        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={"to": "0xeed3", "data": "0x"}
        )
        client._get_rfq_contract = AsyncMock(return_value=(client.w3_l1, mock_contract))
        client.w3_l1.to_hex = lambda x: "0xHash"

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            if method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert res.success

        estimate_kwargs = mock_contract.functions.simpleSwap.return_value.estimate_gas.call_args[0][
            0
        ]
        assert estimate_kwargs["value"] == 10**18

        build_kwargs = mock_contract.functions.simpleSwap.return_value.build_transaction.call_args[
            0
        ][0]
        assert build_kwargs["value"] == 10**18

    async def test_execute_rfq_swap_passes_zero_value_for_erc20_taker(self, client):
        """ERC20 sell (non-zero taker_asset) sets value=0 on both calls."""
        quote = {
            "success": True,
            "signature": "0xabcd",
            "order": {
                "nonceAndMeta": 1,
                "expiry": 9999999999,
                "makerAsset": "0x1111111111111111111111111111111111111111",
                "takerAsset": "0x2222222222222222222222222222222222222222",
                "maker": "0x0000000000000000000000000000000000000003",
                "taker": "0x0000000000000000000000000000000000000004",
                "makerAmount": 2000,
                "takerAmount": 10**6,
            },
        }

        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={"to": "0xeed3", "data": "0x"}
        )
        client._get_rfq_contract = AsyncMock(return_value=(client.w3_l1, mock_contract))
        client.w3_l1.to_hex = lambda x: "0xHash"

        async def mock_rpc_call(w3, method, *args):
            if method == "eth.wait_for_transaction_receipt":
                return {"status": 1}
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            if method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert res.success

        estimate_kwargs = mock_contract.functions.simpleSwap.return_value.estimate_gas.call_args[0][
            0
        ]
        assert estimate_kwargs["value"] == 0

        build_kwargs = mock_contract.functions.simpleSwap.return_value.build_transaction.call_args[
            0
        ][0]
        assert build_kwargs["value"] == 0
