"""
Dexalot SDK Caching Examples

This script demonstrates the caching capabilities of the Dexalot SDK,
including configuration, usage patterns, and performance optimization.
"""

import asyncio
import time

from dexalot_sdk import DexalotClient


async def example_basic_caching():
    """Demonstrate basic caching behavior."""
    print("=" * 60)
    print("Example 1: Basic Caching")
    print("=" * 60)

    # Initialize client with default caching enabled
    client = DexalotClient()
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\n1. First call to get_tokens() - fetches from API")
        start = time.time()
        result = await client.get_tokens()
        first_call_time = time.time() - start
        print(f"   Time: {first_call_time:.3f}s")
        if result.success:
            tokens = result.data
            print(f"   Found {len(tokens)} tokens")
        else:
            print(f"   Error: {result.error}")
            return

        print("\n2. Second call to get_tokens() - returns cached result")
        start = time.time()
        result_cached = await client.get_tokens()
        second_call_time = time.time() - start
        print(f"   Time: {second_call_time:.3f}s")
        if result_cached.success:
            tokens_cached = result_cached.data
            print(f"   Found {len(tokens_cached)} tokens (same as before)")
            print(f"   Speedup: {first_call_time / second_call_time:.1f}x faster")

        print("\n✓ Caching reduces API calls and improves performance!\n")
    finally:
        await client.close()


async def example_custom_ttl():
    """Demonstrate custom TTL configuration."""
    print("=" * 60)
    print("Example 2: Custom TTL Configuration")
    print("=" * 60)

    # Configure custom TTL values
    client = DexalotClient(
        enable_cache=True,
        cache_ttl_static=7200,  # 2 hours for static data
        cache_ttl_semi_static=1800,  # 30 minutes for semi-static
        cache_ttl_balance=5,  # 5 seconds for balances
        cache_ttl_orderbook=0.5,  # 500ms for orderbook
    )
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nCustom TTL values configured:")
        print("  - Static data: 2 hours")
        print("  - Semi-static data: 30 minutes")
        print("  - Balance data: 5 seconds")
        print("  - Orderbook data: 500ms")

        print("\n✓ TTL values can be customized based on your needs!\n")
    finally:
        await client.close()


async def example_cache_invalidation():
    """Demonstrate cache invalidation."""
    print("=" * 60)
    print("Example 3: Cache Invalidation")
    print("=" * 60)

    client = DexalotClient()
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\n1. Fetch tokens (cached)")
        result = await client.get_tokens()
        if result.success:
            tokens = result.data
            print(f"   Found {len(tokens)} tokens")
        else:
            print(f"   Error: {result.error}")
            return

        print("\n2. Invalidate all caches")
        client.invalidate_cache()
        print("   All caches cleared")

        print("\n3. Next call will fetch fresh data from API")
        result_fresh = await client.get_tokens()
        if result_fresh.success:
            tokens_fresh = result_fresh.data
            print(f"   Found {len(tokens_fresh)} tokens (fresh data)")
        else:
            print(f"   Error: {result_fresh.error}")

        print("\n4. Invalidate specific cache level")
        client.invalidate_cache(level="semi_static")
        print("   Semi-static cache cleared (tokens, pairs)")

        print("\n✓ Cache can be invalidated when fresh data is needed!\n")
    finally:
        await client.close()


async def example_disabled_caching():
    """Demonstrate disabling caching."""
    print("=" * 60)
    print("Example 4: Disabled Caching")
    print("=" * 60)

    # Disable caching entirely
    client = DexalotClient(enable_cache=False)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nCaching disabled - every call fetches from API")

        print("\n1. First call to get_tokens()")
        start = time.time()
        result = await client.get_tokens()
        first_time = time.time() - start
        print(f"   Time: {first_time:.3f}s")
        if result.success:
            tokens = result.data
            print(f"   Found {len(tokens)} tokens")

        print("\n2. Second call to get_tokens()")
        start = time.time()
        result2 = await client.get_tokens()
        second_time = time.time() - start
        print(f"   Time: {second_time:.3f}s")
        if result2.success:
            tokens2 = result2.data
            print(f"   Found {len(tokens2)} tokens")

        print("\n   Both calls took similar time (no caching)")
        print("\n✓ Caching can be disabled when always-fresh data is required!\n")
    finally:
        await client.close()


async def example_per_user_caching():
    """Demonstrate per-user balance caching."""
    print("=" * 60)
    print("Example 5: Per-User Balance Caching")
    print("=" * 60)

    client = DexalotClient()
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nBalance data is cached per user address:")

        # Assuming client has a connected wallet
        if client.account:
            print(f"\n1. Get balance for connected wallet ({client.account.address[:10]}...)")
            result1 = await client.get_portfolio_balance("USDC")
            if result1.success:
                balance1 = result1.data
                print(f"   Balance: {balance1}")
            else:
                print(f"   Error: {result1.error}")

            print("\n2. Get balance for different address")
            other_address = "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb"
            result2 = await client.get_portfolio_balance("USDC", address=other_address)
            if result2.success:
                balance2 = result2.data
                print(f"   Balance: {balance2}")
            else:
                print(f"   Error: {result2.error}")

            print("\n   Each user has separate cache entries")
            print("   ✓ Ensures data privacy and accuracy!")
        else:
            print("\n   (No wallet connected - skipping this example)")

        print()
    finally:
        await client.close()


async def example_cache_levels():
    """Demonstrate different cache levels."""
    print("=" * 60)
    print("Example 6: Cache Levels Overview")
    print("=" * 60)

    client = DexalotClient()
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nThe SDK uses 4 cache levels:\n")

        print("1. STATIC (1 hour TTL)")
        print("   - get_environments()")
        print("   - get_mainnets()")
        print("   - get_deployment()")
        envs_result = await client.get_environments()
        if envs_result.success:
            envs = envs_result.data
            print(f"   ✓ Fetched {len(envs)} environments (cached for 1 hour)")

        print("\n2. SEMI-STATIC (15 minutes TTL)")
        print("   - get_tokens()")
        print("   - get_clob_pairs()")
        print("   - get_swap_pairs()")
        tokens_result = await client.get_tokens()
        if tokens_result.success:
            tokens = tokens_result.data
            print(f"   ✓ Fetched {len(tokens)} tokens (cached for 15 minutes)")

        print("\n3. BALANCE (10 seconds TTL)")
        print("   - get_portfolio_balance()")
        print("   - get_all_portfolio_balances()")
        print("   - get_chain_wallet_balance()")
        print("   - get_all_chain_wallet_balances()")
        if client.account:
            balances_result = await client.get_all_portfolio_balances()
            if balances_result.success:
                balances = balances_result.data
                num_tokens = len(balances) if isinstance(balances, dict) else 0
                print(f"   ✓ Fetched {num_tokens} token balances (cached for 10 seconds)")
        else:
            print("   (No wallet connected)")
        print("\n4. ORDERBOOK (1 second TTL)")
        print("   - get_orderbook()")
        pairs_result = await client.get_clob_pairs()
        if pairs_result.success:
            # get_clob_pairs returns Result[str], pairs are in self.pairs
            if hasattr(client, "pairs") and client.pairs:
                pair = list(client.pairs.keys())[0]
                ob_result = await client.get_orderbook(pair)
                if ob_result.success:
                    ob = ob_result.data
                    bid_count = len(ob.get("bids", [])) if isinstance(ob, dict) else 0
                    ask_count = len(ob.get("asks", [])) if isinstance(ob, dict) else 0
                    print(
                        f"   ✓ Fetched {pair} orderbook: {bid_count} bids, {ask_count} asks (cached for 1 second)"
                    )

        print("\n✓ Different TTLs optimize for data volatility!\n")
    finally:
        await client.close()


async def example_write_operations():
    """Demonstrate that write operations are never cached."""
    print("=" * 60)
    print("Example 7: Write Operations Never Cached")
    print("=" * 60)

    print("\nWrite operations are NEVER cached to ensure data integrity:")
    print("  - add_order()")
    print("  - cancel_order()")
    print("  - deposit()")
    print("  - withdraw()")
    print("  - transfer_portfolio()")
    print("  - add_gas()")
    print("  - remove_gas()")

    print("\n✓ Every write operation executes immediately!")
    print("✓ No risk of stale transactions or double-spending!\n")


async def main():
    """Run all caching examples."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 10 + "DEXALOT SDK CACHING EXAMPLES" + " " * 20 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    try:
        await example_basic_caching()
        await example_custom_ttl()
        await example_cache_invalidation()
        await example_disabled_caching()
        await example_per_user_caching()
        await example_cache_levels()
        await example_write_operations()

        print("=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)
        print("\nKey Takeaways:")
        print("  1. Caching is enabled by default for better performance")
        print("  2. TTL values can be customized per cache level")
        print("  3. Cache can be invalidated manually when needed")
        print("  4. Balance data is cached per user for privacy")
        print("  5. Write operations are never cached for safety")
        print()

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        print("   Make sure you have a valid .env file with API credentials")
    finally:
        # Small delay to allow all aiohttp sessions to close gracefully
        # This prevents "Unclosed client session" warnings
        await asyncio.sleep(0.250)


if __name__ == "__main__":
    asyncio.run(main())
