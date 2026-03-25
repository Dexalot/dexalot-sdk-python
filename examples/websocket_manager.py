"""
Dexalot SDK WebSocket Manager Examples

This script demonstrates WebSocket manager usage for persistent connections,
multiple subscriptions, reconnection handling, and heartbeat monitoring.
"""

import asyncio

from dexalot_sdk import DexalotClient
from dexalot_sdk.core.config import DexalotConfig


async def example_basic_subscription(client: DexalotClient):
    """Demonstrate basic WebSocket subscription."""
    print("=" * 60)
    print("Example 1: Basic WebSocket Subscription")
    print("=" * 60)

    # Enable WebSocket manager
    client.config.ws_manager_enabled = True

    print("\nSubscribing to orderbook updates...")

    # Track received messages
    messages_received = []

    def on_orderbook_update(message):
        """Callback for orderbook updates."""
        messages_received.append(message)
        print(
            f"  📊 Orderbook update received: {len(message.get('bids', []))} bids, {len(message.get('asks', []))} asks"
        )

    # Subscribe to orderbook
    try:
        await client.subscribe_to_events(
            topic="OrderBook/AVAX/USDC", callback=on_orderbook_update, is_private=False
        )
        print("✓ Subscribed to OrderBook/AVAX/USDC")

        # Wait for messages
        print("\nWaiting for messages (10 seconds)...")
        await asyncio.sleep(10)

        print(f"\n✓ Received {len(messages_received)} messages")

        # Unsubscribe
        client.unsubscribe_from_events("OrderBook/AVAX/USDC")
        print("✓ Unsubscribed")

    except RuntimeError as e:
        print(f"✗ Error: {e}")
        print("  Make sure WebSocket manager is enabled in config")


async def example_multiple_subscriptions(client: DexalotClient):
    """Demonstrate multiple topic subscriptions."""
    print("\n" + "=" * 60)
    print("Example 2: Multiple Subscriptions")
    print("=" * 60)

    client.config.ws_manager_enabled = True

    print("\nSubscribing to multiple topics...")

    orderbook_updates = []
    execution_updates = []

    def on_orderbook(message):
        orderbook_updates.append(message)
        print(f"  📊 Orderbook: {len(message.get('bids', []))} bids")

    def on_execution(message):
        execution_updates.append(message)
        print(f"  ⚡ Execution: {message.get('status', 'N/A')}")

    try:
        # Subscribe to multiple topics
        await client.subscribe_to_events("OrderBook/AVAX/USDC", on_orderbook, is_private=False)
        await client.subscribe_to_events("Execution", on_execution, is_private=True)

        print("✓ Subscribed to OrderBook/AVAX/USDC and Execution")

        # Wait for messages
        print("\nWaiting for messages (10 seconds)...")
        await asyncio.sleep(10)

        print(f"\n✓ Orderbook updates: {len(orderbook_updates)}")
        print(f"✓ Execution updates: {len(execution_updates)}")

        # Unsubscribe
        client.unsubscribe_from_events("OrderBook/AVAX/USDC")
        client.unsubscribe_from_events("Execution")
        print("✓ Unsubscribed from all topics")

    except RuntimeError as e:
        print(f"✗ Error: {e}")


async def example_private_subscription(client: DexalotClient):
    """Demonstrate private topic subscription (requires wallet)."""
    print("\n" + "=" * 60)
    print("Example 3: Private Topic Subscription")
    print("=" * 60)

    if not client.account:
        print("⚠ No wallet connected - skipping private subscription example")
        print("  Set PRIVATE_KEY environment variable to enable this example")
        return

    client.config.ws_manager_enabled = True

    print("\nSubscribing to private order updates...")

    order_updates = []

    def on_order_update(message):
        order_updates.append(message)
        print(f"  📋 Order update: {message.get('status', 'N/A')}")

    try:
        await client.subscribe_to_events(
            topic="Orders",
            callback=on_order_update,
            is_private=True,  # Requires authentication
        )
        print("✓ Subscribed to Orders (private)")

        # Wait for messages
        print("\nWaiting for order updates (10 seconds)...")
        await asyncio.sleep(10)

        print(f"\n✓ Received {len(order_updates)} order updates")

        client.unsubscribe_from_events("Orders")
        print("✓ Unsubscribed")

    except RuntimeError as e:
        print(f"✗ Error: {e}")


async def example_reconnection_handling(base_client: DexalotClient):
    """Demonstrate automatic reconnection."""
    print("\n" + "=" * 60)
    print("Example 4: Automatic Reconnection")
    print("=" * 60)

    # Configure reconnection settings
    config = DexalotConfig(
        parent_env=base_client.config.parent_env,
        api_base_url=base_client.config.api_base_url,
        ws_manager_enabled=True,
        ws_reconnect_initial_delay=1.0,
        ws_reconnect_max_delay=10.0,
        ws_reconnect_exponential_base=2.0,
        ws_reconnect_max_attempts=5,
    )

    client = DexalotClient(config=config)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"✗ Cannot initialize client: {init_result.error}")
            return

        print("\nWebSocket manager configured with automatic reconnection:")
        print(f"  Initial delay: {config.ws_reconnect_initial_delay}s")
        print(f"  Max delay: {config.ws_reconnect_max_delay}s")
        print(f"  Max attempts: {config.ws_reconnect_max_attempts}")

        reconnection_count = 0

        def on_message(message):
            nonlocal reconnection_count
            # Track reconnections (would be logged by manager)
            pass

        try:
            await client.subscribe_to_events("OrderBook/AVAX/USDC", on_message, is_private=False)
            print("✓ Connected and subscribed")

            # Note: In a real scenario, you would simulate a connection drop
            # The manager automatically handles reconnection with exponential backoff
            print("\nIf connection drops, manager will automatically reconnect")
            print("with exponential backoff (1s, 2s, 4s, 8s, 10s max)")

            await asyncio.sleep(5)
            client.unsubscribe_from_events("OrderBook/AVAX/USDC")

        except RuntimeError as e:
            print(f"✗ Error: {e}")
    finally:
        await client.close()


async def example_heartbeat_monitoring(base_client: DexalotClient):
    """Demonstrate heartbeat/ping-pong monitoring."""
    print("\n" + "=" * 60)
    print("Example 5: Heartbeat Monitoring")
    print("=" * 60)

    # Configure heartbeat settings
    config = DexalotConfig(
        parent_env=base_client.config.parent_env,
        api_base_url=base_client.config.api_base_url,
        ws_manager_enabled=True,
        ws_ping_interval=10,  # Ping every 10 seconds
        ws_ping_timeout=5,  # Wait 5s for pong
    )

    client = DexalotClient(config=config)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"✗ Cannot initialize client: {init_result.error}")
            return

        print("\nWebSocket manager configured with heartbeat:")
        print(f"  Ping interval: {config.ws_ping_interval}s")
        print(f"  Ping timeout: {config.ws_ping_timeout}s")
        print("\nManager automatically sends ping messages and monitors pong responses")
        print("If pong is not received within timeout, connection is closed and reconnected")

        def on_message(message):
            pass

        try:
            await client.subscribe_to_events("OrderBook/AVAX/USDC", on_message, is_private=False)
            print("✓ Connected with heartbeat monitoring")

            # Wait to see heartbeat in action
            print("\nMonitoring connection (15 seconds)...")
            await asyncio.sleep(15)

            client.unsubscribe_from_events("OrderBook/AVAX/USDC")
            print("✓ Disconnected")

        except RuntimeError as e:
            print(f"✗ Error: {e}")
    finally:
        await client.close()


async def example_one_off_connection(client: DexalotClient):
    """Demonstrate one-off WebSocket connection (doesn't use manager)."""
    print("\n" + "=" * 60)
    print("Example 6: One-Off Connection (No Manager)")
    print("=" * 60)

    print("\nUsing listen_to_events() for one-time subscription:")
    print("(This doesn't use the WebSocket manager)")

    messages_received = []

    def on_message(message):
        messages_received.append(message)
        print(f"  📊 Received: {len(message.get('bids', []))} bids")

    try:
        # One-off connection - closes after duration
        print("\nListening for 10 seconds...")
        await client.listen_to_events(
            topic="OrderBook/AVAX/USDC", duration_seconds=10, callback=on_message
        )

        print(f"\n✓ Received {len(messages_received)} messages")
        print("✓ Connection closed automatically")

    except Exception as e:
        print(f"✗ Error: {e}")


async def example_cleanup(client: DexalotClient):
    """Demonstrate proper cleanup."""
    print("\n" + "=" * 60)
    print("Example 7: Proper Cleanup")
    print("=" * 60)

    client.config.ws_manager_enabled = True

    def on_message(message):
        pass

    try:
        await client.subscribe_to_events("OrderBook/AVAX/USDC", on_message, is_private=False)
        print("✓ Subscribed")

        # Use WebSocket for a while
        await asyncio.sleep(5)

        # Proper cleanup
        print("\nCleaning up...")
        await client.close_websocket()
        print("✓ WebSocket closed and cleaned up")

    except RuntimeError as e:
        print(f"✗ Error: {e}")


async def main():
    """Run all WebSocket manager examples."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 8 + "DEXALOT SDK WEBSOCKET MANAGER EXAMPLES" + " " * 12 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    client = None
    try:
        # Initialize client
        client = DexalotClient()
        init_result = await client.initialize_client()

        if not init_result.success:
            print(f"✗ Cannot initialize client: {init_result.error}")
            return

        # Run examples
        await example_basic_subscription(client)
        await example_multiple_subscriptions(client)
        await example_private_subscription(client)
        await example_reconnection_handling(client)
        await example_heartbeat_monitoring(client)
        await example_one_off_connection(client)
        await example_cleanup(client)

        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)
        print("\nKey Takeaways:")
        print("  1. Enable WebSocket manager via config.ws_manager_enabled = True")
        print("  2. Use subscribe_to_events() for persistent connections")
        print("  3. Use listen_to_events() for one-off connections")
        print("  4. Manager handles reconnection and heartbeat automatically")
        print("  5. Always cleanup with close_websocket() when done")
        print()

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        print("   Make sure you have a valid .env file with API credentials")
    finally:
        if client is not None:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
