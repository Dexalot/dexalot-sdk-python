import time

import pytest

from tests.integration.conftest import wait_for_balance_change


class TestWithdraw:
    @pytest.mark.asyncio
    async def test_03_withdraw(self, client):
        """Test withdrawing assets from Dexalot L1 to Fuji."""
        print("Testing Withdraw...")

        # Check if client has account configured
        if not client.account:
            pytest.skip(
                "No account/signer configured. Withdraw tests require PRIVATE_KEY in .env file."
            )

        # Initial Balances
        balance_avax_result = await client.get_portfolio_balance("AVAX")
        balance_alot_result = await client.get_portfolio_balance("ALOT")
        balance_usdc_result = await client.get_portfolio_balance("USDC")

        # Check for errors and print them for debugging
        if not balance_avax_result.success:
            print(f"AVAX balance error: {balance_avax_result.error}")
            pytest.skip(f"AVAX balance check failed: {balance_avax_result.error}")
        if not balance_alot_result.success:
            print(f"ALOT balance error: {balance_alot_result.error}")
            pytest.skip(f"ALOT balance check failed: {balance_alot_result.error}")
        if not balance_usdc_result.success:
            print(f"USDC balance error: {balance_usdc_result.error}")
            pytest.skip(f"USDC balance check failed: {balance_usdc_result.error}")

        balance_avax = balance_avax_result.data
        balance_alot = balance_alot_result.data
        balance_usdc = balance_usdc_result.data

        init_avax = balance_avax["total"]
        init_alot = balance_alot["total"]
        init_usdc = balance_usdc["total"]

        # Withdraw 1 AVAX
        res_avax = await client.withdraw("AVAX", 1.0, "Fuji")
        assert res_avax.success, f"Withdraw failed: {res_avax.error}"
        assert "Withdraw transaction sent" in res_avax.data
        print(f"Withdrew 1 AVAX: {res_avax.data}")

        # Withdraw 10 ALOT
        res_alot = await client.withdraw("ALOT", 10.0, "Fuji")
        assert res_alot.success, f"Withdraw failed: {res_alot.error}"
        assert "Withdraw transaction sent" in res_alot.data
        print(f"Withdrew 10 ALOT: {res_alot.data}")

        # Withdraw 10 USDC
        res_usdc = await client.withdraw("USDC", 10.0, "Fuji")
        assert res_usdc.success, f"Withdraw failed: {res_usdc.error}"
        assert "Withdraw transaction sent" in res_usdc.data
        print(f"Withdrew 10 USDC: {res_usdc.data}")

        print("Waiting for withdrawals to finalize...")
        time.sleep(15)

        # Verify
        final_avax = await wait_for_balance_change(client, "AVAX", init_avax, -1.0)
        final_alot = await wait_for_balance_change(client, "ALOT", init_alot, -10.0)
        final_usdc = await wait_for_balance_change(client, "USDC", init_usdc, -10.0)

        assert final_avax is not None, "AVAX withdraw failed to update balance"
        assert final_alot is not None, "ALOT withdraw failed to update balance"
        assert final_usdc is not None, "USDC withdraw failed to update balance"
        print("✅ Withdraw verification passed")
