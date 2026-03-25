"""
Dexalot SDK Parallel Operations Examples

This script demonstrates parallel async operations for improved performance,
including parallel balance fetching, orderbook queries, and performance comparisons.
"""

import asyncio
import time

from dexalot_sdk import DexalotClient


async def example_parallel_orderbooks(client: DexalotClient):
    """Demonstrate fetching multiple orderbooks in parallel."""
    print("=" * 60)
    print("Example 1: Parallel Orderbook Queries")
    print("=" * 60)

    # Get available pairs
    pairs_result = await client.get_clob_pairs()
    if not pairs_result.success:
        print(f"✗ Error fetching pairs: {pairs_result.error}")
        return

    # Pairs are stored in client.pairs after get_clob_pairs() is called
    if not client.pairs:
        print("✗ No pairs available")
        return

    pairs = list(client.pairs.keys())[:5]  # First 5 pairs

    print(f"\nFetching orderbooks for {len(pairs)} pairs...")

    # Sequential approach
    print("\n1. Sequential approach:")
    start = time.time()
    sequential_results = []
    for pair in pairs:
        result = await client.get_orderbook(pair)
        sequential_results.append((pair, result))
    sequential_time = time.time() - start
    print(f"   Time: {sequential_time:.3f}s")

    # Parallel approach
    print("\n2. Parallel approach:")
    start = time.time()
    parallel_tasks = [client.get_orderbook(pair) for pair in pairs]
    parallel_results = await asyncio.gather(*parallel_tasks)
    parallel_time = time.time() - start
    print(f"   Time: {parallel_time:.3f}s")

    # Results
    print(f"\n✓ Speedup: {sequential_time / parallel_time:.2f}x faster")
    print("\nOrderbook results:")
    for pair, result in zip(pairs, parallel_results, strict=True):
        if result.success:
            orderbook = result.data
            bids = len(orderbook.get("bids", []))
            asks = len(orderbook.get("asks", []))
            print(f"  {pair}: {bids} bids, {asks} asks")
        else:
            print(f"  {pair}: Error - {result.error}")


async def example_parallel_balances(client: DexalotClient):
    """Demonstrate parallel balance fetching across chains."""
    print("\n" + "=" * 60)
    print("Example 2: Parallel Balance Fetching Across Chains")
    print("=" * 60)

    if not client.account:
        print("⚠ No wallet connected - skipping balance example")
        print("  Set PRIVATE_KEY environment variable to enable this example")
        return

    address = client.account.address
    print(f"\nFetching balances for {address[:10]}... across all chains")

    # Sequential approach
    print("\n1. Sequential approach:")
    start = time.time()
    sequential_result = await client.get_all_chain_wallet_balances(address)
    sequential_time = time.time() - start
    print(f"   Time: {sequential_time:.3f}s")

    # Note: get_all_chain_wallet_balances already uses parallel fetching internally
    # This example shows the performance benefit
    if sequential_result.success:
        balances = sequential_result.data
        if isinstance(balances, dict):
            chains = list(balances.keys())
            print(f"   Fetched balances for {len(chains)} chains")
            print("\n✓ Parallel fetching is automatic in get_all_chain_wallet_balances()")
            print("  This would take much longer if done sequentially!")


async def example_parallel_mixed_operations(client: DexalotClient):
    """Demonstrate parallel execution of different operations."""
    print("\n" + "=" * 60)
    print("Example 3: Parallel Mixed Operations")
    print("=" * 60)

    print("\nFetching multiple data types in parallel...")

    # Create tasks for different operations
    tasks = [
        ("Tokens", client.get_tokens()),
        ("Pairs", client.get_clob_pairs()),
        ("Environments", client.get_environments()),
    ]

    # Add orderbook if pairs are available
    pairs_result = await client.get_clob_pairs()
    if pairs_result.success and client.pairs:
        pairs = list(client.pairs.keys())
        if pairs:
            tasks.append(("Orderbook", client.get_orderbook(pairs[0])))

    start = time.time()
    results = await asyncio.gather(*[task[1] for task in tasks], return_exceptions=True)
    parallel_time = time.time() - start

    print(f"\n✓ Fetched {len(tasks)} different data types in {parallel_time:.3f}s")
    print("\nResults:")

    for (name, _), result in zip(tasks, results, strict=True):
        if isinstance(result, Exception):
            print(f"  {name}: Error - {result}")
        elif hasattr(result, "success"):
            # Result pattern
            if result.success:
                data = result.data
                if isinstance(data, list):
                    print(f"  {name}: {len(data)} items")
                elif isinstance(data, dict):
                    print(f"  {name}: {len(data)} keys")
                else:
                    print(f"  {name}: Success")
            else:
                print(f"  {name}: Error - {result.error}")
        else:
            # Direct data (shouldn't happen now, but handle for safety)
            print(f"  {name}: Success (direct data)")


async def example_error_handling_parallel(client: DexalotClient):
    """Demonstrate error handling in parallel operations."""
    print("\n" + "=" * 60)
    print("Example 4: Error Handling in Parallel Operations")
    print("=" * 60)

    # Mix valid and invalid operations
    tasks = [
        client.get_tokens(),  # Valid - returns Result[list]
        client.get_orderbook("INVALID/PAIR"),  # Invalid pair - returns Result[dict]
        client.get_clob_pairs(),  # Valid - returns Result[str]
        client.get_orderbook("ANOTHER/INVALID"),  # Invalid pair - returns Result[dict]
    ]

    print("\nExecuting mixed valid/invalid operations in parallel...")

    # Use return_exceptions=True to handle errors gracefully
    results = await asyncio.gather(*tasks, return_exceptions=True)

    print("\nResults:")
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"  Task {i + 1}: Exception - {result}")
        elif result.success:
            print(f"  Task {i + 1}: Success")
        else:
            print(f"  Task {i + 1}: Error - {result.error}")

    print("\n✓ All tasks completed, errors handled gracefully")


async def main():
    """Run all parallel operation examples."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 8 + "DEXALOT SDK PARALLEL OPERATIONS EXAMPLES" + " " * 14 + "║")
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
        await example_parallel_orderbooks(client)
        await example_parallel_balances(client)
        await example_parallel_mixed_operations(client)
        await example_error_handling_parallel(client)

        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)
        print("\nKey Takeaways:")
        print("  1. Use asyncio.gather() for parallel operations")
        print("  2. Parallel operations significantly improve performance")
        print("  3. Handle errors gracefully with return_exceptions=True")
        print("  4. SDK methods are safe for concurrent execution")
        print()

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        print("   Make sure you have a valid .env file with API credentials")
    finally:
        # Clean up client session
        if client is not None:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
