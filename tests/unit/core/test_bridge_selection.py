import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dexalot_sdk.core.transfer import TransferClient


@pytest.fixture
def client():
    # Patch environment to ensure no invalid PRIVATE_KEY is loaded
    with patch.dict(os.environ, {"PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
        with patch("dexalot_sdk.core.config.load_dotenv"):
            client = TransferClient()
            client.account = MagicMock()
            client.account.address = "0xUser"

            # Create async-compatible w3 mocks
            class ConstantAwaitable:
                def __init__(self, val):
                    self.val = val

                def __await__(self):
                    if False:
                        yield
                    return self.val

            w3_l1 = MagicMock()
            w3_l1.eth.get_transaction_count = AsyncMock(return_value=1)
            w3_l1.eth.send_raw_transaction = AsyncMock(return_value=b"tx_hash")
            w3_l1.eth.gas_price = ConstantAwaitable(1000000000)
            w3_l1.to_hex.side_effect = lambda x: f"0x{x.hex()}" if isinstance(x, bytes) else str(x)

            w3_mainnet = MagicMock()
            w3_mainnet.eth.contract = MagicMock()
            w3_mainnet.eth.get_transaction_count = AsyncMock(return_value=1)
            w3_mainnet.eth.send_raw_transaction = AsyncMock(return_value=b"tx_hash")
            w3_mainnet.eth.gas_price = ConstantAwaitable(1000000000)
            w3_mainnet.to_hex.side_effect = (
                lambda x: f"0x{x.hex()}" if isinstance(x, bytes) else str(x)
            )

            client.w3_l1 = w3_l1
            client.w3_mainnet = w3_mainnet
            client.portfolio_main_avax_contract = MagicMock()
            client.portfolio_sub_contract = MagicMock()

            # Mock chain config
            client.chain_config = {
                "Avalanche": {"chain_id": 43114},
                "Fuji": {"chain_id": 43113},
                "GUNZ": {"chain_id": 12345},
                "OtherChain": {"chain_id": 99999},
            }
            client.chain_id = 43113  # Initialize as Fuji for deposit test
            client.subnet_chain_id = 123456

            # Mock token data
            client.token_data = {
                "AVAX": {
                    "env_avax": {"chain_id": 43114, "evmdecimals": 18},
                    "env_fuji": {"chain_id": 43113, "evmdecimals": 18},
                    "env_gunz": {"chain_id": 12345, "evmdecimals": 18},
                    "env_other": {"chain_id": 99999, "evmdecimals": 18},
                }
            }

            client.private_key = "0x" + "a" * 64  # Valid 66-char private key (32 bytes)
            client._parse_revert_reason = MagicMock(return_value="Revert Reason")

            # Mock async contract methods
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
                            m_res.call = AsyncMock(return_value="0xBridge")
                            m_res.fn_name = name
                            m_fn.side_effect = lambda *args, **kwargs: m_res
                            self._methods[name] = m_fn
                        return self._methods[name]

                c.functions = FunctionsMock()
                return c

            client.portfolio_main_avax_contract.functions.portfolioBridge.return_value.call = (
                AsyncMock(return_value="0xBridge")
            )
            client.portfolio_main_avax_contract.functions.depositNative.return_value.fn_name = (
                "depositNative"
            )
            client.portfolio_sub_contract.functions.withdrawToken.return_value.fn_name = (
                "withdrawToken"
            )
            w3_mainnet.eth.contract.side_effect = mock_contract

            yield client


class TestBridgeSelection:
    async def test_deposit_bridge_selection(self, client):
        # Mock contract calls
        client._get_bridge_fee_internal = AsyncMock(return_value=0)

        # 1. Fuji -> Dexalot (Should use ICM = 2)
        res = await client.deposit("AVAX", 1, "Fuji")
        print(f"Result: {res}")
        # Verify call happened
        assert client.portfolio_main_avax_contract.functions.depositNative.called
        call_args = client.portfolio_main_avax_contract.functions.depositNative.call_args
        assert call_args[0][1] == 2  # bridge_id

        # 2. Fuji -> Dexalot with Override (Should use LZ = 0)
        await client.deposit("AVAX", 1, "Fuji", use_layerzero=True)
        call_args = client.portfolio_main_avax_contract.functions.depositNative.call_args
        assert call_args[0][1] == 0  # bridge_id

        # 3. GUNZ -> Dexalot (Should use ICM = 2)
        client.chain_id = 12345  # Update to GUNZ chain ID
        await client.deposit("AVAX", 1, "GUNZ")
        call_args = client.portfolio_main_avax_contract.functions.depositNative.call_args
        assert call_args[0][1] == 2  # bridge_id

        # 4. Other -> Dexalot (Should use LZ = 0)
        client.chain_id = 99999  # Update to OtherChain ID
        await client.deposit("AVAX", 1, "OtherChain")
        call_args = client.portfolio_main_avax_contract.functions.depositNative.call_args
        assert call_args[0][1] == 0  # bridge_id

    async def test_withdraw_bridge_selection(self, client):
        # Mock contract calls
        client.portfolio_sub_contract.functions.withdrawToken.return_value.estimate_gas = AsyncMock(
            return_value=100000
        )
        client.portfolio_sub_contract.functions.withdrawToken.return_value.build_transaction = (
            AsyncMock(return_value={})
        )

        # 1. Dexalot -> Fuji (Should use ICM = 2)
        res = await client.withdraw("AVAX", 1, "Fuji")
        print(f"Result: {res}")
        assert client.portfolio_sub_contract.functions.withdrawToken.called
        call_args = client.portfolio_sub_contract.functions.withdrawToken.call_args
        # withdrawToken(user, symbol, amount, bridge_id, chain_id)
        assert call_args[0][3] == 2  # bridge_id

        # 2. Dexalot -> Fuji with Override (Should use LZ = 0)
        await client.withdraw("AVAX", 1, "Fuji", use_layerzero=True)
        call_args = client.portfolio_sub_contract.functions.withdrawToken.call_args
        assert call_args[0][3] == 0  # bridge_id

        # 3. Dexalot -> GUNZ (Should use ICM = 2)
        await client.withdraw("AVAX", 1, "GUNZ")
        call_args = client.portfolio_sub_contract.functions.withdrawToken.call_args
        assert call_args[0][3] == 2  # bridge_id

        # 4. Dexalot -> Other (Should use LZ = 0)
        await client.withdraw("AVAX", 1, "OtherChain")
        call_args = client.portfolio_sub_contract.functions.withdrawToken.call_args
        assert call_args[0][3] == 0  # bridge_id
