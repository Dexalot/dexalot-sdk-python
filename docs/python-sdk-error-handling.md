# Error Handling

The SDK uses a `Result[T]` return type for all public methods. No exceptions are raised for expected failure conditions (network errors, validation failures, contract reverts). This guide covers the full error model, debugging workflow, and common errors.

---

## Result\[T\] in depth

Every SDK method returns a `Result` instance with three fields:

| Field | Type | Description |
|---|---|---|
| `success` | `bool` | `True` if the operation succeeded |
| `data` | `T \| None` | The return value on success; `None` on failure |
| `error` | `str \| None` | Human-readable error message on failure; `None` on success |

```python
result = await client.add_order(pair="ALOT/USDC", price=0.15, quantity=100.0, side=0, order_type=1)

if result.success:
    tx_hash = result.data      # str: transaction hash
else:
    print(result.error)        # str: sanitized error description
```

**Bool coercion** — `Result` evaluates to `True` when successful, so you can use it directly in conditionals:

```python
if result := await client.get_trading_pairs():
    pairs = result.data
```

**Factory methods** (used internally; useful when building wrappers):

```python
ok     = Result.ok(data={"key": "value"})
failed = Result.fail("Token not found")
```

**Generic type parameter** — type checkers see `result.data` as `T`:

```python
result: Result[list[dict]] = await client.get_open_orders(pair="ALOT/USDC")
orders: list[dict] | None = result.data
```

---

## Failure categories

### 1. Input validation

Caught before any network call. Common causes:

- Empty or malformed pair symbol (`"ALOT"` instead of `"ALOT/USDC"`)
- Quantity or price ≤ 0
- Invalid side value (not 0 or 1)
- Missing signer for write operations

```python
result = await client.add_order(pair="ALOT/USDC", price=-1.0, quantity=100.0, side=0, order_type=1)
# result.success == False
# result.error == "price must be positive"
```

### 2. Network / HTTP errors

Returned when the REST API is unreachable, returns a non-200 status, or the request times out. Retry logic (if enabled) runs before the final failure is returned.

```python
result = await client.get_trading_pairs()
# result.success == False
# result.error == "HTTP 503: service unavailable"  (sanitized)
```

### 3. Blockchain / contract reverts

Returned when a submitted transaction reverts on-chain. The raw revert error code is decoded via `get_revert_reason()` where possible.

```python
result = await client.deposit(token="USDC", amount=99999.0, source_chain="avalanche")
# result.success == False
# result.error == "insufficient balance"   (decoded revert reason)
```

---

## Revert reasons

`get_revert_reason()` maps 4-byte error selectors (and named error codes) from `errors.json` to human-readable descriptions:

```python
raw = "execution reverted: 0x12345678"
description = client.get_revert_reason(raw)
print(description)   # → "P-AFNE-01: Insufficient allowance"
```

If no matching code is found, the original message is returned unchanged. Use this to log user-facing messages from failed transactions:

```python
result = await client.add_order(...)
if not result.success:
    reason = client.get_revert_reason(result.error)
    print(f"Order failed: {reason}")
```

---

## Error sanitization

In production, the SDK strips sensitive context from error messages before returning them:

- File paths (e.g. `/home/user/app/client.py:42`)
- Stack traces
- Raw RPC URLs
- Internal library error details

This prevents accidental exposure of infrastructure details in logs or API responses.

**What gets returned instead:** a concise, user-facing description, e.g. `"network error"` instead of the full stack trace with connection details.

**Locally**, enable `DEBUG` log level to see the full unsanitized context in logs:

```python
DexalotClient.configure_logging(log_level="DEBUG")
```

At `DEBUG` level, the logger emits full exception details with stack traces before sanitization. `result.error` is still sanitized — the raw context is only in the log output.

---

## Debugging checklist

When an operation fails unexpectedly:

1. **Enable DEBUG logging** to see full error context:
   ```python
   DexalotClient.configure_logging(log_level="DEBUG")
   ```

2. **Disable cache** to rule out stale data:
   ```python
   client = DexalotClient(enable_cache=False)
   ```

3. **Use testnet** (`PARENTENV=fuji-multi`) to avoid real-money risk while debugging.

4. **Inspect `.error`** — the sanitized message usually gives enough context even in production.

5. **Call `get_revert_reason()`** on transaction failures to decode on-chain error codes.

6. **Check the remediation plan** at `docs/python-sdk-remediation-plan.md` for known issues.

---

## Common errors

| Error message (approx.) | Likely cause | Fix |
|---|---|---|
| `"signer required for this operation"` | Write operation called without a signer | Pass `signer=Account.from_key(...)` to constructor |
| `"pair not found"` | Invalid trading pair symbol | Check `get_trading_pairs()` for valid symbols |
| `"insufficient balance"` | Portfolio balance too low for the trade | Check `get_portfolio_balance()` first |
| `"price must be positive"` | `price <= 0` passed to `add_order` | Validate price before calling |
| `"HTTP 401"` | Auth header invalid or expired | Call `reinitialize()` to refresh auth |
| `"HTTP 429"` | Rate limit exceeded server-side | Lower `rate_limit_requests_per_second`, add backoff |
| `"network error"` | Connection failure or timeout | Check network; retry logic runs automatically if enabled |
| `"RPC provider unavailable"` | All configured RPC endpoints failed | Add backup RPC URLs via `DEXALOT_RPC_<CHAIN_ID>` |
| `"nonce too low"` | Transaction nonce collision | Avoid concurrent transactions without lock; use nonce manager |
| `"gas estimation failed"` | On-chain pre-flight check rejected | Check token allowances and contract state |
