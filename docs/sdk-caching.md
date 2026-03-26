# SDK Caching Guide

This document provides detailed information about configuring the caching system in the Dexalot SDK (Python and TypeScript).

## Overview

The Dexalot SDK uses a 4-level caching architecture to optimize performance by reducing redundant API calls. Each cache level has a different Time-To-Live (TTL) based on data volatility.

## Cache Levels

### 1. Static Cache (Default: 1 hour / 3600 seconds)

**Purpose:** Cache data that rarely or never changes

**Cached Methods:**
- `get_environments()` / `getEnvironments()` - Trading environments configuration
- `get_mainnets()` / `getMainnets()` - Connected mainnet networks
- `get_deployment()` / `getDeployment()` - Deployment configuration

**When to customize:**
- Set higher TTL (e.g., 2-4 hours) if you want to reduce API calls further
- Set lower TTL (e.g., 30 minutes) if deployment configs might change

**Example:**
```python
# Python
client = DexalotClient(cache_ttl_static=7200)  # 2 hours
```

```typescript
// TypeScript
const client = new DexalotClient(createConfig({
    cacheTtlStatic: 7200  // 2 hours
}));
```

### 2. Semi-Static Cache (Default: 15 minutes / 900 seconds)

**Purpose:** Cache data that changes occasionally

**Cached Methods:**
- `get_tokens()` / `getTokens()` - Token metadata
- `get_clob_pairs()` / `getClobPairs()` - Trading pairs
- `get_swap_pairs(chain_identifier)` / `getSwapPairs(chainId)` - Swap pairs for a chain

**When to customize:**
- Set higher TTL (e.g., 30-60 minutes) for stable production environments
- Set lower TTL (e.g., 5 minutes) if new tokens/pairs are added frequently

**Example:**
```python
# Python
client = DexalotClient(cache_ttl_semi_static=1800)  # 30 minutes
```

```typescript
// TypeScript
const client = new DexalotClient(createConfig({
    cacheTtlSemiStatic: 1800  // 30 minutes
}));
```

### 3. Balance Cache (Default: 10 seconds)

**Purpose:** Cache user-specific balance data

**Cached Methods:**
- `get_portfolio_balance(token, address=None)` / `getPortfolioBalance(token, address?)`
- `get_all_portfolio_balances(address=None)` / `getAllPortfolioBalances(address?)`
- `get_chain_wallet_balance(chain, token, address=None)` / `getChainWalletBalance(chain, token, address?)`
- `get_chain_wallet_balances(chain, address=None)` / `getChainWalletBalances(chain, address?)`
- `get_all_chain_wallet_balances(address=None)` / `getAllChainWalletBalances(address?)`

**Important:** Balance data is cached **per user address** to ensure data privacy and accuracy.

**When to customize:**
- Set higher TTL (e.g., 30-60 seconds) for read-heavy applications
- Set lower TTL (e.g., 1-5 seconds) for applications requiring near-real-time balances
- Set to 0 to disable balance caching entirely

**Example:**
```python
# Python
client = DexalotClient(cache_ttl_balance=5)  # 5 seconds
```

```typescript
// TypeScript
const client = new DexalotClient(createConfig({
    cacheTtlBalance: 5  // 5 seconds
}));
```

### 4. Orderbook Cache (Default: 1 second)

**Purpose:** Cache real-time orderbook data

**Cached Methods:**
- `get_orderbook(pair)` / `getOrderBook(pair)`

**When to customize:**
- Set higher TTL (e.g., 2-5 seconds) if slight delays are acceptable
- Set lower TTL (e.g., 0.5 seconds) for high-frequency trading applications
- Set to 0 to disable orderbook caching

**Example:**
```python
# Python
client = DexalotClient(cache_ttl_orderbook=0.5)  # 500ms
```

```typescript
// TypeScript
const client = new DexalotClient(createConfig({
    cacheTtlOrderbook: 0.5  // 500ms
}));
```

## Configuration Options

### Enable/Disable Caching

```python
# Python
# Enable caching (default)
client = DexalotClient(enable_cache=True)

# Disable all caching
client = DexalotClient(enable_cache=False)
```

```typescript
// TypeScript
// Enable caching (default)
const client = new DexalotClient(createConfig({ cacheEnabled: true }));

// Disable all caching
const client = new DexalotClient(createConfig({ cacheEnabled: false }));
```

### Custom TTL Values

```python
# Python
# Configure all cache levels
client = DexalotClient(
    enable_cache=True,
    cache_ttl_static=7200,      # 2 hours
    cache_ttl_semi_static=1800,  # 30 minutes
    cache_ttl_balance=5,         # 5 seconds
    cache_ttl_orderbook=0.5      # 500ms
)
```

```typescript
// TypeScript
// Configure all cache levels
const client = new DexalotClient(createConfig({
    cacheEnabled: true,
    cacheTtlStatic: 7200,      // 2 hours
    cacheTtlSemiStatic: 1800,  // 30 minutes
    cacheTtlBalance: 5,         // 5 seconds
    cacheTtlOrderbook: 0.5     // 500ms
}));
```

### Partial Configuration

You can configure only specific cache levels:

```python
# Python
# Only customize balance cache, others use defaults
client = DexalotClient(cache_ttl_balance=30)

# Customize multiple levels
client = DexalotClient(
    cache_ttl_semi_static=3600,  # 1 hour
    cache_ttl_balance=1          # 1 second
)
```

```typescript
// TypeScript
// Only customize balance cache, others use defaults
const client = new DexalotClient(createConfig({
    cacheTtlBalance: 30
}));

// Customize multiple levels
const client = new DexalotClient(createConfig({
    cacheTtlSemiStatic: 3600,  // 1 hour
    cacheTtlBalance: 1         // 1 second
}));
```

## Cache Invalidation

### Invalidate All Caches

```python
# Python
client.invalidate_cache()
```

```typescript
// TypeScript
client.invalidateCache();
```

### Invalidate Specific Cache Level

```python
# Python
# Invalidate static cache
client.invalidate_cache(level="static")

# Invalidate semi-static cache
client.invalidate_cache(level="semi_static")

# Invalidate balance cache
client.invalidate_cache(level="balance")

# Invalidate orderbook cache
client.invalidate_cache(level="orderbook")
```

```typescript
// TypeScript
// Invalidate static cache
client.invalidateCache('static');

// Invalidate semi-static cache
client.invalidateCache('semi_static');

// Invalidate balance cache
client.invalidateCache('balance');

// Invalidate orderbook cache
client.invalidateCache('orderbook');
```

## Use Cases

### High-Frequency Trading Application

Minimize cache TTLs for near-real-time data:

```python
# Python
client = DexalotClient(
    cache_ttl_static=3600,       # Keep static data cached
    cache_ttl_semi_static=300,   # 5 minutes for pairs
    cache_ttl_balance=1,         # 1 second for balances
    cache_ttl_orderbook=0.5      # 500ms for orderbook
)
```

```typescript
// TypeScript
const client = new DexalotClient(createConfig({
    cacheTtlStatic: 3600,       // Keep static data cached
    cacheTtlSemiStatic: 300,   // 5 minutes for pairs
    cacheTtlBalance: 1,         // 1 second for balances
    cacheTtlOrderbook: 0.5     // 500ms for orderbook
}));
```

### Dashboard/Analytics Application

Maximize cache TTLs to reduce API load:

```python
# Python
client = DexalotClient(
    cache_ttl_static=7200,       # 2 hours
    cache_ttl_semi_static=3600,  # 1 hour
    cache_ttl_balance=60,        # 1 minute
    cache_ttl_orderbook=5        # 5 seconds
)
```

```typescript
// TypeScript
const client = new DexalotClient(createConfig({
    cacheTtlStatic: 7200,       // 2 hours
    cacheTtlSemiStatic: 3600,  // 1 hour
    cacheTtlBalance: 60,        // 1 minute
    cacheTtlOrderbook: 5        // 5 seconds
}));
```

### Development/Testing

Disable caching for always-fresh data:

```python
# Python
client = DexalotClient(enable_cache=False)
```

```typescript
// TypeScript
const client = new DexalotClient(createConfig({ cacheEnabled: false }));
```

### Production API Server

Balance performance and freshness:

```python
# Python
client = DexalotClient(
    cache_ttl_static=3600,       # 1 hour (default)
    cache_ttl_semi_static=900,   # 15 minutes (default)
    cache_ttl_balance=10,        # 10 seconds (default)
    cache_ttl_orderbook=1        # 1 second (default)
)
```

```typescript
// TypeScript
const client = new DexalotClient(createConfig({
    cacheTtlStatic: 3600,       // 1 hour (default)
    cacheTtlSemiStatic: 900,    // 15 minutes (default)
    cacheTtlBalance: 10,        // 10 seconds (default)
    cacheTtlOrderbook: 1        // 1 second (default)
}));
```

## Best Practices

1. **Start with defaults**: The default TTL values are optimized for most use cases
2. **Monitor performance**: Adjust TTLs based on your application's performance metrics
3. **Consider data freshness requirements**: Balance between performance and data accuracy
4. **Use cache invalidation**: Manually invalidate caches after write operations if needed
5. **Test with caching disabled**: Ensure your application works correctly without caching
6. **Per-user data**: Remember that balance data is automatically cached per user

## Performance Metrics

Expected API call reduction with default settings:

| Data Type | Without Cache | With Cache | Reduction |
|-----------|---------------|------------|-----------|
| Static | Every request | 1/hour | ~99.9% |
| Semi-Static | Every request | 1/15min | ~95% |
| Balance | Every request | 1/10sec | Varies* |
| Orderbook | Every request | 1/sec | Varies* |

*Reduction depends on application request patterns

## Troubleshooting

### Stale Data Issues

If you're seeing stale data:
1. Check your TTL values - they might be too high
2. Manually invalidate the cache: `client.invalidate_cache()` (Python) or `client.invalidateCache()` (TypeScript)
3. Consider disabling caching for that specific use case

### Performance Issues

If caching isn't improving performance:
1. Verify caching is enabled: `enable_cache=True` (Python) or `cacheEnabled: true` (TypeScript)
2. Check that you're calling the same methods repeatedly
3. Monitor cache hit rates (future feature)

### Memory Concerns

The cache has a maximum size of 256 entries per level. If you're concerned about memory:
1. Reduce TTL values to expire entries faster
2. Disable caching for less-used data
3. Manually invalidate caches periodically

## Technical Details

- **Cache Implementation**: In-memory TTL-based cache using `MemoryCache` class
- **Cache Scope**: Module-level (shared across all client instances)
- **Thread Safety**: Not thread-safe across OS threads. Safe for concurrent asyncio tasks — `async_ttl_cached` coalesces concurrent callers for the same key via `asyncio.Future` (stampede protection).
- **Persistence**: Cache is lost when the process terminates
- **Maximum Size**: 256 entries per cache level (FIFO eviction)

## See Also

- [Python SDK README](../python/dexalot-sdk/README.md) - Python SDK main documentation
- [TypeScript SDK README](../typescript/dexalot-sdk/README.md) - TypeScript SDK main documentation
