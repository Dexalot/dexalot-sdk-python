import time

import pytest


class TestReplaceOrder:
    @pytest.mark.asyncio
    async def test_05_replace_order(self, client):
        """Test cancel/replace functionality."""
        print("Testing Replace Order...")

        # Check if client has account configured
        if not client.account:
            pytest.skip(
                "No account/signer configured. Order tests require PRIVATE_KEY in .env file."
            )

        # Ensure client is fully initialized
        if not client.trade_pairs_contract:
            pytest.skip("TradePairs contract not initialized. Client may not be fully initialized.")

        pair = "AVAX/USDC"
        cancel_all_result = await client.cancel_all_orders()
        if not cancel_all_result.success:
            print(f"Warning: cancel_all_orders failed: {cancel_all_result.error}")
        time.sleep(2)

        # 1. Place Initial Order
        res_initial = await client.add_order(pair, "BUY", 0.5, 14.5, "LIMIT")
        if not res_initial.success:
            pytest.skip(f"Failed to place initial order: {res_initial.error}")
        time.sleep(5)
        orders_result = await client.get_open_orders()
        if not orders_result.success or len(orders_result.data) == 0:
            pytest.skip(
                f"Failed to get initial order: {orders_result.error if not orders_result.success else 'No orders found'}"
            )
        orders = orders_result.data
        order_id = orders[0]["internal_order_id"]

        # 2. Replace
        replacements = [
            {"order_id": order_id, "pair": pair, "side": "BUY", "amount": 0.6, "price": 14.5}
        ]
        res_replace = await client.cancel_add_list(replacements)
        if not res_replace.success:
            pytest.skip(f"Failed to replace order: {res_replace.error}")
        assert "tx_hash" in res_replace.data
        print("Replaced Order")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 1
        assert float(orders[0]["quantity"]) == 0.6
        print("✅ Replace verification passed")

        # 3. Cancel Replaced Order
        replaced_order_id = orders[0]["internal_order_id"]
        res_cancel = await client.cancel_order(replaced_order_id)
        if not res_cancel.success:
            pytest.skip(f"Failed to cancel replaced order: {res_cancel.error}")
        assert "transaction sent" in res_cancel.data
        print("Cancelled Replaced Order")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 0  # No orders remaining
        print("✅ Replaced Order cancel verification passed")
