import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
                client.deployments = {"MainnetRFQ": {"Avalanche": {"address": "0xRFQ", "abi": []}}}
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
        assert "Could not resolve" in result.error

        # For chain_id 999, mock API to return error
        def side_effect_999(url, params=None, **kwargs):
            mock_resp = AsyncMock()

            # Make raise_for_status raise an exception when called
            def raise_error():
                raise Exception("Chain not found")

            mock_resp.raise_for_status = raise_error
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

        # Set side_effect for the 999 test
        client._mock_session.get.side_effect = side_effect_999
        result = await client.get_swap_pairs(999)
        assert not result.success
        assert "No swap pairs found" in result.error or "Failed to fetch RFQ pairs" in result.error

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

    async def test_transform_quote_from_api(self, client):
        """Test _transform_quote_from_api transforms field names to snake_case."""
        # Test lowercase fields
        quote1 = {
            "chainid": 43114,
            "securequote": {
                "signature": "0xSig",
                "data": {
                    "nonceAndMeta": 1,
                    "expiry": 1,
                    "makerAsset": "0xM",
                    "takerAsset": "0xT",
                    "maker": "0xMkr",
                    "taker": "0xTkr",
                    "makerAmount": 100,
                    "takerAmount": 200,
                },
            },
            "quoteid": "q123",
        }

        transformed1 = client._transform_quote_from_api(quote1)
        assert transformed1["chain_id"] == 43114
        assert transformed1["secure_quote"] is not None
        assert transformed1["secure_quote"]["signature"] == "0xSig"
        assert transformed1["secure_quote"]["data"]["nonce_and_meta"] == 1
        assert transformed1["secure_quote"]["data"]["maker_asset"] == "0xM"
        assert transformed1["secure_quote"]["data"]["taker_asset"] == "0xT"
        assert transformed1["secure_quote"]["data"]["maker_amount"] == 100
        assert transformed1["secure_quote"]["data"]["taker_amount"] == 200
        assert transformed1["quote_id"] == "q123"

        # Test preference for existing snake_case fields
        quote2 = {
            "chain_id": 43114,
            "secure_quote": {
                "signature": "0xSig",
                "data": {
                    "nonce_and_meta": 1,
                    "maker_asset": "0xM",
                    "taker_asset": "0xT",
                    "maker_amount": 100,
                    "taker_amount": 200,
                },
            },
            "quote_id": "q123",
            "chainid": 999,  # Should be ignored
            "securequote": {},  # Should be ignored
            "quoteid": "ignored",  # Should be ignored
        }

        transformed2 = client._transform_quote_from_api(quote2)
        assert transformed2["chain_id"] == 43114  # Prefer existing
        assert transformed2["quote_id"] == "q123"  # Prefer existing
        assert transformed2["secure_quote"]["data"]["nonce_and_meta"] == 1  # Prefer existing

        # Test secureQuote (camelCase) field
        quote3 = {
            "chainid": 43114,
            "secureQuote": {
                "signature": "0xSig",
                "data": {
                    "nonceAndMeta": 1,
                },
            },
        }

        transformed3 = client._transform_quote_from_api(quote3)
        assert transformed3["secure_quote"] is not None
        assert transformed3["secure_quote"]["signature"] == "0xSig"
        assert transformed3["secure_quote"]["data"]["nonce_and_meta"] == 1

        # Test order field (legacy)
        quote4 = {
            "chainid": 43114,
            "securequote": {
                "signature": "0xSig",
                "order": {
                    "nonceAndMeta": 1,
                    "makerAsset": "0xM",
                },
            },
        }

        transformed4 = client._transform_quote_from_api(quote4)
        assert transformed4["secure_quote"]["order"] is not None
        assert transformed4["secure_quote"]["order"]["nonce_and_meta"] == 1
        assert transformed4["secure_quote"]["order"]["maker_asset"] == "0xM"

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
        client.account.address = "0xUser"

        await client.get_swap_firm_quote("AVAX", "USDC", 1.0, chain_id=43114)
        call_args = client._mock_session.get.call_args
        assert call_args[1]["params"]["address"] == "0xUser"
        assert "firm" in call_args[0][0]

    async def test_get_swap_quote_transforms_fields(self, client):
        """Test that get_swap_soft_quote transforms quote fields from API response."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "chainid": 43114,
                "securequote": {
                    "signature": "0xSig",
                    "data": {
                        "nonceAndMeta": 1,
                        "makerAsset": "0xM",
                    },
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
        assert result.data["secure_quote"] is not None
        assert result.data["secure_quote"]["data"]["nonce_and_meta"] == 1
        assert result.data["secure_quote"]["data"]["maker_asset"] == "0xM"

    async def test_execute_rfq_swap(self, client):
        """Test execute_rfq_swap."""
        quote = {
            "success": True,
            "securequote": {
                "signature": "0x1234",
                "data": {
                    "nonceAndMeta": 123,
                    "expiry": 9999999999,
                    "makerAsset": "0xToken",
                    "takerAsset": "0xTokenOut",
                    "maker": "0xMaker",
                    "taker": "0xTaker",
                    "makerAmount": 1000,
                    "takerAmount": 2000,
                },
            },
        }

        mock_w3 = client.w3_l1
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
        assert "Swap transaction confirmed" in res.data

        # Verify contract call
        # simpleSwap((tuple), signature)
        expected_tuple = (
            123,
            9999999999,
            "0xToken",
            "0xTokenOut",
            "0xMaker",
            "0xTaker",
            1000,
            2000,
        )
        # Check call args manually to avoid bytes representation issues
        args = mock_contract.functions.simpleSwap.call_args[0]
        assert args[0] == expected_tuple
        assert args[1] == b"\x12\x34"

    async def test_execute_rfq_swap_with_transformed_fields(self, client):
        """Test execute_rfq_swap handles transformed field names."""
        # Quote with lowercase/camelCase fields that need transformation
        quote = {
            "chainid": 43114,
            "securequote": {
                "signature": "0x1234",
                "data": {
                    "nonceAndMeta": 123,
                    "expiry": 9999999999,
                    "makerAsset": "0xToken",
                    "takerAsset": "0xTokenOut",
                    "maker": "0xMaker",
                    "taker": "0xTaker",
                    "makerAmount": 1000,
                    "takerAmount": 2000,
                },
            },
        }

        mock_w3 = client.w3_l1
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

        mock_contract = MagicMock()
        mock_function_call = MagicMock()
        mock_function_call.estimate_gas = AsyncMock(return_value=100000)
        mock_function_call.build_transaction = AsyncMock(return_value={})
        mock_contract.functions.simpleSwap.return_value = mock_function_call
        mock_w3.eth.contract.return_value = mock_contract

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
        assert "Swap transaction confirmed" in res.data

        # Verify contract call uses transformed field names
        expected_tuple = (
            123,
            9999999999,
            "0xToken",
            "0xTokenOut",
            "0xMaker",
            "0xTaker",
            1000,
            2000,
        )
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
        """Test execute_rfq_swap with native token."""
        quote = {
            "success": True,
            "securequote": {
                "signature": "0x1234",
                "data": {
                    "nonceAndMeta": 123,
                    "expiry": 9999999999,
                    "makerAsset": "0x0000000000000000000000000000000000000000",
                    "takerAsset": "0xTokenOut",
                    "maker": "0xMaker",
                    "taker": "0xTaker",
                    "makerAmount": 1000,
                    "takerAmount": 2000,
                },
            },
        }

        mock_w3 = client.w3_l1
        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract

        # Capture build_transaction kwargs
        mock_contract.functions.simpleSwap.return_value.build_transaction.side_effect = lambda x: x

        await client.execute_rfq_swap(quote)

        # Note: Current implementation does not set 'value' even for native tokens.
        # So we just verify it runs without error.

    async def test_execute_rfq_swap_errors(self, client):
        """Test execute_rfq_swap errors."""
        client.account = None
        # Now raises ValueError instead of returning string
        with pytest.raises(ValueError, match="Account is required for signing transactions"):
            await client.execute_rfq_swap({})
        client.account = MagicMock()
        client.account.address = "0xUser"

        # Provider missing
        # Ensure data is valid to pass checks
        valid_quote = {"success": True, "securequote": {"signature": "s", "data": {"a": 1}}}
        # So we need to unset it for this test.
        client.w3_l1 = None
        result = await client.execute_rfq_swap(valid_quote)
        assert not result.success
        assert "not initialized" in result.error
        client.w3_l1 = MagicMock()  # Restore w3_l1

        # Contract missing (Empty dict)
        client.deployments["MainnetRFQ"] = {}
        result = await client.execute_rfq_swap(
            {"chainId": 43114, "success": True, "securequote": {"signature": "s", "data": {"a": 1}}}
        )
        assert not result.success
        assert "not initialized" in result.error

        # MainnetRFQ key missing
        if "MainnetRFQ" in client.deployments:
            del client.deployments["MainnetRFQ"]
        result = await client.execute_rfq_swap(
            {"chainId": 43114, "success": True, "securequote": {"signature": "s", "data": {"a": 1}}}
        )
        assert not result.success
        assert "not initialized" in result.error

        # Exception
        client.deployments["MainnetRFQ"] = {"Avalanche": {"address": "0x", "abi": []}}
        # We need to ensure w3.eth.contract doesn't raise, but something inside try block raises
        # Or we fix the code to wrap contract creation in try block.
        # For now, let's mock contract creation to succeed, but function call to fail.
        mock_w3 = client.w3_l1
        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract
        mock_contract.functions.simpleSwap.side_effect = Exception("Err")

        result = await client.execute_rfq_swap(
            {"success": True, "securequote": {"signature": "s", "data": {"a": 1}}}
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
        await client.get_swap_soft_quote("A", "B", 1, chain_id="invalid")
        # Should be "43114" as code uses _resolve_chain_id which returns None if invalid
        # And then defaults to 43114 in _get_swap_quote_base

        call_args = client._mock_session.get.call_args
        assert call_args[1]["params"]["chainid"] == "43114"

        # We need chain_id=43114 but NOT in chain_config map?
        # Or chain_id=43114 and chain_config has it but we want to test fallback?
        # The code iterates chain_config to find name.
        # If we remove 43114 from chain_config, it should hit fallback.

        client.chain_config = {}  # Empty config
        quote = {
            "success": True,
            "securequote": {
                "signature": "0x1234",
                "data": {
                    "nonceAndMeta": 1,
                    "expiry": 1,
                    "makerAsset": "0x",
                    "takerAsset": "0x",
                    "maker": "0x",
                    "taker": "0x",
                    "makerAmount": 1,
                    "takerAmount": 1,
                },
            },
        }

        # Mock provider and contract
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
        client.deployments["MainnetRFQ"]["Avalanche"] = {"address": "0x", "abi": []}

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
        assert "Swap transaction confirmed" in res.data

    async def test_swap_errors(self, client):
        """Test swap client errors and edge cases."""
        quote = {"success": False, "reason": "Bad quote"}
        client.account = MagicMock()
        client.account.address = "0xUser"
        result = await client.execute_rfq_swap(quote)
        assert not result.success
        assert "Cannot execute failed quote" in result.error

        quote = {"success": True}
        result = await client.execute_rfq_swap(quote)
        assert not result.success
        assert "Invalid quote format" in result.error

        quote = {"success": True, "securequote": {}}
        result = await client.execute_rfq_swap(quote)
        assert not result.success
        assert "Invalid secure quote data" in result.error

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
            "securequote": {
                "signature": "0x1234",
                "data": {
                    "nonceAndMeta": 1,
                    "expiry": 9999999999,
                    "makerAsset": "0xMakerAsset",
                    "takerAsset": "0xTakerAsset",
                    "maker": "0xMaker",
                    "taker": "0xTaker",
                    "makerAmount": 1000000,
                    "takerAmount": 2000000,
                },
            },
        }
        successful_quote = Result.ok(quote_data)

        # Mock the contract and transaction flow
        mock_contract = MagicMock()
        mock_contract.functions.simpleSwap.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        mock_contract.functions.simpleSwap.return_value.build_transaction = AsyncMock(
            return_value={"from": "0xUser", "nonce": 0, "gas": 120000, "gasPrice": 100}
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
        assert "Swap transaction confirmed" in result.data

        quote = {
            "success": True,
            "securequote": {
                "signature": b"sig",  # Bytes signature
                "order": {  # 'order' instead of 'data'
                    "makerAsset": "A",
                    "takerAsset": "B",
                    "maker": "M",
                    "taker": "T",
                },
            },
        }
        client.deployments["MainnetRFQ"] = {"Avalanche": {"address": "0xRFQ", "abi": []}}
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
        assert "Swap transaction confirmed" in res.data

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
            "securequote": {
                "order": {
                    "nonceAndMeta": 1,
                    "expiry": 9999999999,
                    "makerAsset": "0xMakerAsset",
                    "takerAsset": "0xTakerAsset",
                    "maker": "0xMaker",
                    "taker": "0xTaker",
                    "makerAmount": 1000000,
                    "takerAmount": 2000000,
                },
                "signature": "0x1234",
            }
        }

        client.rfq_pairs = {43114: {"A/B": {"pair": "A/B"}}}
        client.deployments = {
            "MainnetRFQ": {
                "Avalanche": {
                    "address": "0xRFQ",
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
            return_value={"to": "0xRFQ", "data": "0x"}
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
        assert "Swap transaction confirmed" in res.data

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
    # secure_quote snake_case transform, nonceAndMeta, execute_swap edge paths
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
        """execute_rfq_swap returns fail when the swap transaction receipt has status=0."""
        quote = {
            "success": True,
            "secure_quote": {
                "signature": "0xabcd",
                "data": {
                    "makerAsset": "A",
                    "takerAsset": "B",
                    "maker": "M",
                    "taker": "T",
                    "makerAmount": 1,
                    "takerAmount": 1,
                    "expiry": 9999999999,
                    "nonceAndMeta": 0,
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
                return {"status": 0}  # reverted
            elif method == "eth.send_raw_transaction":
                return b"tx_hash"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote)
        assert not res.success
        assert "Transaction reverted" in res.error

    async def test_execute_rfq_swap_no_wait_for_receipt(self, client):
        """execute_rfq_swap returns 'sent' message when wait_for_receipt=False."""
        quote = {
            "success": True,
            "secure_quote": {
                "signature": "0xabcd",
                "data": {
                    "makerAsset": "A",
                    "takerAsset": "B",
                    "maker": "M",
                    "taker": "T",
                    "makerAmount": 1,
                    "takerAmount": 1,
                    "expiry": 9999999999,
                    "nonceAndMeta": 0,
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
            if method == "eth.send_raw_transaction":
                return b"tx_hash"
            elif method == "eth.gas_price":
                return 100
            return None

        client._rpc_call = AsyncMock(side_effect=mock_rpc_call)

        res = await client.execute_rfq_swap(quote, wait_for_receipt=False)
        assert res.success
        assert "Swap transaction sent" in res.data

    async def test_estimate_swap_gas_no_account_raises(self, client):
        """_estimate_swap_gas raises ValueError when account is None."""
        client.account = None
        with pytest.raises(ValueError, match="Account is required for gas estimation"):
            await client._estimate_swap_gas(MagicMock(), (), b"")
