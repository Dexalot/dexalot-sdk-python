# User Guide

End-to-end guide from installation to production usage of the Dexalot Python SDK.

---

## Prerequisites & installation

**Python ≥ 3.12** is required (match statements and PEP 695 generics are used throughout).

```bash
pip install dexalot-sdk
```

Or with [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv add dexalot-sdk
```

### Environment setup

Copy `env.example` to `.env` and fill in the required fields:

```bash
cp env.example .env
```

Minimum required fields for read-only access:

```bash
PARENTENV=fuji-multi   # testnet; use "production-multi" for mainnet
```

For signing transactions, add:

```bash
PRIVATE_KEY=0x...      # hex-encoded private key; zeroed after Account creation
```

Prefer passing a pre-built `eth_account.Account` object to the constructor instead of setting `PRIVATE_KEY` — this keeps the raw key out of the config entirely.

---

## Core concepts

### Result[T] — no exceptions

Every SDK method returns a `Result` object. The SDK **never raises** on expected failures (network errors, validation errors, contract reverts). Always check `.success` before accessing `.data`:

```python
result = await client.get_trading_pairs()
if result.success:
    pairs = result.data
else:
    print(result.error)   # human-readable error message
```

See [Error Handling](python-sdk-error-handling.md) for the full `Result[T]` reference.

### Async-first

All I/O methods are `async def`. Use `asyncio.run()` or an async framework (FastAPI, etc.) as your entry point.

### Context manager lifecycle

The recommended pattern is `async with`:

```python
async with DexalotClient() as client:
    result = await client.get_trading_pairs()
```

The context manager calls `initialize_client()` on entry and `close_websocket()` on exit, ensuring HTTP sessions and WebSocket connections are cleaned up properly.

Manual lifecycle:

```python
client = DexalotClient()
await client.initialize_client()
try:
    ...
finally:
    await client.close_websocket()
```

---

## Getting started

### List trading pairs and fetch an order book

```python
import asyncio
from dexalot_sdk import DexalotClient

async def main():
    async with DexalotClient() as client:
        # List available trading pairs
        pairs_result = await client.get_trading_pairs()
        if not pairs_result.success:
            print(f"Error: {pairs_result.error}")
            return

        for pair in pairs_result.data[:5]:
            print(pair["pair"])   # e.g. "ALOT/USDC"

        # Fetch order book for a pair
        ob_result = await client.get_orderbook("ALOT/USDC")
        if ob_result.success:
            ob = ob_result.data
            print("Best bid:", ob["rows"][0])   # first bid level

asyncio.run(main())
```

### Read-only with explicit config

```python
from dexalot_sdk import DexalotClient
from dexalot_sdk.core.config import DexalotConfig

config = DexalotConfig(
    parent_env="fuji-multi",
    log_level="DEBUG",
    enable_cache=False,
)

async with DexalotClient(config=config) as client:
    result = await client.get_tokens()
```

---

## Trading — CLOB

### Place a limit order

```python
from dexalot_sdk import DexalotClient
from eth_account import Account

signer = Account.from_key("0x...")

async with DexalotClient(signer=signer) as client:
    result = await client.add_order(
        pair="ALOT/USDC",
        price=0.15,
        quantity=100.0,
        side=0,          # 0 = BUY, 1 = SELL
        order_type=1,    # 1 = LIMIT, 4 = LIMIT_FOK, 5 = LIMIT_IOC
    )
    if result.success:
        print("Order placed. Tx:", result.data)
    else:
        print("Failed:", result.error)
```

### Cancel an order

```python
result = await client.cancel_order(pair="ALOT/USDC", order_id="0xabc...")
```

### Cancel all orders for a pair

```python
result = await client.cancel_all_orders(pair="ALOT/USDC")
```

### Batch operations

Place multiple orders in one transaction:

```python
orders = [
    {"pair": "ALOT/USDC", "price": 0.14, "quantity": 50.0, "side": 0, "order_type": 1},
    {"pair": "ALOT/USDC", "price": 0.13, "quantity": 75.0, "side": 0, "order_type": 1},
]
result = await client.add_limit_order_list(orders)
```

Cancel multiple orders by ID:

```python
result = await client.cancel_list_orders(pair="ALOT/USDC", order_ids=["0xabc...", "0xdef..."])
```

Atomic cancel-and-replace (cancel list, then place new list):

```python
result = await client.cancel_add_list(
    cancel_ids=["0xold..."],
    new_orders=[{"pair": "ALOT/USDC", "price": 0.16, "quantity": 100.0, "side": 0, "order_type": 1}],
)
```

### Query open orders

```python
result = await client.get_open_orders(pair="ALOT/USDC")
if result.success:
    for order in result.data:
        print(order["id"], order["price"], order["quantity"])
```

### Get a specific order

```python
result = await client.get_order(order_id="0xabc...")
result = await client.get_order_by_client_id(client_order_id="my-order-1")
```

---

## Simple Swap — RFQ

The swap flow is: soft quote → firm quote → execute. See [Simple Swap](simple-swap.md) for protocol details.

### Soft quote (indicative, no commitment)

```python
result = await client.get_swap_soft_quote(
    base_token="ALOT",
    quote_token="USDC",
    quantity=100.0,
    side=0,   # 0 = BUY base token, 1 = SELL base token
)
if result.success:
    print("Indicative price:", result.data)
```

### Firm quote (binding, starts 30s expiry)

```python
result = await client.get_swap_firm_quote(
    base_token="ALOT",
    quote_token="USDC",
    quantity=100.0,
    side=0,
)
if result.success:
    quote = result.data
    print("Firm quote ID:", quote["nonceAndMeta"])
```

### Execute swap

```python
result = await client.execute_rfq_swap(quote=quote, signer=signer)
if result.success:
    print("Swap tx:", result.data)
```

---

## Portfolio & transfers

### Check portfolio balances

```python
result = await client.get_all_portfolio_balances()
if result.success:
    for token, balance in result.data.items():
        print(token, balance["total"], balance["available"])
```

Single token:

```python
result = await client.get_portfolio_balance(token="USDC")
```

### Check chain wallet balances

```python
result = await client.get_all_chain_wallet_balances()
```

### Deposit

```python
result = await client.deposit(
    token="USDC",
    amount=100.0,
    source_chain="avalanche",
)
if result.success:
    print("Deposit tx:", result.data)
```

### Withdraw

```python
result = await client.withdraw(
    token="USDC",
    amount=50.0,
    target_chain="avalanche",
)
```

### Add / remove gas

```python
await client.add_gas(amount=0.1)   # deposit native token (AVAX) as gas
await client.remove_gas(amount=0.05)
```

### Estimate bridge fee

```python
result = await client.get_deposit_bridge_fee(
    token="USDC",
    source_chain="ethereum",
    use_layerzero=True,
)
```

---

## Real-time events — WebSocket

The WebSocket manager must be enabled either via config or the `ws_manager_enabled` constructor kwarg.

```python
config = DexalotConfig(ws_manager_enabled=True)

async with DexalotClient(config=config, signer=signer) as client:
    # Subscribe to order updates for a pair
    await client.subscribe_to_events(
        pair="ALOT/USDC",
        callback=my_callback,
    )

    # Keep running
    await asyncio.sleep(60)

    await client.unsubscribe_from_events(pair="ALOT/USDC")
```

Callback signature:

```python
async def my_callback(event: dict) -> None:
    print(event["type"], event)
```

Callbacks run on the asyncio event loop and can `await` normally.

See [WebSocket Protocol](websocket.md) for the full event schema.

---

## Configuration deep-dive

All options can be set via constructor kwargs, environment variables, or a `.env` file. Constructor kwargs take precedence.

| Category | Key options |
|---|---|
| Environment | `parent_env` (`PARENTENV`): `"fuji-multi"` / `"production-multi"` |
| Signer | Pass `signer=Account.from_key(...)` or set `PRIVATE_KEY` |
| Cache | `enable_cache`, `cache_ttl_static/semi_static/balance/orderbook` |
| Retry | `retry_enabled`, `retry_max_attempts`, `retry_initial_delay`, `retry_max_delay` |
| Rate limit | `rate_limit_enabled`, `rate_limit_requests_per_second`, `rate_limit_rpc_per_second` |
| WebSocket | `ws_manager_enabled`, `ws_ping_interval`, `ws_reconnect_max_attempts` |
| Logging | `log_level` (`DEBUG`/`INFO`/…), `log_format` (`console`/`json`) |
| RPC | `DEXALOT_RPC_<CHAIN_ID>=url1,url2` overrides (e.g. `DEXALOT_RPC_43114=...`) |

See [API Reference](python-sdk-reference.md#dexalotconfig) for the full `DexalotConfig` field table.

### Disable caching for debugging

```python
client = DexalotClient(enable_cache=False)
```

### Use mainnet

```python
client = DexalotClient(parent_env="production-multi", signer=signer)
```

### Tune retry behavior

```python
from dexalot_sdk.core.config import DexalotConfig

config = DexalotConfig(
    retry_max_attempts=5,
    retry_initial_delay=0.5,
    retry_max_delay=30.0,
)
```

---

## Error handling best practices

1. **Always check `.success`** before accessing `.data`.
2. **Use `result.error`** to log failures; it is already sanitized for production.
3. **Enable `DEBUG` logging** locally to get full context including stack traces and raw error messages.
4. **Use `get_revert_reason()`** to translate on-chain revert codes to human-readable descriptions.

```python
DexalotClient.configure_logging(log_level="DEBUG")
```

See [Error Handling](python-sdk-error-handling.md) for a full debugging checklist and common error table.

---

## Recommended patterns

### Context manager (preferred)

```python
async with DexalotClient(signer=signer) as client:
    ...
# HTTP sessions and WebSocket connections closed automatically
```

### Long-running services — periodic reinitialize

For services that run for hours, call `reinitialize()` periodically to refresh auth tokens, RPC providers, and cache state:

```python
async def run_forever(client: DexalotClient):
    while True:
        await client.reinitialize()
        await asyncio.sleep(3600)   # reinitialize every hour
```

### Unit conversion

```python
# Human-readable to atomic
atomic = DexalotClient.unit_conversion(1.5, decimals=18)   # → 1500000000000000000

# Atomic to human-readable
human = DexalotClient.unit_conversion(1_000_000, decimals=6, to_base=False)  # → 1.0
```

### Structured logging (production)

```python
DexalotClient.configure_logging(log_level="INFO", log_format="json")
```

JSON format emits one log line per event with `level`, `message`, `timestamp`, and structured fields — suitable for log aggregators (Datadog, Loki, etc.).
