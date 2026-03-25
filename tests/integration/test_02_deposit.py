import time

import pytest

from tests.integration.conftest import wait_for_balance_change


class TestDeposit:
    @pytest.mark.asyncio
    async def test_02_deposit(self, client):
        """Test depositing assets from Fuji to Dexalot L1."""
        print("Testing Deposit...")

        # Check if client has account configured
        if not client.account:
            pytest.skip(
                "No account/signer configured. Deposit tests require PRIVATE_KEY in .env file."
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

        # Deposit 2 AVAX
        res_avax = await client.deposit("AVAX", 2.0, "Fuji")
        assert res_avax.success, f"Deposit failed: {res_avax.error}"
        assert "Deposit transaction sent" in res_avax.data
        print(f"Deposited 2 AVAX: {res_avax.data}")

        # Deposit 20 ALOT
        res_alot = await client.deposit("ALOT", 20.0, "Fuji")
        assert res_alot.success, f"Deposit failed: {res_alot.error}"
        assert "Deposit transaction sent" in res_alot.data
        print(f"Deposited 20 ALOT: {res_alot.data}")

        # Deposit 20 USDC
        res_usdc = await client.deposit("USDC", 20.0, "Fuji")
        assert res_usdc.success, f"Deposit failed: {res_usdc.error}"
        assert "Deposit transaction sent" in res_usdc.data
        print(f"Deposited 20 USDC: {res_usdc.data}")

        # Wait for updates (simplified wait, ideally check specific amounts)
        print("Waiting for deposits to finalize...")
        time.sleep(15)  # Fuji is fast, but give it a moment

        # Verify
        final_avax = await wait_for_balance_change(client, "AVAX", init_avax, 2.0)
        final_alot = await wait_for_balance_change(client, "ALOT", init_alot, 20.0)
        final_usdc = await wait_for_balance_change(client, "USDC", init_usdc, 20.0)

        assert final_avax is not None, "AVAX deposit failed to update balance"
        assert final_alot is not None, "ALOT deposit failed to update balance"
        assert final_usdc is not None, "USDC deposit failed to update balance"
        print("✅ Deposit verification passed")
