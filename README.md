# Dexalot Python SDK

## Disclaimer

Here is our public release of Dexalot SDK for Python.  It is in beta testing right now.  Fork it, contribute to it and use it to integrate with Dexalot and let us know how we can improve it.

**Please Note**: The public interface may undergo breaking changes.

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
- **`utils/token_normalization.py`**: Normalize user token symbols and `BASE/QUOTE` pairs (strip, ASCII uppercase, optional aliases from `data/token_aliases.json`)
- **`utils/cache.py`**: TTL-based caching utilities (`MemoryCache`, `ttl_cached`, `async_ttl_cached`)
- **`utils/observability.py`**: Structured logging and operation tracking
- **`utils/result.py`**: Standardized `Result[T]` type for consistent error handling
- **`utils/retry.py`**: Async retry decorator with exponential backoff
- **`utils/rate_limit.py`**: Token bucket rate limiter for API and RPC calls
- **`utils/nonce_manager.py`**: Async-safe nonce management to prevent transaction race conditions
- **`utils/provider_manager.py`**: RPC provider failover with health tracking
- **`utils/error_sanitizer.py`**: Error message sanitization to prevent information leakage
- **`utils/websocket_manager.py`**: Persistent WebSocket connection manager with reconnection and heartbeat

### Token and pair inputs

User-facing methods accept common symbol variants: surrounding whitespace is ignored, symbols are folded to ASCII uppercase, and a small alias map (for example **ETHER** → **ETH**, **BITCOIN** → **BTC**) is applied after format validation. Trading pairs are normalized per leg (`eth/usdc` → `ETH/USDC`). Examples in this repo use canonical symbols; callers may pass mixed case or aliases interchangeably.

## Installation

**Requirements:** Python 3.12, 3.13, or 3.14.

Install from PyPI:

```sh
pip install dexalot-sdk
```

For local development with `uv`:

```sh
uv venv
uv sync --group dev
```

Or with pip in editable mode from the repository root:

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

### Order History

```python
result = await client.get_order_history(
    pair="ALOT/USDC",
    status="FILLED",
    limit=50,
)
if result.success:
    for order in result.data:
        print(f"{order['pair']} {order['side']} {order['quantity']} @ {order['price']}")
```

## Dependencies

- `web3>=6.0.0`: Multi-chain blockchain interactions (AsyncWeb3 for async operations)
- `aiohttp`: Async HTTP client for Dexalot API communication
- `python-dotenv`: Environment variable management
- `eth-account`: Ethereum account management and transaction signing
- `websockets`: Async WebSocket client for real-time event subscriptions

## Scripts

The published package includes the `secrets-vault` console command:

```sh
secrets-vault keygen
```

Repository maintenance utilities such as `scripts/version_manager.py` are kept in the repo and are not part of the installed package.

## Testing

Run tests from the repository root:

```sh
make test  # Unit tests
make cov   # Coverage report
```

## Release

Releases are **tag-driven**. Pushing a `v*` tag to `main` triggers
`.github/workflows/pypi.yml`, which builds the sdist + wheel and
publishes to PyPI via trusted publishing (OIDC — no long-lived API
token is stored anywhere). The workflow also supports
`workflow_dispatch` for manual reruns (the tag/version match check
still fires).

**Release gate (enforced by the workflow):** the tag name must equal
`v{project.version}` in `pyproject.toml`; the workflow aborts with a
`SystemExit` otherwise.

**Steps:**

1. Sync the version across all version-bearing files via the
   maintenance script:

   ```sh
   python scripts/version_manager.py <new-version>
   # touches VERSION, pyproject.toml, src/dexalot_sdk/__init__.py, uv.lock
   ```

2. Review the diff, commit on a feature branch, and merge a PR into
   `main`.

3. From `main`, tag and push:

   ```sh
   git checkout main && git pull
   git tag -a v<new-version> -m "Release v<new-version>"
   git push origin v<new-version>
   ```

4. Watch the **Publish to PyPI** workflow in GitHub Actions. On green,
   verify the new version at <https://pypi.org/p/dexalot-sdk>.

> ⚠️ Once a version is published to PyPI it cannot be re-uploaded
> under the same number — it can only be *yanked*. Always bump the
> version before tagging.


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
- `get_deployment()` (also caches per `(env, contract_type, return_abi)` filter combination)
- `get_token_price_history(token, *, from_ts=None, to_ts=None)`
- `get_token_hourly_price_history(token, *, from_ts=None, to_ts=None)`

**Semi-Static Data (15 minutes):**
- `get_tokens()`
- `get_clob_pairs()`
- `get_swap_pairs(chain_identifier)`
- `get_token_usd_prices(env=None)`

**Balance Data (10 seconds):**
- `get_portfolio_balance(token, address=None)`
- `get_all_portfolio_balances(address=None)`
- `get_chain_wallet_balance(chain, token, address=None)`
- `get_chain_wallet_balances(chain, address=None)`
- `get_chain_token_balances(chain, address=None, tokens=...)`
- `get_all_chain_wallet_balances(address=None)`
- `get_order_history(account=None, *, pair=None, status=None, limit=100, offset=0)`
- `get_combined_transfers(*, kind=None, from_ts=None, to_ts=None, limit=100, offset=0)`

**Orderbook Data (1 second):**
- `get_orderbook(pair)`
- `get_candles(pair, interval, limit)`
- `get_market_snapshot()`

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

# RPC calls use failover automatically when the primary provider fails (if enabled)
```

### Provider failover behavior

- With `provider_failover_enabled=False`, only the primary RPC URL is used (no rotation).
- When the API returns a single provider entry, the client uses that URL directly.
- Environment variables can override failover settings as documented above.

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
    price=25.0,
)

if result.success:
    print(f"Order placed: {result.data['tx_hash']}")
    print(f"Client order ID: {result.data['client_order_id']}")  # save for cancel/replace
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

### Backend reason codes

Errors from the Dexalot REST API include structured `reasonCode` (e.g. `FQ-015`, `P-AFNE-02`, `T-TMDQ-01`, `RF-IMV-01`) and human `reason` fields. These are preserved verbatim in `Result.fail()` messages — you'll see `"FQ-015: insufficient liquidity"` rather than the generic `"Request failed with status code 400"`. Pattern-match on the code prefix to react programmatically:

```python
result = await client.get_swap_firm_quote("USDC", "AVAX", 100)
if not result.success and result.error.startswith("FQ-"):
    # RFQ backend rejected the quote — see the reason code prefix for why
    ...
```

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
- `add_order(pair, side, amount, price, order_type="LIMIT", client_order_id=None, wait_for_receipt=True)`
- `add_limit_order_list(orders, wait_for_receipt=True)`
- `cancel_order(order_id, wait_for_receipt=True)`
- `cancel_order_by_client_id(client_order_id, wait_for_receipt=True)`
- `cancel_list_orders(order_ids, wait_for_receipt=True)`
- `cancel_list_orders_by_client_id(client_order_ids, wait_for_receipt=True)`
- `replace_order(order_id, new_price, new_amount, client_order_id=None, wait_for_receipt=True)`
- `cancel_add_list(replacements, wait_for_receipt=True)`

### Order ID Semantics

Every canonical SDK order has two identifiers, one transaction hash for state-changing actions, and a normalized order shape for reads:

| Field | Source | Description |
|---|---|---|
| `internal_order_id` | Contract-assigned | Assigned by the TradePairs contract at placement; present in all order data returned by `get_open_orders`, `get_order`, and `get_order_by_client_id` |
| `client_order_id` | Caller-specified | Provided by the caller at placement (or generated by the SDK when omitted); echoed back in every placement and cancel/replace result |
| `tx_hash` | Blockchain | Transaction hash for the on-chain action; not an order identifier |

Order reads (`get_open_orders`, `get_order`, `get_order_by_client_id`) return a full canonical order dict with these fields:

- `internal_order_id`, `client_order_id`, `trade_pair_id`, `pair`
- `price`, `total_amount`, `quantity`, `quantity_filled`, `total_fee`
- `trader_address`, `side`, `type1`, `type2`, `status`
- `update_block`, `create_block`, `create_ts`, `update_ts`

Enum-style fields are normalized to human-readable strings such as `BUY`, `SELL`, `LIMIT`, `GTC`, and `FILLED`. Block fields are returned as Python integers, not hex strings.

**Placement methods** — all four placement functions (`add_order`, `add_limit_order_list`, `replace_order`, `cancel_add_list`) accept an optional `client_order_id`. When omitted, the SDK generates a random 32-byte value. The result always contains `client_order_id` (or `client_order_ids` for batch calls) so you can record the ID for later operations.

**Cancel/replace results** — cancel and replace methods return typed ID fields so you always know exactly what was cancelled and what was created:

| Method | Returns |
|---|---|
| `cancel_order` | `cancelled_client_order_id`, `cancelled_internal_order_id` |
| `cancel_order_by_client_id` | `cancelled_client_order_id` |
| `cancel_list_orders` | `cancelled_internal_order_ids` |
| `cancel_list_orders_by_client_id` | `cancelled_client_order_ids` |
| `replace_order` | `cancelled_client_order_id`, `cancelled_internal_order_id`, `client_order_id` (new) |
| `cancel_add_list` | `cancelled_client_order_ids`, `cancelled_internal_order_ids`, `client_order_ids` (new) |

**Routing by identifier type:**

- `get_order()` and `cancel_order()` and `replace_order()` accept **either** type (`order_id` parameter).
- `get_order_by_client_id()` and `cancel_order_by_client_id()` take a `client_order_id` specifically.
- `cancel_add_list()` accepts `order_id` per replacement (either type); also infers `pair` from the existing order when possible.
- **Never** pass `tx_hash` to order lookup, cancel, or replace methods.

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
- `internal_order_id` (from `id`)
- `client_order_id` (from `clientordid`, `clientOrderId`)
- `trade_pair_id` (from `tradePairId`, or derived from `pair` when needed)
- `pair`, `price`, `quantity`, `total_amount`, `quantity_filled`, `total_fee`
- `trader_address`, `side`, `type1`, `type2`, `status`
- `create_block`, `update_block`, `create_ts`, `update_ts`

Orders are normalized into one canonical SDK shape regardless of whether the source was the REST API or the contract.

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
- HTTP envelope `{"success": true, "quote": {...}}` is unwrapped so callers see only the inner executable quote.  Envelope-layer failures (`success: false`) become `Result.fail(...)` at the HTTP layer using the API's `reason`/`error` field.
- `chain_id` (from `chainid`, `chainId`)
- `quote_id` (from `quoteid`, `quoteId`)
- Top-level fields preserved as-is: `signature`, `order`, `tx`, `expiry`, `nonceAndMeta`, `pair`, `price`, `side`, `minOutputAmount`, `maxSlippageBps`, `bridgeFee`, `usdAmount`, `baseAddress`, `quoteAddress`, `baseAmount`, `quoteAmount`
- Inner `order` data normalized to snake_case: `nonce_and_meta`, `maker_asset`, `taker_asset`, `maker_amount`, `taker_amount` (camelCase originals retained)

**Deployment API:**
- `env`, `address`, `abi` (handles variations like `Env`, `Address`, `Abi`)

**Price History API (`get_token_price_history`, `get_token_hourly_price_history`):**
- Returns `list[PricePoint]` with `timestamp` (unix seconds, UTC) and `price` (`float`).
- `timestamp` is normalized from `date` ISO-8601, `ts`, `timestamp`, or `time` aliases — millisecond magnitudes are auto-detected and divided down to seconds.
- `price` is coerced from a string decimal to `float`; scientific notation is supported.
- Rows are returned sorted ascending by `timestamp`.

**Combined Transfers API (`get_combined_transfers`):**
- Returns `list[Transfer]` — a frozen dataclass with snake_case fields normalized from the backend's `DBTransfer` shape: `action_type`, `status`, `symbol`, `quantity`, `fee`, `trader_address`, `bridge`, `bridge_url`, `nonce`, `source_env`, `source_chain_id`, `source_tx`, `source_ts`, `target_env`, `target_chain_id`, `target_tx`, `target_ts`.
- Numeric enums are mapped to string `Literal` labels: `status` (`COMPLETED`/`INFLIGHT`/`DELAYED`), `action_type` (10 labels including `WITHDRAWN`/`DEPOSITED`/`SENT`/`RECEIVED`/`RECOVERED`/`ADD_GAS`/`REMOVE_GAS`/`AUTO_FILL`/`WITHDRAW_PENDING`/`DEPOSIT_PENDING`), `bridge` (`NATIVE`/`LAYER0`/`CELER`/`ICM`).
- `quantity` and `fee` arrive as display-decimal strings — no wei→human conversion is needed (parsed via `float()`).

**Order History API (`get_order_history`):**
- Same canonical order dict and field aliases as `get_open_orders` — see the "Orders API" entry above.

### Benefits

- **Consistent interface**: Field names are exposed in snake_case in Python consistently.
- **Alias handling**: Common camelCase and alternate keys from the API are normalized automatically.

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
- **Default-on**: No manual nonce bookkeeping for normal use

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
- **Order IDs**: Must be valid prefixed hex, decimal-string internal IDs, bytes32 values, or plain client IDs that fit in bytes32
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
