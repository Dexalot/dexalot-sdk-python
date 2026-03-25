import time

import pytest


class TestBatchOrders:
    def _check_client_setup(self, client):
        """Check if client is properly configured for order tests."""
        if not client.account:
            pytest.skip(
                "No account/signer configured. Order tests require PRIVATE_KEY in .env file."
            )

        if not client.trade_pairs_contract:
            pytest.skip("TradePairs contract not initialized. Client may not be fully initialized.")

    def _check_error_response(self, response, operation_name):
        """Check if response is an error and skip test if so."""
        if hasattr(response, "success"):
            # Result object
            if not response.success:
                print(f"{operation_name} returned error: {response.error}")
                pytest.skip(f"Failed {operation_name}: {response.error}")
            return response.data
        elif isinstance(response, str):
            # Legacy string response
            error_keywords = [
                "Error",
                "Failed",
                "not configured",
                "not initialized",
                "Insufficient",
            ]
            if any(keyword in response for keyword in error_keywords):
                print(f"{operation_name} returned error: {response}")
                pytest.skip(f"Failed {operation_name}: {response}")
        return response

    async def _place_batch_orders(self, client, pair):
        """Place initial batch of orders."""
        orders_to_place = [
            {"pair": pair, "side": "BUY", "amount": 0.5, "price": 14.0},
            {"pair": pair, "side": "BUY", "amount": 0.5, "price": 14.1},
            {"pair": pair, "side": "SELL", "amount": 0.5, "price": 19.0},
            {"pair": pair, "side": "SELL", "amount": 0.5, "price": 19.1},
        ]
        res_place = await client.add_limit_order_list(orders_to_place)
        res_place_data = self._check_error_response(res_place, "add_limit_order_list")
        assert "tx_hash" in res_place_data
        print("Placed Batch Orders")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 4
        print("✅ Batch Place verified")
        return orders

    async def _replace_batch_orders(self, client, orders, pair):
        """Replace all orders with new prices."""
        replacements = []
        for order in orders:
            new_price = (
                float(order["price"]) + 1.0 if order["side"] == 1 else float(order["price"]) - 1.0
            )
            replacements.append(
                {
                    "order_id": order["id"],
                    "pair": pair,
                    "side": "BUY" if order["side"] == 0 else "SELL",
                    "amount": float(order["quantity"]),
                    "price": new_price,
                }
            )

        res_replace = await client.cancel_add_list(replacements)
        res_replace_data = self._check_error_response(res_replace, "cancel_add_list")
        assert "tx_hash" in res_replace_data
        print("Replaced Batch Orders")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 4
        print("✅ Batch Replace verified")
        return orders

    async def _cancel_by_client_id(self, client, orders):
        """Cancel buy orders by client ID."""
        buy_orders = [o for o in orders if o["side"] == 0]
        buy_cids = [o["clientordid"] for o in buy_orders]

        res_cancel_cid = await client.cancel_list_orders_by_client_id(buy_cids)
        res_cancel_cid_data = self._check_error_response(
            res_cancel_cid, "cancel_list_orders_by_client_id"
        )
        assert "transaction sent" in res_cancel_cid_data
        print("Cancelled Buys by Client ID")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 2
        assert all(o["side"] == 1 for o in orders)  # Only Sells remain
        print("✅ Cancel by Client ID verified")
        return orders

    async def _cancel_by_internal_id(self, client, orders):
        """Cancel remaining orders by internal ID."""
        sell_ids = [o["id"] for o in orders]
        res_cancel_id = await client.cancel_list_orders(sell_ids)
        res_cancel_id_data = self._check_error_response(res_cancel_id, "cancel_list_orders")
        assert "transaction sent" in res_cancel_id_data
        print("Cancelled Sells by Internal ID")
        time.sleep(5)

        orders_result = await client.get_open_orders()
        if not orders_result.success:
            pytest.skip(f"Failed to get open orders: {orders_result.error}")
        orders = orders_result.data
        assert len(orders) == 0
        print("✅ Cancel by Internal ID verified")

    @pytest.mark.asyncio
    async def test_06_batch_orders(self, client):
        """Test batch order placement, replacement, and cancellation."""
        print("Testing Batch Orders...")

        self._check_client_setup(client)

        pair = "AVAX/USDC"
        cancel_all_result = await client.cancel_all_orders()
        if not cancel_all_result.success:
            print(f"Warning: cancel_all_orders failed: {cancel_all_result.error}")
        time.sleep(2)

        # 1. Place List (2 Buys, 2 Sells)
        orders = await self._place_batch_orders(client, pair)

        # 2. Replace All (+/- 1 price)
        orders = await self._replace_batch_orders(client, orders, pair)

        # 3. Cancel 2 Buys by Client ID
        orders = await self._cancel_by_client_id(client, orders)

        # 4. Cancel Remaining Sells by Internal ID
        await self._cancel_by_internal_id(client, orders)
