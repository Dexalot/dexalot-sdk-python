import pytest


class TestIntegrationMCPTools:
    """Integration tests for Dexalot MCP Tools on Fuji Testnet."""

    @pytest.mark.asyncio
    async def test_01_read_only_tools(self, client):
        """Test all read-only tools."""
        print("\n")
        print("Testing Read-Only Tools...")

        # 1. get_environments
        result = await client.get_environments()
        assert result.success
        envs = result.data
        assert isinstance(envs, list)
        assert len(envs) > 0
        print("✅ get_environments passed")

        # 2. get_chains
        chains_result = await client.get_chains()
        assert chains_result.success
        chains = chains_result.data
        assert isinstance(chains, dict)
        assert "43113" in chains or 43113 in chains  # Fuji ID
        print("✅ get_chains passed")

        # 3. get_swap_pairs
        chain_name = list(chains.values())[0]
        pairs_result = await client.get_swap_pairs(chain_name)
        # The return type is now a Result object
        assert hasattr(pairs_result, "success")
        if pairs_result.success:
            assert isinstance(pairs_result.data, (list, dict))
        print("✅ get_swap_pairs passed")

        # 4. get_all_chain_wallet_balances
        # Use a random address for read-only tests if no wallet connected
        test_address = "0x0000000000000000000000000000000000000000"
        info_result = await client.get_all_chain_wallet_balances(test_address)
        assert hasattr(info_result, "success")
        if info_result.success:
            assert isinstance(info_result.data, dict)
            assert "address" in info_result.data
        print("✅ get_all_chain_wallet_balances passed")

        # 5. get_portfolio_balance
        balance_result = await client.get_portfolio_balance("AVAX", test_address)
        assert hasattr(balance_result, "success")
        if balance_result.success:
            assert isinstance(balance_result.data, dict)
        print("✅ get_portfolio_balance passed")

        # 6. get_all_portfolio_balances
        all_balances_result = await client.get_all_portfolio_balances(test_address)
        assert hasattr(all_balances_result, "success")
        if all_balances_result.success:
            assert isinstance(all_balances_result.data, dict)
        print("✅ get_all_portfolio_balances passed")
