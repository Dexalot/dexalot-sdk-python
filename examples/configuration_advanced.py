"""
Dexalot SDK Advanced Configuration Examples

This script demonstrates advanced configuration options including custom retry,
rate limiting, provider failover, WebSocket settings, and environment variables.
"""

import asyncio

from dexalot_sdk import DexalotClient
from dexalot_sdk.core.config import DexalotConfig


async def example_custom_retry_config(base_client: DexalotClient):
    """Demonstrate custom retry configuration."""
    print("=" * 60)
    print("Example 1: Custom Retry Configuration")
    print("=" * 60)

    # Create custom retry configuration, inheriting base settings from base_client
    config = DexalotConfig(
        parent_env=base_client.config.parent_env,
        api_base_url=base_client.config.api_base_url,
        retry_enabled=True,
        retry_max_attempts=5,  # Try up to 5 times
        retry_initial_delay=2.0,  # Start with 2s delay
        retry_max_delay=30.0,  # Max 30s between retries
        retry_exponential_base=2.0,  # Double delay each time
        retry_on_status=(429, 500, 502, 503, 504),  # Retry on these HTTP codes
    )

    client = DexalotClient(config=config)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nRetry configuration:")
        print(f"  Max attempts: {config.retry_max_attempts}")
        print(f"  Initial delay: {config.retry_initial_delay}s")
        print(f"  Max delay: {config.retry_max_delay}s")
        print(f"  Exponential base: {config.retry_exponential_base}")
        print(f"  Retry on status codes: {config.retry_on_status}")

        print("\n✓ Retry will attempt up to 5 times with delays: 2s, 4s, 8s, 16s, 30s")

        # Test with a call
        result = await client.get_tokens()
        if result.success:
            print(f"\n✓ Successfully fetched {len(result.data)} tokens (with retry configured)")
    finally:
        await client.close()


async def example_custom_rate_limiting(base_client: DexalotClient):
    """Demonstrate custom rate limiting configuration."""
    print("\n" + "=" * 60)
    print("Example 2: Custom Rate Limiting")
    print("=" * 60)

    # Create custom rate limiting configuration, inheriting base settings
    config = DexalotConfig(
        parent_env=base_client.config.parent_env,
        api_base_url=base_client.config.api_base_url,
        rate_limit_enabled=True,
        rate_limit_requests_per_second=10.0,  # 10 API calls/second
        rate_limit_rpc_per_second=20.0,  # 20 RPC calls/second
    )

    client = DexalotClient(config=config)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nRate limiting configuration:")
        print(f"  API requests: {config.rate_limit_requests_per_second}/second")
        print(f"  RPC calls: {config.rate_limit_rpc_per_second}/second")

        print("\n✓ Rate limiter will throttle requests to prevent API throttling")

        # Test with multiple rapid calls
        print("\nMaking 5 rapid API calls (rate limited)...")
        start = asyncio.get_event_loop().time()
        tasks = [client.get_tokens() for _ in range(5)]
        await asyncio.gather(*tasks)
        elapsed = asyncio.get_event_loop().time() - start

        print(f"✓ Completed in {elapsed:.2f}s (rate limited to ~10 req/s)")
    finally:
        await client.close()


async def example_provider_failover_config(base_client: DexalotClient):
    """Demonstrate provider failover configuration."""
    print("\n" + "=" * 60)
    print("Example 3: Provider Failover Configuration")
    print("=" * 60)

    # Create custom failover configuration, inheriting base settings
    config = DexalotConfig(
        parent_env=base_client.config.parent_env,
        api_base_url=base_client.config.api_base_url,
        provider_failover_enabled=True,
        provider_failover_cooldown=30,  # 30s cooldown before retrying failed provider
        provider_failover_max_failures=3,  # Mark unhealthy after 3 failures
    )

    client = DexalotClient(config=config)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nProvider failover configuration:")
        print(f"  Enabled: {config.provider_failover_enabled}")
        print(f"  Cooldown: {config.provider_failover_cooldown}s")
        print(f"  Max failures: {config.provider_failover_max_failures}")

        print("\n✓ Failover will automatically switch to backup providers on failure")
        print("  Failed providers are retried after cooldown period")

        # Note: To test failover, you would need to configure multiple RPC URLs
        # via environment variables: DEXALOT_RPC_<CHAIN_ID>=url1,url2,url3
    finally:
        await client.close()


async def example_websocket_config(base_client: DexalotClient):
    """Demonstrate WebSocket configuration."""
    print("\n" + "=" * 60)
    print("Example 4: WebSocket Configuration")
    print("=" * 60)

    # Create custom WebSocket configuration, inheriting base settings
    config = DexalotConfig(
        parent_env=base_client.config.parent_env,
        api_base_url=base_client.config.api_base_url,
        ws_manager_enabled=True,
        ws_ping_interval=20,  # Ping every 20 seconds
        ws_ping_timeout=5,  # Wait 5s for pong
        ws_reconnect_initial_delay=1.0,
        ws_reconnect_max_delay=30.0,
        ws_reconnect_exponential_base=2.0,
        ws_reconnect_max_attempts=10,  # Try up to 10 times
    )

    client = DexalotClient(config=config)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nWebSocket configuration:")
        print(f"  Manager enabled: {config.ws_manager_enabled}")
        print(f"  Ping interval: {config.ws_ping_interval}s")
        print(f"  Ping timeout: {config.ws_ping_timeout}s")
        print(f"  Reconnect initial delay: {config.ws_reconnect_initial_delay}s")
        print(f"  Reconnect max delay: {config.ws_reconnect_max_delay}s")
        print(f"  Reconnect max attempts: {config.ws_reconnect_max_attempts}")

        print("\n✓ WebSocket manager configured with custom settings")
    finally:
        await client.close()


async def example_connection_pool_config(base_client: DexalotClient):
    """Demonstrate connection pool configuration."""
    print("\n" + "=" * 60)
    print("Example 5: Connection Pool Configuration")
    print("=" * 60)

    # Create custom connection pool configuration, inheriting base settings
    config = DexalotConfig(
        parent_env=base_client.config.parent_env,
        api_base_url=base_client.config.api_base_url,
        connection_pool_limit=200,  # Total 200 connections
        connection_pool_limit_per_host=50,  # 50 per host
    )

    client = DexalotClient(config=config)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nConnection pool configuration:")
        print(f"  Total pool size: {config.connection_pool_limit}")
        print(f"  Per-host limit: {config.connection_pool_limit_per_host}")

        print("\n✓ Connection pool configured for high-throughput scenarios")
    finally:
        await client.close()


async def example_timeout_config(base_client: DexalotClient):
    """Demonstrate timeout configuration."""
    print("\n" + "=" * 60)
    print("Example 6: Timeout Configuration")
    print("=" * 60)

    # Create custom timeout configuration, inheriting base settings
    config = DexalotConfig(
        parent_env=base_client.config.parent_env,
        api_base_url=base_client.config.api_base_url,
        timeouts=(10, 60),  # 10s connect, 60s read
    )

    client = DexalotClient(config=config)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nTimeout configuration:")
        print(f"  Connect timeout: {config.timeouts[0]}s")
        print(f"  Read timeout: {config.timeouts[1]}s")

        print("\n✓ Timeouts configured for slower networks")
    finally:
        await client.close()


async def example_comprehensive_config(base_client: DexalotClient):
    """Demonstrate comprehensive configuration combining multiple settings."""
    print("\n" + "=" * 60)
    print("Example 7: Comprehensive Configuration")
    print("=" * 60)

    # Create comprehensive configuration, inheriting base settings
    config = DexalotConfig(
        # Environment (inherit from base_client)
        parent_env=base_client.config.parent_env,
        api_base_url=base_client.config.api_base_url,
        # Caching
        enable_cache=True,
        cache_ttl_static=7200,  # 2 hours
        cache_ttl_semi_static=1800,  # 30 minutes
        cache_ttl_balance=5,  # 5 seconds
        cache_ttl_orderbook=1,  # 1 second
        # Retry
        retry_enabled=True,
        retry_max_attempts=3,
        retry_initial_delay=1.0,
        retry_max_delay=10.0,
        # Rate Limiting
        rate_limit_enabled=True,
        rate_limit_requests_per_second=5.0,
        rate_limit_rpc_per_second=10.0,
        # Nonce Manager
        nonce_manager_enabled=True,
        # Provider Failover
        provider_failover_enabled=True,
        provider_failover_cooldown=60,
        provider_failover_max_failures=3,
        # WebSocket
        ws_manager_enabled=False,  # Disabled by default
        ws_ping_interval=30,
        ws_ping_timeout=10,
        # Connection Pool
        connection_pool_limit=100,
        connection_pool_limit_per_host=30,
        # Timeouts
        timeouts=(5, 30),
        # Logging
        log_level="INFO",
        log_format="console",
    )

    client = DexalotClient(config=config)
    try:
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"   Error: {init_result.error}")
            return

        print("\nComprehensive configuration applied:")
        print("  ✓ Caching enabled with custom TTLs")
        print("  ✓ Retry with exponential backoff")
        print("  ✓ Rate limiting configured")
        print("  ✓ Nonce manager enabled")
        print("  ✓ Provider failover enabled")
        print("  ✓ Connection pool configured")
        print("  ✓ Timeouts configured")
        print("  ✓ Logging configured")

        print("\n✓ All reliability features enabled and configured")
    finally:
        await client.close()


async def example_environment_variables():
    """Demonstrate configuration via environment variables."""
    print("\n" + "=" * 60)
    print("Example 8: Environment Variable Configuration")
    print("=" * 60)

    print("\nConfiguration can be set via environment variables:")
    print("\n  # Retry settings")
    print("  export DEXALOT_RETRY_ENABLED=true")
    print("  export DEXALOT_RETRY_MAX_ATTEMPTS=5")
    print("  export DEXALOT_RETRY_INITIAL_DELAY=2.0")
    print("\n  # Rate limiting")
    print("  export DEXALOT_RATE_LIMIT_ENABLED=true")
    print("  export DEXALOT_RATE_LIMIT_REQUESTS_PER_SECOND=10.0")
    print("\n  # Provider failover")
    print("  export DEXALOT_PROVIDER_FAILOVER_ENABLED=true")
    print("  export DEXALOT_PROVIDER_FAILOVER_COOLDOWN=60")
    print("\n  # WebSocket")
    print("  export DEXALOT_WS_MANAGER_ENABLED=true")
    print("  export DEXALOT_WS_PING_INTERVAL=30")
    print("\n  # RPC overrides (chain ID format)")
    print("  export DEXALOT_RPC_43114=https://api.avax.network/ext/bc/C/rpc,https://backup.rpc.com")
    print("  export DEXALOT_RPC_432204=https://subnets.avax.network/dexalot/mainnet/rpc")

    print("\n✓ Environment variables are automatically loaded by DexalotConfig.from_env()")


async def example_config_precedence():
    """Demonstrate configuration precedence."""
    print("\n" + "=" * 60)
    print("Example 9: Configuration Precedence")
    print("=" * 60)

    print("\nConfiguration precedence (highest to lowest):")
    print("  1. Constructor arguments (DexalotClient(config=config))")
    print("  2. Environment variables (DEXALOT_*)")
    print("  3. .env file")
    print("  4. Default values")

    print("\nExample:")
    print("  # .env file sets: DEXALOT_RETRY_MAX_ATTEMPTS=3")
    print("  # Environment variable: export DEXALOT_RETRY_MAX_ATTEMPTS=5")
    print("  # Constructor: DexalotClient(config=DexalotConfig(retry_max_attempts=10))")
    print("  # Result: retry_max_attempts = 10 (constructor wins)")

    print("\n✓ Constructor arguments have highest priority")


async def main():
    """Run all advanced configuration examples."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 6 + "DEXALOT SDK ADVANCED CONFIGURATION EXAMPLES" + " " * 10 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    base_client = None
    try:
        # Initialize base client for some examples (not actually used, but kept for consistency)
        base_client = DexalotClient()
        init_result = await base_client.initialize_client()
        if not init_result.success:
            print(f"⚠ Warning: Base client initialization failed: {init_result.error}")
            print("   Some examples may not work correctly")

        # Run examples
        await example_custom_retry_config(base_client)
        await example_custom_rate_limiting(base_client)
        await example_provider_failover_config(base_client)
        await example_websocket_config(base_client)
        await example_connection_pool_config(base_client)
        await example_timeout_config(base_client)
        await example_comprehensive_config(base_client)
        await example_environment_variables()
        await example_config_precedence()

        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)
        print("\nKey Takeaways:")
        print("  1. All settings are configurable via DexalotConfig")
        print("  2. Environment variables provide easy configuration")
        print("  3. Constructor arguments override environment variables")
        print("  4. Sensible defaults are provided for all settings")
        print("  5. Configuration is validated on initialization")
        print()

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        print("   Make sure you have a valid .env file with API credentials")
    finally:
        if base_client is not None:
            await base_client.close()


if __name__ == "__main__":
    asyncio.run(main())
