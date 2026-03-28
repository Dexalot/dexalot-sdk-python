# Dexalot Python SDK

## Overview

`dexalot-sdk` is a Python library that provides core functionality for interacting with the Dexalot decentralized exchange. It offers a unified client interface for trading operations, cross-chain transfers, and portfolio management across multiple blockchain networks.

## Features

- **Unified Client**: Single `DexalotClient` interface for all Dexalot operations
- **Modular Architecture**: Separate clients for CLOB, Swap, and Transfer operations
- **Multi-Chain Support**: Works with Dexalot L1 subnet and connected chains
- **Caching**: TTL-based memory cache utilities for performance optimization

## Architecture

### Core Components

- **`core/client.py`**: Unified `DexalotClient` inheriting from modular components
- **`core/base.py`**: Environment setup, Web3 connections, error handling
- **`core/clob.py`**: Central Limit Order Book trading operations
- **`core/swap.py`**: SimpleSwap RFQ (Request for Quote) functionality
- **`core/transfer.py`**: Cross-chain deposits/withdrawals, portfolio management

### Utilities

- **`utils/input_validators.py`**: Validate SDK method input parameters (amounts, addresses, pairs, etc.)
- **`utils/cache.py`**: TTL-based caching utilities (`MemoryCache`, `ttl_cached`, `async_ttl_cached`)
- **`utils/observability.py`**: Structured logging and operation tracking
- **`utils/result.py`**: Standardized `Result[T]` type for consistent error handling
- **`utils/retry.py`**: Async retry decorator with exponential backoff
- **`utils/rate_limit.py`**: Token bucket rate limiter for API and RPC calls
- **`utils/nonce_manager.py`**: Async-safe nonce management to prevent transaction race conditions
- **`utils/provider_manager.py`**: RPC provider failover with health tracking
- **`utils/error_sanitizer.py`**: Error message sanitization to prevent information leakage
- **`utils/websocket_manager.py`**: Persistent WebSocket connection manager with reconnection and heartbeat

## Installation

### Install `uv`

`uv` is the recommended way to manage Python environments and dependencies for this SDK.

```sh
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# or with Homebrew
brew install uv
```

Install the SDK using uv (recommended):

```sh
uv venv && uv sync --group dev
```

Or install with pip:

```sh
pip install -e .
```

## Quick Start

```python
import asyncio
from dexalot_sdk import DexalotClient

async def main():
    client = None
    try:
        # Initialize client
        client = DexalotClient()
        result = await client.initialize_client()
        
        if not result.success:
            print(f"Initialization failed: {result.error}")
            return
        
        # Fetch trading pairs (stores pairs in client.pairs)
        pairs_result = await client.get_clob_pairs()
        if pairs_result.success:
            print(f"Available pairs: {list(client.pairs.keys())}")
        else:
            print(f"Error: {pairs_result.error}")
    finally:
        # Always close the client to clean up resources
        if client is not None:
            await client.close()

# Run the async function
asyncio.run(main())
```

**Key Points:**
- The SDK is **fully async** - all methods must be awaited
- Async operational methods return `Result[T]` for consistent error handling
- Use `asyncio.run()` for scripts or `await` in async contexts
- Always call `await client.close()` when done to clean up resources

See `examples/async_basic.py` for more examples.

## Usage

### Basic Async Usage

```python
import asyncio
from dexalot_sdk import DexalotClient

async def main():
    client = None
    try:
        client = DexalotClient()
        
        # Initialize client (required before other operations)
        init_result = await client.initialize_client()
        if not init_result.success:
            print(f"Failed to initialize: {init_result.error}")
            return
        
        # Get available trading pairs (stores pairs in client.pairs)
        pairs_result = await client.get_clob_pairs()
        if pairs_result.success:
            print(f"Found {len(client.pairs)} trading pairs")
        else:
            print(f"Error fetching pairs: {pairs_result.error}")
    finally:
        # Always close the client to clean up resources
        if client is not None:
            await client.close()

asyncio.run(main())
```

### Error Handling with Result Pattern

Async operational methods return `Result[T]` which provides consistent error handling:

```python
result = await client.get_orderbook("AVAX/USDC")

if result.success:
    orderbook = result.data
    print(f"Bids: {orderbook['bids']}")
    print(f"Asks: {orderbook['asks']}")
else:
    print(f"Error: {result.error}")
    # Handle error appropriately
```

See `examples/error_handling.py` for comprehensive error handling patterns.
## Dependencies

- `web3>=6.0.0`: Multi-chain blockchain interactions (AsyncWeb3 for async operations)
- `aiohttp`: Async HTTP client for Dexalot API communication
- `python-dotenv`: Environment variable management
- `eth-account`: Ethereum account management and transaction signing
- `websockets`: Async WebSocket client for real-time event subscriptions

## Scripts

No scripts are included in the SDK. For prompt validation, use the `dexalot-mcp` package.

## Testing

Run tests from the repository root:

```sh
make test  # Unit tests
make cov   # Coverage report
```


## Caching

The SDK includes a built-in 4-level caching system to optimize performance by reducing redundant API calls. Caching is **enabled by default** with sensible TTL (Time-To-Live) values.

> **📖 Detailed Documentation**: See [SDK Caching Guide](docs/sdk-caching.md) for comprehensive caching documentation, including advanced usage patterns, use cases, troubleshooting, and performance considerations.

### Cache Levels

| Level | Data Type | Default TTL | Examples |
|-------|-----------|-------------|----------|
| **Static** | Rarely changes | 1 hour | Environments, deployments, connected chains |
| **Semi-Static** | Changes occasionally | 15 minutes | Tokens, trading pairs |
| **Balance** | User-specific, updates frequently | 10 seconds | Portfolio balances, wallet balances |
| **Orderbook** | Real-time data | 1 second | Order book snapshots |

### Basic Usage

```python
import asyncio
from dexalot_sdk import DexalotClient
from pprint import pprint

async def main():
    async with DexalotClient() as client:
        await client.initialize_client()

        # First call fetches from API
        result = await client.get_all_portfolio_balances()
        if result.success:
            pprint(result.data)
            # {'ALOT': {'available': 95.5, 'locked': 4.5, 'total': 100.0}, 'AVAX': ...}

        # Second call within 10 seconds returns cached result
        result = await client.get_all_portfolio_balances()  # Cached!

asyncio.run(main())
```

### Configuration

Customize cache behavior during client initialization:

```python
# Disable caching entirely
client = DexalotClient(enable_cache=False)

# Custom TTL values (in seconds)
client = DexalotClient(
    enable_cache=True,
    cache_ttl_static=7200,      # 2 hours for static data
    cache_ttl_semi_static=1800,  # 30 minutes for semi-static
    cache_ttl_balance=5,         # 5 seconds for balances
    cache_ttl_orderbook=0.5      # 500ms for orderbook
)
```

### Cache Invalidation

Manually clear cached data when needed:

```python
# Clear all cache levels
client.invalidate_cache()

# Clear specific cache level
client.invalidate_cache(level="balance")  # Options: static, semi_static, balance, orderbook
```

### Cached Methods

**Static Data (1 hour):**
- `get_environments()`
- `get_chains()`
- `get_deployment()`

**Semi-Static Data (15 minutes):**
- `get_tokens()`
- `get_clob_pairs()`
- `get_swap_pairs(chain_identifier)`

**Balance Data (10 seconds):**
- `get_portfolio_balance(token, address=None)`
- `get_all_portfolio_balances(address=None)`
- `get_chain_wallet_balance(chain, token, address=None)`
- `get_chain_wallet_balances(chain, address=None)`
- `get_all_chain_wallet_balances(address=None)`

**Orderbook Data (1 second):**
- `get_orderbook(pair)`

**Note:** Write operations (e.g., `add_order()`, `cancel_order()`, `deposit()`, `withdraw()`) are **never cached** to ensure data integrity.

### Per-User Caching

Balance data is cached per user address. When `address=None`, the SDK uses the connected wallet's address:

```python
# Each user gets their own cached balance data
balance1 = await client.get_portfolio_balance("USDC")  # Uses connected wallet
balance2 = await client.get_portfolio_balance("USDC", address="0xOtherUser")  # Different cache entry
```

### Performance Impact

Expected reduction in API calls:
- **Static data**: ~99.9% fewer calls (1 call per hour vs. every request)
- **Semi-static data**: ~95% fewer calls (1 call per 15 min vs. frequent polling)
- **Balance data**: Significant reduction for applications polling balances
- **Orderbook data**: Useful for multi-component applications

See `examples/caching_demo.py` for complete examples.

## Configuration

The SDK uses a centralized configuration system (`DexalotConfig`) that supports multiple initialization methods.

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `parent_env` | `str` | `"fuji-multi"` | Environment configuration (e.g., `production-multi-avax`, `fuji-multi`) |
| `api_base_url` | `str` | Auto-detected | Base URL for Dexalot API (derived from `parent_env`) |
| `private_key` | `str` | `None` | Wallet private key for signing transactions |
| `enable_cache` | `bool` | `True` | Enable/disable all caching behavior |
| `timeouts` | `tuple` | `(5, 30)` | Connect/Read timeouts for HTTP requests |
| `log_level` | `str` | `"INFO"` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `log_format` | `str` | `"console"` | Log output format (`console`, `json`) |
| `connection_pool_limit` | `int` | `100` | Total connection pool size across all hosts |
| `connection_pool_limit_per_host` | `int` | `30` | Maximum connections per individual host |

### Retry Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `retry_enabled` | `bool` | `True` | Enable/disable automatic retry |
| `retry_max_attempts` | `int` | `3` | Maximum number of retry attempts |
| `retry_initial_delay` | `float` | `1.0` | Initial delay in seconds before first retry |
| `retry_max_delay` | `float` | `10.0` | Maximum delay in seconds between retries |
| `retry_exponential_base` | `float` | `2.0` | Exponential backoff multiplier |
| `retry_on_status` | `tuple` | `(429, 500, 502, 503, 504)` | HTTP status codes that trigger retry |
| `retry_on_exceptions` | `tuple` | `(aiohttp.ClientError, asyncio.TimeoutError)` | Exception types that trigger retry |

### Rate Limiting Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `rate_limit_enabled` | `bool` | `True` | Enable/disable rate limiting |
| `rate_limit_requests_per_second` | `float` | `5.0` | Maximum API requests per second |
| `rate_limit_rpc_per_second` | `float` | `10.0` | Maximum RPC calls per second |

### Nonce Manager Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `nonce_manager_enabled` | `bool` | `True` | Enable/disable nonce manager (prevents race conditions) |

### WebSocket Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `ws_manager_enabled` | `bool` | `False` | Enable/disable WebSocket Manager (persistent connections) |
| `ws_ping_interval` | `int` | `30` | Seconds between ping messages |
| `ws_ping_timeout` | `int` | `10` | Seconds to wait for pong before reconnecting |
| `ws_reconnect_initial_delay` | `float` | `1.0` | Initial reconnect delay in seconds |
| `ws_reconnect_max_delay` | `float` | `60.0` | Maximum reconnect delay in seconds |
| `ws_reconnect_exponential_base` | `float` | `2.0` | Exponential backoff multiplier |
| `ws_reconnect_max_attempts` | `int` | `10` | Maximum reconnection attempts (0 = infinite) |
| `ws_time_offset_ms` | `int` | `0` | Clock skew compensation added to timestamps in WebSocket auth messages |

### Precedence

Configuration values are resolved in the following order (highest to lowest priority):

1. **Constructor Arguments**: Passed directly to `DexalotClient`
   ```python
   # 1. Highest Priority
   client = DexalotClient(parent_env="custom-env")
   ```
2. **Environment Variables**: System-level variables
   ```bash
   # 2. High Priority
   export PARENTENV="production-multi-avax"
   ```
3. **`.env` File**: Variables loaded from local `.env` file
   ```ini
   # 3. Medium Priority
   PARENTENV=fuji-multi
   ```
4. **Defaults**: Hardcoded SDK defaults (`fuji-multi`)

### Advanced Configuration

For complex setups, you can pass a `DexalotConfig` object directly:

```python
from dexalot_sdk import DexalotClient
from dexalot_sdk.core.config import DexalotConfig

config = DexalotConfig(
    parent_env="production-multi-subnet",
    timeouts=(10, 60),
    enable_cache=False
)

client = DexalotClient(config=config)
```

## Provider Failover

The SDK includes automatic RPC provider failover to improve reliability when a single RPC endpoint fails. This feature allows you to configure multiple RPC endpoints per chain, with automatic failover to backup providers when the primary provider fails.

### Features

- **Multiple Providers**: Configure multiple RPC endpoints per chain (primary + fallbacks)
- **Fail-Fast Strategy**: Automatically switches to the next provider when the current one fails
- **Health Tracking**: Tracks provider health (failure counts, last failure time)
- **Automatic Recovery**: Failed providers are retried after a cooldown period
- **Async-Safe**: Concurrent operations are handled safely with asyncio locks; lock-free fast path when the primary provider is healthy

### Configuration

Provider failover is **enabled by default**. You can configure it via environment variables or `DexalotConfig`:

| Variable | Description | Default |
|----------|-------------|---------|
| `DEXALOT_PROVIDER_FAILOVER_ENABLED` | Enable/disable failover | `true` |
| `DEXALOT_PROVIDER_FAILOVER_COOLDOWN` | Seconds before retrying failed provider | `60` |
| `DEXALOT_PROVIDER_FAILOVER_MAX_FAILURES` | Max failures before marking provider unhealthy | `3` |

### RPC Provider Override

You can override RPC endpoints for specific chains using environment variables. This is useful for:
- Adding backup providers for redundancy
- Using custom RPC endpoints
- Testing with different providers

Two formats are supported:

1. **Chain ID format (preferred)**: `DEXALOT_RPC_<CHAIN_ID>=url1,url2,url3`
2. **Native token symbol format**: `DEXALOT_RPC_<NATIVE_TOKEN_SYMBOL>=url1,url2,url3`

Chain ID takes precedence over native token symbol if both are set. Examples:

```bash
# Chain ID format (preferred)
DEXALOT_RPC_43114=https://api.avax.network/ext/bc/C/rpc,https://avalanche.public-rpc.com
DEXALOT_RPC_1=https://eth.llamarpc.com,https://ethereum.public-rpc.com
DEXALOT_RPC_42161=https://arb1.arbitrum.io/rpc
DEXALOT_RPC_432204=https://subnets.avax.network/dexalot/mainnet/rpc

# Native token symbol format (alternative)
DEXALOT_RPC_AVAX=https://api.avax.network/ext/bc/C/rpc,https://avalanche.public-rpc.com
DEXALOT_RPC_ETH=https://eth.llamarpc.com,https://ethereum.public-rpc.com
DEXALOT_RPC_ALOT=https://subnets.avax.network/dexalot/mainnet/rpc
```

### How It Works

1. **Provider Initialization**: When the client initializes, it loads RPC endpoints from:
   - Environment variable overrides (if set)
   - API response (from Dexalot API)
   - Multiple URLs can be provided (comma-separated)

2. **Failover Strategy**: When an RPC call fails:
   - The failed provider is marked with a failure count
   - The SDK automatically tries the next available provider
   - If all providers fail, an error is raised

3. **Health Tracking**: Each provider tracks:
   - Failure count (incremented on each failure)
   - Last failure time (for cooldown calculation)
   - Health status (healthy/unhealthy)

4. **Recovery**: After the cooldown period, failed providers can be retried. Providers are marked as unhealthy only after exceeding the max failure threshold.

### Example

```python
from dexalot_sdk import DexalotClient
from dexalot_sdk.core.config import DexalotConfig

# Configure failover
config = DexalotConfig(
    provider_failover_enabled=True,
    provider_failover_cooldown=60,  # 60 seconds cooldown
    provider_failover_max_failures=3,  # Mark unhealthy after 3 failures
)

client = DexalotClient(config=config)
await client.connect()
await client.initialize_client()

# RPC calls will automatically use failover if primary provider fails
# No code changes needed - failover is transparent
```

### Backwards Compatibility

- If `provider_failover_enabled=False`, behavior matches the original implementation (single provider)
- Single provider from API response works as before
- Environment variable override is optional
- All existing code paths continue to work

### RPC Security Settings

By default, plain `http://` RPC URLs are **rejected at provider setup time** with a `ValueError`. This prevents accidental use of unencrypted connections in production.

| Option | Env Variable | Default | Description |
|--------|-------------|---------|-------------|
| `allow_insecure_rpc` | `DEXALOT_ALLOW_INSECURE_RPC` | `false` | Allow plain `http://` RPC endpoints |

> **Security note:** Plain `http://` RPC connections transmit JSON-RPC calls (including signed transactions) without encryption. In production, always use `https://` endpoints. Only set `allow_insecure_rpc=True` for local development or trusted private networks.

```python
# Allow http:// for local development only
config = DexalotConfig(allow_insecure_rpc=True)
client = DexalotClient(config=config)
```

## Observability

The SDK includes a comprehensive instrumentation layer to track API operations, performance metrics, and WebSocket events.

### Features

- **Structured Logging**: Logs are output in JSON format (or plain text) with metadata.
- **Performance Tracking**: Automatically tracks the duration of all core operations (`clob`, `swap`, `transfer`).
- **Security**: Designed with privacy by default:
  - **No Arguments**: Function arguments and return values are **never logged**.
  - **No Payloads**: Transaction payloads and private keys are **never logged**.
  - **Safe Defaults**: Minimal logging in production (`INFO`), detailed tracing only in `DEBUG`.

### Configuration

Control logging behavior using environment variables:

| Variable | Description | Default | Values |
|----------|-------------|---------|--------|
| `DEXALOT_LOG_LEVEL` | Logging verbosity | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `DEXALOT_LOG_FORMAT` | Log output format | `console` | `json`, `console` |

### Instrumented Components

- **CLOB**: Full coverage of Order Management (`add`/`cancel`/`replace`), Market Data (`orderbook`, `pairs`), and Account Data (`open_orders`).
- **Swap**: RFQ operation lifecycle including Firm/Soft Quotes and Swap Execution.
- **Transfer**: Cross-chain Bridge operations (`deposit`/`withdraw`), Portfolio Management (`transfer_portfolio`), and comprehensive Balance queries.
- **WebSocket**: Connection lifecycle events (`Open`/`Close`/`Error`) and message traffic (at `DEBUG` level).

### Example Output

```json
{
  "timestamp": "2023-10-27T10:00:00Z",
  "level": "INFO",
  "logger": "dexalot_sdk.core.clob",
  "message": "clob completed in 0.123s",
  "extra_fields": {
    "operation": "clob",
    "function": "add_order",
    "duration": 0.123,
    "status": "success"
  }
}
```

See `examples/logging_console.py` for a demonstration of logging capabilities.

## Resource Cleanup

The SDK manages several resources that need proper cleanup:
- **HTTP sessions** (`aiohttp.ClientSession`)
- **Web3 provider sessions** (internal `aiohttp` sessions)
- **WebSocket connections** (if WebSocket manager is enabled)

### Always Close the Client

Always call `await client.close()` when you're done with the client to ensure proper resource cleanup:

```python
async def main():
    client = None
    try:
        client = DexalotClient()
        await client.initialize_client()
        
        # Your operations here
        result = await client.get_tokens()
        if result.success:
            print(f"Tokens: {result.data}")
    finally:
        # Always close the client in a finally block
        if client is not None:
            await client.close()
```

### Context Manager (Alternative)

You can also use the client as an async context manager:

```python
async def main():
    async with DexalotClient() as client:
        await client.initialize_client()
        
        # Your operations here
        result = await client.get_tokens()
        if result.success:
            print(f"Tokens: {result.data}")
    # Client is automatically closed when exiting the context
```

**Note:** The `close()` method:
- Closes all HTTP sessions (SDK's main session and web3 provider sessions)
- Closes WebSocket connections (if enabled)
- Waits for graceful SSL connection shutdown
- Is safe to call multiple times (idempotent)

## Async Usage

The SDK is **fully async** - all methods are `async def` and must be awaited. This enables concurrent operations and better performance.

### Script Usage (asyncio.run)

For standalone scripts, use `asyncio.run()`:

```python
import asyncio
from dexalot_sdk import DexalotClient

async def main():
    client = None
    try:
        client = DexalotClient()
        await client.initialize_client()
        
        # Your async operations here
        result = await client.get_tokens()
        if result.success:
            print(f"Tokens: {result.data}")
    finally:
        if client is not None:
            await client.close()

if __name__ == "__main__":
    asyncio.run(main())
```

### Application Usage (async context)

In async applications (e.g., FastAPI, async web frameworks), use `await` directly:

```python
from fastapi import FastAPI
from dexalot_sdk import DexalotClient

app = FastAPI()
client = DexalotClient()

@app.on_event("startup")
async def startup():
    await client.initialize_client()

@app.on_event("shutdown")
async def shutdown():
    await client.close()

@app.get("/tokens")
async def get_tokens():
    result = await client.get_tokens()
    if result.success:
        return result.data
    return {"error": result.error}
```

### Parallel Operations

The async architecture enables parallel operations for better performance:

```python
import asyncio
from dexalot_sdk import DexalotClient

async def main():
    client = None
    try:
        client = DexalotClient()
        await client.initialize_client()
        
        # Fetch multiple orderbooks in parallel
        pairs = ["AVAX/USDC", "ALOT/USDC", "ETH/USDC"]
        results = await asyncio.gather(
            *[client.get_orderbook(pair) for pair in pairs]
        )
        
        for pair, result in zip(pairs, results, strict=True):
            if result.success:
                print(f"{pair}: {len(result.data['bids'])} bids")
    finally:
        if client is not None:
            await client.close()
```

See `examples/async_parallel.py` for more parallel operation examples.

## Error Handling

The SDK uses a `Result[T]` pattern for consistent error handling across async operational methods.

### Result Pattern

Async operational methods return `Result[T]` with three fields:
- `success: bool` - True if operation succeeded
- `data: T | None` - Result data on success, None on error
- `error: str | None` - Error message on failure, None on success

### Basic Error Handling

```python
result = await client.add_order(
    pair="AVAX/USDC",
    side="BUY",
    amount=1.0,
    price=25.0
)

if result.success:
    tx_hash = result.data["tx_hash"]
    print(f"Order placed: {tx_hash}")
else:
    print(f"Order failed: {result.error}")
    # Handle error (retry, log, notify user, etc.)
```

### Validation Errors

Input validation errors are returned as `Result.fail()` with descriptive messages:

```python
# Invalid amount (negative)
result = await client.add_order(
    pair="AVAX/USDC",
    side="BUY",
    amount=-1.0,  # Invalid!
    price=25.0
)

if not result.success:
    # result.error will be: "Invalid amount: must be positive (> 0), got -1.0"
    print(f"Validation error: {result.error}")
```

### Error Sanitization

Error messages are automatically sanitized to prevent information leakage:
- File paths are removed
- URLs are removed
- Stack traces are removed
- User-friendly messages are provided

### Best Practices

1. **Always check `result.success`** before accessing `result.data`
2. **Handle errors appropriately** - log, retry, or notify users
3. **Use descriptive error messages** - the SDK provides clear error messages
4. **Don't expose internal errors** - error sanitization is automatic

```python
async def place_order_safely(client, pair, side, amount, price):
    result = await client.add_order(pair, side, amount, price)
    
    if result.success:
        return {"status": "success", "tx_hash": result.data["tx_hash"]}
    else:
        # Log error for debugging
        logger.error(f"Order failed: {result.error}")
        # Return user-friendly message
        return {"status": "error", "message": "Failed to place order. Please try again."}
```

See `examples/error_handling.py` for comprehensive error handling patterns.

## Transaction Receipt Handling

All state-changing operations (placing orders, deposits, withdrawals, etc.) now support a `wait_for_receipt` parameter that controls whether the SDK waits for blockchain transaction confirmation before returning.

### Default Behavior

By default, **all state-changing operations wait for transaction receipts** (`wait_for_receipt=True`). This ensures:
- Transactions are confirmed on-chain before the method returns
- Transaction failures are detected immediately
- More reliable operation results

### Usage

```python
# Default behavior: waits for receipt (recommended)
result = await client.add_order("AVAX/USDC", "BUY", 1.0, 25.0)
# Method returns only after transaction is confirmed

# Explicitly wait for receipt
result = await client.add_order(
    "AVAX/USDC", "BUY", 1.0, 25.0, 
    wait_for_receipt=True
)

# Don't wait for receipt (returns immediately after sending)
result = await client.add_order(
    "AVAX/USDC", "BUY", 1.0, 25.0, 
    wait_for_receipt=False
)
# Method returns immediately with transaction hash
# Transaction may still be pending
```

### When to Use `wait_for_receipt=False`

Use `wait_for_receipt=False` when:
- **Batch operations**: Sending many transactions and want to submit them quickly
- **Fire-and-forget**: You don't need immediate confirmation
- **Custom polling**: You'll check transaction status yourself

**Important**: When `wait_for_receipt=False`, the SDK returns immediately after broadcasting the transaction. You should:
- Check transaction status yourself using the returned `tx_hash`
- Handle potential transaction failures in your application logic
- Be aware that the transaction may still be pending when the method returns

### Affected Methods

All state-changing methods support `wait_for_receipt`:

**CLOB Operations:**
- `add_order(pair, side, amount, price, order_type="LIMIT", wait_for_receipt=True)`
- `add_limit_order_list(orders, wait_for_receipt=True)`
- `cancel_order(order_id, wait_for_receipt=True)`
- `cancel_list_orders(order_ids, wait_for_receipt=True)`
- `cancel_list_orders_by_client_id(client_order_ids, wait_for_receipt=True)`
- `replace_order(order_id, new_price, new_amount, wait_for_receipt=True)`
- `cancel_add_list(replacements, wait_for_receipt=True)`

**Transfer Operations:**
- `deposit(token, amount, source_chain, use_layerzero=False, wait_for_receipt=True)`
- `withdraw(token, amount, destination_chain, use_layerzero=False, wait_for_receipt=True)`
- `add_gas(amount, wait_for_receipt=True)`
- `remove_gas(amount, wait_for_receipt=True)`
- `transfer_portfolio(token, amount, to_address, wait_for_receipt=True)`

**Swap Operations:**
- `execute_rfq_swap(quote, wait_for_receipt=True)`

### Example: Batch Order Placement

```python
# Place multiple orders without waiting for each receipt
orders = [
    {"pair": "AVAX/USDC", "side": "BUY", "amount": 1.0, "price": 25.0},
    {"pair": "AVAX/USDC", "side": "BUY", "amount": 2.0, "price": 24.0},
    {"pair": "AVAX/USDC", "side": "SELL", "amount": 1.0, "price": 26.0},
]

# Submit all orders quickly without waiting
result = await client.add_limit_order_list(orders, wait_for_receipt=False)
if result.success:
    tx_hash = result.data["tx_hash"]
    # Check status later
    # await check_transaction_status(tx_hash)
```

### Example: Fire-and-Forget Deposit

```python
# Submit deposit and continue with other operations
result = await client.deposit("AVAX", 1.0, "Avalanche", wait_for_receipt=False)
if result.success:
    tx_hash = result.data  # Just the transaction hash
    # Continue with other operations
    # Monitor deposit status separately
```

## API Field Name Standardization

The SDK automatically standardizes API response field names to match Python naming conventions (snake_case). This ensures consistent field names regardless of API response format variations.

### Standardized Fields

**Orders API:**
- `clientOrderId` (from `clientordid`, `client_order_id`)
- `tradePairId`, `filledQuantity`, `totalAmount`, `totalFee`, `txHash`

**Environments API:**
- `chain_id` (from `chainid`, `chainId`)
- `env_type` (from `type`, `envType`)
- `rpc` (from `chain_instance`)
- `network` (from `chain_display_name`)

**Tokens API:**
- `evm_decimals` (from `evmdecimals`, `evmDecimals`, `decimals`)
- `chain_id` (from `chainid`, `chainId`)
- `network` (from `chain_display_name`)

**Pairs API:**
- `base_decimals`, `quote_decimals`
- `base_display_decimals`, `quote_display_decimals`
- `min_trade_amount`, `max_trade_amount`

**RFQ Quotes API:**
- `chainId` (from `chainid`, `chain_id`)
- `secureQuote` (from `securequote`, `secure_quote`)
- `quoteId` (from `quoteid`, `quote_id`)
- Nested order data: `nonceAndMeta`, `makerAsset`, `takerAsset`, `makerAmount`, `takerAmount`

**Deployment API:**
- `env`, `address`, `abi` (handles variations like `Env`, `Address`, `Abi`)

### Benefits

- **Consistent Interface**: Always use snake_case field names in Python
- **Backward Compatible**: Existing code continues to work
- **Future-Proof**: Handles API field name variations automatically
- **No Code Changes**: Transformation happens transparently

All API responses are automatically transformed before being returned, so you can always rely on standardized field names.

## Reliability Features

The SDK includes several reliability features that work automatically to improve stability and performance.

### Retry Mechanism

Automatic retry with exponential backoff for transient failures:

- **Default**: 3 attempts with exponential backoff (1s, 2s, 4s)
- **Retries on**: HTTP 429, 500, 502, 503, 504 and network errors
- **Configurable**: Via `DexalotConfig` or environment variables

```python
from dexalot_sdk import DexalotClient
from dexalot_sdk.core.config import DexalotConfig

# Custom retry configuration
config = DexalotConfig(
    retry_enabled=True,
    retry_max_attempts=5,
    retry_initial_delay=2.0,  # Start with 2s delay
    retry_max_delay=30.0,      # Max 30s between retries
    retry_exponential_base=2.0
)

client = DexalotClient(config=config)
```

### Rate Limiting

Token bucket rate limiter prevents API throttling:

- **Default**: 5 requests/second for API, 10 requests/second for RPC
- **Automatic**: Applied to all HTTP and RPC calls
- **Configurable**: Via `DexalotConfig` or environment variables

```python
config = DexalotConfig(
    rate_limit_enabled=True,
    rate_limit_requests_per_second=10.0,  # 10 API calls/second
    rate_limit_rpc_per_second=20.0         # 20 RPC calls/second
)
```

### Nonce Manager

Automatic nonce management prevents transaction race conditions:

- **Automatic**: Tracks nonces per (chain_id, address) combination
- **Thread-safe**: Uses async locks for concurrent transactions
- **Transparent**: No code changes needed

The nonce manager is enabled by default and works automatically. It:
1. Fetches the current nonce from the chain on first use
2. Tracks nonces locally for subsequent transactions
3. Automatically increments nonces for each transaction
4. Prevents race conditions in concurrent scenarios

```python
# Nonce manager works automatically - no configuration needed
# For high-concurrency scenarios, it's already handling nonces correctly

# Multiple transactions can be sent concurrently
tasks = [
    client.add_order("AVAX/USDC", "BUY", 1.0, 25.0),
    client.add_order("ALOT/USDC", "BUY", 10.0, 0.5),
    client.deposit("AVAX", 1.0)
]
results = await asyncio.gather(*tasks)
# Nonce manager ensures correct nonce ordering
```

### Provider Failover

Automatic RPC provider failover (see [Provider Failover](#provider-failover) section above).

## WebSocket Manager

The SDK includes a persistent WebSocket manager for long-running subscriptions with automatic reconnection and heartbeat.

### Features

- **Persistent Connections**: Single connection for multiple subscriptions
- **Multiple Subscriptions**: Subscribe to multiple topics with individual callbacks
- **Automatic Reconnection**: Exponential backoff reconnection on failures
- **Heartbeat Monitoring**: Ping/pong mechanism to detect dead connections
- **Thread-Safe**: Safe for concurrent use

### Basic Usage

```python
from dexalot_sdk import DexalotClient

async def main():
    client = None
    try:
        client = DexalotClient()
        await client.initialize_client()
        
        # Enable WebSocket manager
        config = client.config
        config.ws_manager_enabled = True
        
        # Subscribe to orderbook updates
        def on_orderbook_update(message):
            print(f"Orderbook update: {message}")
        
        await client.subscribe_to_events(
            topic="OrderBook/AVAX/USDC",
            callback=on_orderbook_update,
            is_private=False
        )
        
        # Subscribe to private order updates
        def on_order_update(message):
            print(f"Order update: {message}")
        
        await client.subscribe_to_events(
            topic="Orders",
            callback=on_order_update,
            is_private=True
        )
        
        # Keep connection alive
        await asyncio.sleep(60)
        
        # Unsubscribe when done
        client.unsubscribe_from_events("OrderBook/AVAX/USDC")
    finally:
        # Always close the client to clean up WebSocket and HTTP sessions
        if client is not None:
            await client.close()

asyncio.run(main())
```

### Configuration

```python
from dexalot_sdk.core.config import DexalotConfig

config = DexalotConfig(
    ws_manager_enabled=True,
    ws_ping_interval=30,        # Ping every 30 seconds
    ws_ping_timeout=10,         # Wait 10s for pong before reconnecting
    ws_reconnect_initial_delay=1.0,
    ws_reconnect_max_delay=60.0,
    ws_reconnect_exponential_base=2.0,
    ws_reconnect_max_attempts=10  # 0 = infinite retries
)

client = DexalotClient(config=config)
```

### One-Off Connections

Use `subscribe_to_events()` with the manager enabled:

```python
async def on_message(message):
    print(f"Received: {message}")

# Start the manager on first subscription
await client.subscribe_to_events(
    topic="OrderBook/AVAX/USDC",
    callback=on_message,
    is_private=False
)

# Later, unsubscribe and close the client when done
client.unsubscribe_from_events("OrderBook/AVAX/USDC")
```

See `examples/websocket_manager.py` for complete examples.

## Input Validation

The SDK automatically validates all input parameters before processing operations. This prevents invalid data from reaching the blockchain or API. Validation is implemented in `utils/input_validators.py` and returns `Result[None]` for consistent error handling.

### Automatic Validation

Input validation is applied to all critical methods:

- **CLOB Operations**: `add_order()`, `cancel_order()`, `get_orderbook()`, etc.
- **Swap Operations**: `execute_rfq_swap()`, `get_swap_firm_quote()`, etc.
- **Transfer Operations**: `deposit()`, `withdraw()`, `transfer_portfolio()`, etc.

### Validation Rules

- **Amounts**: Must be positive, finite numbers (not NaN or infinite)
- **Prices**: Must be positive, finite numbers
- **Addresses**: Must be valid Ethereum addresses (0x prefix, 42 chars, hex)
- **Pairs**: Must be in `TOKEN/TOKEN` format
- **Order IDs**: Must be valid hex strings or bytes32 format
- **Token Symbols**: Must be non-empty, alphanumeric strings

### Handling Validation Errors

Validation errors are returned as `Result.fail()` with descriptive messages:

```python
# Invalid amount
result = await client.add_order(
    pair="AVAX/USDC",
    side="BUY",
    amount=-1.0,  # Invalid: negative amount
    price=25.0
)

if not result.success:
    # result.error: "Invalid amount: must be positive (> 0), got -1.0"
    print(result.error)

# Invalid address
result = await client.get_portfolio_balance(
    token="USDC",
    address="invalid"  # Not a valid Ethereum address
)
if not result.success:
    # result.error: "Invalid address: must be a valid Ethereum address (0x prefix, 42 chars, hex)"
    print(result.error)
```

### Common Validation Errors

| Error | Cause | Solution |
|-------|-------|----------|
| "Invalid amount: must be positive" | Negative or zero amount | Use positive values |
| "Invalid address: must be a valid Ethereum address" | Invalid address format | Use 0x-prefixed hex addresses |
| "Invalid pair: must be in TOKEN/TOKEN format" | Invalid pair format | Use format like "AVAX/USDC" |
| "Invalid order_id: must be hex string or bytes32" | Invalid order ID | Use valid hex string |

Validation happens before any network calls, so invalid inputs fail fast with clear error messages.
