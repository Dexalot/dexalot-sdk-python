"""
Dexalot SDK Basic Async Usage Examples

This script demonstrates basic async usage patterns with the Dexalot SDK,
including initialization, Result pattern handling, and simple operations.
"""

import asyncio

from dexalot_sdk import DexalotClient


async def example_initialization():
    """Demonstrate client initialization."""
    print("=" * 60)
    print("Example 1: Client Initialization")
    print("=" * 60)

    client = DexalotClient()

    # Initialize client (required before other operations)
    result = await client.initialize_client()

    if result.success:
        print("✓ Client initialized successfully")
        print(f"  Environment: {client.parent_env}")
        print(f"  API Base URL: {client.api_base_url}")
    else:
        print(f"✗ Initialization failed: {result.error}")
        return None

    return client


async def example_get_tokens(client: DexalotClient):
    """Demonstrate fetching tokens."""
    print("\n" + "=" * 60)
    print("Example 2: Fetching Tokens")
    print("=" * 60)

    result = await client.get_tokens()

    if result.success:
        tokens = result.data
        print(f"✓ Found {len(tokens)} tokens")
        print("\nFirst 5 tokens:")
        for token in tokens[:5]:
            symbol = token.get("symbol", "N/A")
            name = token.get("name", "N/A")
            print(f"  - {symbol}: {name}")
    else:
        print(f"✗ Error fetching tokens: {result.error}")


async def example_get_pairs(client: DexalotClient):
    """Demonstrate fetching trading pairs."""
    print("\n" + "=" * 60)
    print("Example 3: Fetching Trading Pairs")
    print("=" * 60)

    result = await client.get_clob_pairs()

    if result.success:
        # get_clob_pairs returns Result[str], pairs are stored in client.pairs
        if hasattr(client, "pairs") and client.pairs:
            pair_list = list(client.pairs.keys())
            print(f"✓ Found {len(pair_list)} trading pairs")
            print("\nFirst 5 pairs:")
            for pair in pair_list[:5]:
                print(f"  - {pair}")
        else:
            print("✓ Pairs fetched but no pairs available")
    else:
        print(f"✗ Error fetching pairs: {result.error}")


async def example_get_orderbook(client: DexalotClient):
    """Demonstrate fetching orderbook."""
    print("\n" + "=" * 60)
    print("Example 4: Fetching Orderbook")
    print("=" * 60)

    # First, get available pairs
    pairs_result = await client.get_clob_pairs()
    if not pairs_result.success:
        print(f"✗ Error fetching pairs: {pairs_result.error}")
        return

    # Get first available pair (pairs are stored in client.pairs)
    if hasattr(client, "pairs") and client.pairs:
        pairs = list(client.pairs.keys())
        if not pairs:
            print("✗ No trading pairs available")
            return
        pair = pairs[0]
    else:
        print("✗ Unexpected pairs format")
        return

    print(f"Fetching orderbook for {pair}...")

    result = await client.get_orderbook(pair)

    if result.success:
        orderbook = result.data
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        print("✓ Orderbook fetched successfully")
        print(f"  Bids: {len(bids)} orders")
        print(f"  Asks: {len(asks)} orders")

        if bids:
            best_bid = bids[0]
            print(f"  Best bid: {best_bid.get('price', 'N/A')} @ {best_bid.get('quantity', 'N/A')}")

        if asks:
            best_ask = asks[0]
            print(f"  Best ask: {best_ask.get('price', 'N/A')} @ {best_ask.get('quantity', 'N/A')}")
    else:
        print(f"✗ Error fetching orderbook: {result.error}")


async def example_get_balances(client: DexalotClient):
    """Demonstrate fetching balances (requires wallet connection)."""
    print("\n" + "=" * 60)
    print("Example 5: Fetching Balances")
    print("=" * 60)

    if not client.account:
        print("⚠ No wallet connected - skipping balance example")
        print("  Set PRIVATE_KEY environment variable to enable this example")
        return

    result = await client.get_all_portfolio_balances()

    if result.success:
        balances = result.data
        if isinstance(balances, dict) and "balances" in balances:
            balance_list = balances["balances"]
            print(f"✓ Found balances for {len(balance_list)} tokens")
            print("\nToken balances:")
            for balance in balance_list[:5]:  # Show first 5
                symbol = balance.get("symbol", "N/A")
                available = balance.get("available", 0)
                locked = balance.get("locked", 0)
                total = balance.get("total", 0)
                print(f"  - {symbol}: {available} available, {locked} locked, {total} total")
        else:
            print(f"✓ Balances: {balances}")
    else:
        print(f"✗ Error fetching balances: {result.error}")


async def main():
    """Run all basic async examples."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 10 + "DEXALOT SDK BASIC ASYNC EXAMPLES" + " " * 18 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    client = None
    try:
        # Initialize client
        client = await example_initialization()
        if not client:
            print("\n✗ Cannot proceed without initialized client")
            return

        # Run examples
        await example_get_tokens(client)
        await example_get_pairs(client)
        await example_get_orderbook(client)
        await example_get_balances(client)

        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)
        print("\nKey Takeaways:")
        print("  1. All SDK methods are async and must be awaited")
        print("  2. All methods return Result[T] for consistent error handling")
        print("  3. Always check result.success before accessing result.data")
        print("  4. Use asyncio.run() for standalone scripts")
        print()

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        print("   Make sure you have a valid .env file with API credentials")
    finally:
        # Clean up client session
        if client:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
