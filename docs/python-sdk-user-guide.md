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
PRIVATE_KEY=0x...      # hex-encoded private key; cleared from config after Account creation
```

Prefer passing a pre-built `eth_account.Account` object to the constructor instead of setting `PRIVATE_KEY` — this keeps the raw key out of the config entirely.

---

## Core concepts

### Result[T] — result-first operational API

Async operational methods return a `Result` object. Expected failures such as network errors, validation errors, and contract reverts are returned as `Result.fail(...)`. Some configuration or programmer errors can still raise immediately, so always check `.success` before accessing `.data` on Result-returning calls:

```python
result = await client.get_clob_pairs()
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
    await client.initialize_client()
    result = await client.get_clob_pairs()
```

The context manager opens the HTTP session on entry and calls `close()` on exit. It does **not** call `initialize_client()` automatically, so run `await client.initialize_client()` before trading, swap, transfer, or contract-dependent operations.

Manual lifecycle:

```python
client = DexalotClient()
await client.connect()
await client.initialize_client()
try:
    ...
finally:
    await client.close()
```

---

## Getting started

### List trading pairs and fetch an order book

```python
import asyncio
from dexalot_sdk import DexalotClient

async def main():
    async with DexalotClient() as client:
        await client.initialize_client()

        # List available trading pairs
        pairs_result = await client.get_clob_pairs()
        if not pairs_result.success:
            print(f"Error: {pairs_result.error}")
            return

        for pair in pairs_result.data[:5]:
            print(pair["pair"])   # e.g. "ALOT/USDC"

        # Fetch order book for a pair
        ob_result = await client.get_orderbook("ALOT/USDC")
        if ob_result.success:
            ob = ob_result.data
            print("Best bid:", ob["bids"][0])
            print("Best ask:", ob["asks"][0])

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
    await client.initialize_client()
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
    await client.initialize_client()
    result = await client.add_order(
        pair="ALOT/USDC",
        side="BUY",
        amount=100.0,
        price=0.15,
        order_type="LIMIT",
    )
    if result.success:
        print("Tx:", result.data["tx_hash"])
        print("Client order ID:", result.data["client_order_id"])  # save for cancel/replace
    else:
        print("Failed:", result.error)
```

You can optionally supply your own `client_order_id` (a 32-byte hex string or a UTF-8 string ≤ 32 bytes). When omitted, the SDK generates one randomly:

```python
result = await client.add_order(
    pair="ALOT/USDC",
    side="BUY",
    amount=100.0,
    price=0.15,
    client_order_id="0x" + "ab" * 32,  # deterministic, idempotent
)
```

### Cancel an order

```python
result = await client.cancel_order(order_id="0xabc...")
if result.success:
    print("Cancelled. client_order_id:", result.data["cancelled_client_order_id"])
```

### Cancel all orders for a pair

```python
result = await client.cancel_all_orders()
```

### Batch operations

Place multiple orders in one transaction:

```python
orders = [
    {"pair": "ALOT/USDC", "side": "BUY", "amount": 50.0, "price": 0.14},
    {"pair": "ALOT/USDC", "side": "BUY", "amount": 75.0, "price": 0.13},
]
result = await client.add_limit_order_list(orders)
```

Cancel multiple orders by internal order ID:

```python
result = await client.cancel_list_orders(order_ids=["0xabc...", "0xdef..."])
if result.success:
    print("Cancelled:", result.data["cancelled_internal_order_ids"])
```

Atomic cancel-and-replace (cancel list, then place new list):

```python
result = await client.cancel_add_list(
    replacements=[
        {
            "order_id": "0xold...",   # internal_order_id or client_order_id
            "pair": "ALOT/USDC",
            "side": "BUY",
            "amount": 100.0,
            "price": 0.16,
            # "client_order_id": "0x..."  # optional — generated if omitted
        }
    ],
)
if result.success:
    print("New IDs:", result.data["client_order_ids"])
    print("Cancelled client IDs:", result.data["cancelled_client_order_ids"])
```

### Query open orders

```python
result = await client.get_open_orders(pair="ALOT/USDC")
if result.success:
    for order in result.data:
        print(order["internal_order_id"], order["client_order_id"], order["price"], order["quantity"])
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
    from_token="ALOT",
    to_token="USDC",
    amount=100.0,
)
if result.success:
    print("Indicative price:", result.data)
```

### Firm quote (binding, starts 30s expiry)

```python
result = await client.get_swap_firm_quote(
    from_token="ALOT",
    to_token="USDC",
    amount=100.0,
)
if result.success:
    quote = result.data
    print("Firm quote ID:", quote["quote_id"])
```

### Execute swap

```python
result = await client.execute_rfq_swap(quote)
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
    source_chain="Avalanche",
)
if result.success:
    print("Deposit tx:", result.data)
```

### Withdraw

```python
result = await client.withdraw(
    token="USDC",
    amount=50.0,
    target_chain="Avalanche",
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

The WebSocket manager must be enabled either via config or the `ws_manager_enabled` constructor kwarg. `subscribe_to_events()` takes a `topic` string and may raise `RuntimeError` if the manager is disabled.

```python
config = DexalotConfig(ws_manager_enabled=True)

async with DexalotClient(config=config, signer=signer) as client:
    await client.initialize_client()

    # Subscribe to order book updates for a pair
    await client.subscribe_to_events(
        topic="OrderBook/ALOT/USDC",
        callback=my_callback,
    )

    # Keep running
    await asyncio.sleep(60)

    client.unsubscribe_from_events("OrderBook/ALOT/USDC")
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

## Secrets vault

The secrets vault stores sensitive values (private keys, tokens) in a Fernet-encrypted SQLite database at `~/.dexalot/secrets_vault.db`. The file is created with owner-only permissions (0o600). Values are encrypted at rest; only key names are stored in plaintext. The vault is shared between the SDK and the MCP server.

### One-time setup

```bash
# 1. Generate an encryption key and save it in a password manager
secrets-vault keygen

# 2. Store your private key
secrets-vault add PRIVATE_KEY 0xabc123...

# 3. Verify
secrets-vault list
secrets-vault get PRIVATE_KEY
```

### Providing the vault key at runtime

| Method | How |
|---|---|
| Environment variable | `DEXALOT_SECRETS_VAULT_KEY=<key>` — for containers and CI |
| Interactive prompt | Leave the env var unset; the server prompts at startup |
| Neither | Server / SDK starts in read-only mode (no signing) |

### Custom vault path

```bash
DEXALOT_SECRETS_VAULT_PATH=/secure/path/vault.db
```

### Using the vault in code

The vault functions are exported from the top-level package:

```python
from dexalot_sdk import (
    generate_secrets_vault_key,
    secrets_vault_set,
    secrets_vault_get,
    secrets_vault_list,
    secrets_vault_remove,
)

key = generate_secrets_vault_key()   # generate once, save safely
secrets_vault_set("~/.dexalot/secrets_vault.db", "PRIVATE_KEY", "0x...", key)

result = secrets_vault_get("~/.dexalot/secrets_vault.db", "PRIVATE_KEY", key)
if result.success:
    private_key = result.data
```

### Safe practices

- Never commit the vault key or your `.env` file to version control.
- Store the vault key in a password manager or secrets manager (1Password, AWS Secrets Manager, HashiCorp Vault, etc.).
- The vault database file itself is safe to back up — it is encrypted and useless without the key.
- Prefer `secrets-vault add PRIVATE_KEY ...` over `PRIVATE_KEY=...` in `.env` for anything beyond a local throwaway key.

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
