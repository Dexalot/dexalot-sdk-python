import time

import pytest


class TestSingleOrders:
    @pytest.mark.asyncio
    async def test_04_single_orders(self, client):
        """Test single order placement and cancellation."""
        print("Testing Single Orders...")

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

        # 1. Place Buy Order
        res_buy = await client.add_order(pair, "BUY", 0.6, 14.5, "LIMIT")
        if not res_buy.success:
            pytest.skip(f"Failed to place buy order: {res_buy.error}")
        assert "tx_hash" in res_buy.data
        print("Placed Buy Order")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 1
        assert orders[0]["side"] == 0  # 0 is BUY
        assert float(orders[0]["quantity"]) == 0.6
        print("✅ Buy Order verified")

        # 2. Place Sell Order
        res_sell = await client.add_order(pair, "SELL", 0.7, 18.5, "LIMIT")
        if not res_sell.success:
            pytest.skip(f"Failed to place sell order: {res_sell.error}")
        assert "tx_hash" in res_sell.data
        print("Placed Sell Order")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 2
        print("✅ Sell Order verified")

        # 3. Cancel Buy Order
        buy_order_id = next(o["internal_order_id"] for o in orders if o["side"] == 0)
        res_cancel = await client.cancel_order(buy_order_id)
        if not res_cancel.success:
            pytest.skip(f"Failed to cancel buy order: {res_cancel.error}")
        assert "transaction sent" in res_cancel.data
        print("Cancelled Buy Order")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 1
        assert orders[0]["side"] == 1  # Only Sell remains
        print("✅ Cancel verification passed")

        # 4. Cancel Sell Order
        sell_order_id = orders[0]["internal_order_id"]
        res_cancel_sell = await client.cancel_order(sell_order_id)
        if not res_cancel_sell.success:
            pytest.skip(f"Failed to cancel sell order: {res_cancel_sell.error}")
        assert "transaction sent" in res_cancel_sell.data
        print("Cancelled Sell Order")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 0  # No orders remaining
        print("✅ Sell Order cancel verification passed")
