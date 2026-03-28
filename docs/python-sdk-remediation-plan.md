# Security & Performance Remediation Plan

This document tracks all findings from the security and performance review of the Python SDK.
Each item includes the finding, affected files, a concrete implementation plan, and acceptance criteria.

---

## How to Use This Document

- Items are ordered by priority (Critical → High → Medium → Performance High → Performance Medium).
- Each item has a `Status` field: **Open** | **In Progress** | **Done**.
- When starting work on an item, update its status and add a PR/commit reference.

---

## Critical

### C-1: Private key persists in `DexalotConfig` after `Account` creation

**Status:** ✅ Resolved

**Finding:**
`DexalotConfig.private_key` is a plain `str` field that lives for the entire lifetime of the config
object. `_setup_account` calls `del private_key` on a local variable, not on `self.config.private_key`.
The key is therefore always accessible at `client.config.private_key`.

**Affected files:**
- [src/dexalot_sdk/core/config.py:25](../src/dexalot_sdk/core/config.py#L25)
- [src/dexalot_sdk/core/base.py:161-176](../src/dexalot_sdk/core/base.py#L161)

**Implementation plan:**
1. In `DexalotBaseClient._setup_account`, after creating the `Account` object, immediately null out
   the key on the config:
   ```python
   def _setup_account(self, signer):
       if signer:
           return signer
       private_key = self.config.private_key
       if private_key:
           try:
               account = Account.from_key(private_key)
               self.config.private_key = None   # <-- zero out
               return account
           except Exception:
               self.config.private_key = None   # <-- zero out even on failure
               return None
       return None
   ```
2. Consider making `private_key` a `dataclasses.field(repr=False)` so it never appears in
   `repr()` or log output (it already is not in `__repr__`, but defence-in-depth).
3. Add a test: after `DexalotClient.__init__`, assert `client.config.private_key is None`.

**Acceptance criteria:**
- `client.config.private_key is None` immediately after construction when initialized via env var.
- No `private_key` value appears in any log line.

---

### C-2: Static `"dexalot"` message makes auth signature replayable indefinitely

**Status:** 🔶 Mitigated — SDK-side timestamped signing implemented behind `config.timestamped_auth` flag (default `False`, env: `DEXALOT_TIMESTAMPED_AUTH`). Probe confirmed backend (testnet) does **not** yet accept timestamped signatures (returns 401). Enable the flag only after backend confirms timestamp window validation.

**Finding:**
`_get_auth_headers` signs the static string `"dexalot"` — no timestamp, no nonce.
The resulting signature is valid forever and replayable by anyone who intercepts the header.

**Affected files:**
- [src/dexalot_sdk/core/clob.py:505-516](../src/dexalot_sdk/core/clob.py#L505)

**Implementation plan:**
1. Check what the Dexalot REST API actually requires for `x-signature`. Consult
   `docs/rest-api.md` and backend team.
2. If the API supports it, change the signed message to include a millisecond timestamp:
   ```python
   def _get_auth_headers(self):
       if not self.account:
           raise Exception("Private key not configured.")
       from eth_account.messages import encode_defunct
       ts = int(time.time() * 1000)
       msg = f"dexalot{ts}"
       message = encode_defunct(text=msg)
       signature = self.account.sign_message(message).signature.hex()
       addr = cast(str, cast(Any, self.account).address)
       return {
           "x-signature": f"{addr}:0x{signature}",
           "x-timestamp": str(ts),
       }
   ```
3. If the API does not yet support timestamps, file a backend issue and document the limitation.
4. Add a unit test that calls `_get_auth_headers` twice and asserts the signatures differ
   (if timestamps are enabled).

**Acceptance criteria:**
- Signed message includes a timestamp or nonce.
- Two calls to `_get_auth_headers` separated by >1 ms produce different signatures.
- Backend validates the timestamp window (coordinate with backend team).

---

## High

### H-1: Module-level shared caches cause cross-instance data leakage

**Status:** ✅ Resolved

**Finding:**
`_STATIC_CACHE`, `_SEMI_STATIC_CACHE`, `_BALANCE_CACHE`, and `_ORDERBOOK_CACHE` are module-level
globals shared across all `DexalotBaseClient` instances. Two clients with different environments
(e.g., mainnet vs. testnet) share the same cache store, so data from one environment can be
served as the result for another.

**Affected files:**
- [src/dexalot_sdk/core/base.py:36-39](../src/dexalot_sdk/core/base.py#L36)
- [src/dexalot_sdk/utils/cache.py](../src/dexalot_sdk/utils/cache.py)

**Implementation plan:**

Option A (preferred — minimal footprint): Include the client's `api_base_url` (or a UUID assigned
at construction time) as part of every cache key. Change `async_ttl_cached` to read this from
`args[0]` when available:
```python
# In async_ttl_cached wrapper:
instance = args[0] if args else None
env_key = getattr(instance, 'api_base_url', '') or ''
key = (func.__name__, env_key, args[1:], frozenset(kwargs.items()))
```

Option B (cleaner, more breaking): Make each cache instance-level. Store them on `self` in
`_configure_caches` and pass them to the decorators via a different mechanism (e.g., a
`cache_attr` parameter that names the instance attribute to use).

Option A is recommended as it is non-breaking and straightforward.

Steps:
1. Update `async_ttl_cached` and `ttl_cached` in `cache.py` to prefix the key with the
   instance's `api_base_url` (or fallback identifier).
2. Update existing tests that assert specific cache key shapes.
3. Add an integration test: create two clients for different environments, call the same
   method on both, and verify each gets environment-appropriate data.

**Acceptance criteria:**
- Cache miss occurs when two instances with different `api_base_url` call the same method.
- Cache hit occurs when the same instance calls the same method within TTL.

---

### H-2: Cache key holds strong reference to `self`, preventing garbage collection

**Status:** ✅ Resolved — fixed as part of H-1 (Option A). Key now uses `env_key + args[1:]`, dropping `self` from the key entirely.

**Finding:**
`key = (func.__name__, args, frozenset(kwargs.items()))` stores the full `args` tuple,
which includes `self`. This means every cached result holds a permanent reference back to the
client instance, preventing it from being garbage-collected even after it goes out of scope.

**Affected files:**
- [src/dexalot_sdk/utils/cache.py:64](../src/dexalot_sdk/utils/cache.py#L64)

**Implementation plan:**
This is naturally resolved when implementing H-1 (Option A). After fixing H-1, the key
becomes `(func.__name__, env_key, args[1:], ...)` where `args[1:]` contains method arguments
but not `self`.

If H-1 is deferred, apply this fix independently:
```python
key = (func.__name__, id(args[0]) if args else None, args[1:], frozenset(kwargs.items()))
```
Note: `id()` reuse is theoretically possible after GC, but is safe in practice here since
the cache's TTL eviction will remove stale entries. A `weakref` approach would be more
correct but complex.

**Acceptance criteria:**
- After a client object goes out of scope and GC is triggered, the client is collected
  (verifiable with `gc.collect()` and `weakref.ref`).

---

### H-3: HTTP method not validated in `_make_http_request`

**Status:** ✅ Resolved

**Finding:**
`getattr(self._session, method.lower())` accepts any string as `method`. A caller passing
`"close"` or `"connector"` would invoke arbitrary methods on the aiohttp session.

**Affected files:**
- [src/dexalot_sdk/core/base.py:423](../src/dexalot_sdk/core/base.py#L423)

**Implementation plan:**
```python
_ALLOWED_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

async def _make_http_request(self, method: str, url: str, **kwargs):
    if method.lower() not in _ALLOWED_HTTP_METHODS:
        raise ValueError(f"Unsupported HTTP method: {method!r}")
    ...
```

**Acceptance criteria:**
- Calling `_make_http_request("close", ...)` raises `ValueError`.
- All existing tests pass unchanged.

---

### H-4: `_execute_single_rpc_call` allows arbitrary attribute traversal on Web3

**Status:** ✅ Resolved — exact-match allowlist `_ALLOWED_RPC_METHODS` added alongside `_ALLOWED_HTTP_METHODS` in `base.py`; validation raises `ValueError` for any unlisted method name. Unit test added.

**Finding:**
`method_name.split(".")` + chained `getattr` allows reaching any attribute on the Web3
object. While currently only called internally with hardcoded strings, it is a footgun.

**Affected files:**
- [src/dexalot_sdk/core/base.py:478-481](../src/dexalot_sdk/core/base.py#L478)

**Implementation plan:**
1. Define an allowlist of permitted method prefixes:
   ```python
   _ALLOWED_RPC_PREFIXES = frozenset({"eth.", "net.", "web3."})
   ```
2. At the top of `_execute_single_rpc_call`, validate:
   ```python
   if not any(method_name.startswith(p) for p in _ALLOWED_RPC_PREFIXES):
       raise ValueError(f"RPC method not allowed: {method_name!r}")
   ```

**Acceptance criteria:**
- Calling with `method_name="__class__.__mro__"` raises `ValueError`.
- All legitimate RPC calls (`eth.gas_price`, `eth.send_raw_transaction`, etc.) continue to work.

---

### H-5: Raw exception text returned to users in balance helpers

**Status:** ✅ Resolved

**Finding:**
Several internal helpers return `f"Error: {e}"` or `f"Error: {str(e)}"` directly in user-facing
dict values, bypassing the `_sanitize_error` pipeline. This can leak file paths, RPC URLs, or
stack trace fragments.

**Affected files:**
- [src/dexalot_sdk/core/transfer.py:314-315](../src/dexalot_sdk/core/transfer.py#L314) — `_get_l1_native_balance`
- [src/dexalot_sdk/core/transfer.py:335](../src/dexalot_sdk/core/transfer.py#L335) — `_get_native_balance`
- [src/dexalot_sdk/core/transfer.py:387](../src/dexalot_sdk/core/transfer.py#L387) — already uses `_sanitize_error` but inconsistently

**Implementation plan:**
Replace all `f"Error: {e}"` in user-facing dicts with `self._sanitize_error(e, "<context>")`:
```python
# _get_l1_native_balance
except Exception as e:
    entry["balance"] = f"Error: {self._sanitize_error(e, 'fetching L1 native balance')}"

# _get_native_balance
except Exception as e:
    entry["balance"] = f"Error: {self._sanitize_error(e, 'fetching native balance')}"
```

**Acceptance criteria:**
- Unit test: when an RPC call raises `Exception("failed: https://rpc.example.com/secret-key")`,
  the returned balance string does not contain the URL.

**Resolution:** Replaced `f"Error: {e}"` / `f"Error: {str(e)}"` in `_get_l1_native_balance` and
`_get_native_balance` with `self._sanitize_error(e, "<context>")`. Two unit tests added in
`tests/unit/core/test_transfer.py` (`test_get_l1_native_balance_sanitizes_error`,
`test_get_native_balance_sanitizes_error`).

---

## Medium

### M-1: WebSocket private-topic signature has no freshness guarantee on the client side

**Status:** 🔶 Mitigated — backend clock-skew window (±30 000 ms) documented in code comment; `config.ws_time_offset_ms` (env: `DEXALOT_WS_TIME_OFFSET_MS`) added to compensate for known clock skew. No client-side enforcement or retry on rejection — coordinate with backend to confirm the window and add enforcement if needed.

**Finding:**
`_subscribe_topic` sends `ts = int(time.time() * 1000)` but there is no client-side check
that the timestamp is within a reasonable window before sending, and no retry if the signature
is rejected by the server due to clock skew.

**Affected files:**
- [src/dexalot_sdk/utils/websocket_manager.py:355-366](../src/dexalot_sdk/utils/websocket_manager.py#L355)

**Implementation plan:**
1. Confirm with backend team what the accepted clock skew window is.
2. Add a comment documenting the expected window.
3. If clock skew is a known operational concern, add a `time_offset` configuration field that
   can be set to compensate.

**Acceptance criteria:**
- The accepted timestamp window is documented in code comments.
- Configuration supports manual time offset adjustment.

---

### M-2: ERC20 approval is not revoked when the subsequent transaction fails

**Status:** ✅ Resolved — `_execute_erc20_deposit` and the new `_execute_erc20_withdrawal` helper both wrap the main tx in a try/except that calls `_ensure_allowance(..., 0)` on failure (best-effort, swallows secondary exceptions). Three unit tests added: `test_erc20_deposit_revokes_allowance_on_failure`, `test_erc20_deposit_no_revoke_when_no_token_info`, `test_erc20_withdraw_revokes_allowance_on_failure`.

**Finding:**
`_ensure_allowance` approves exactly `amount_wei`. If the deposit/withdrawal transaction fails
after the approval succeeds, the allowance remains live, allowing a future accidental or
malicious re-spend.

**Affected files:**
- [src/dexalot_sdk/core/transfer.py:1110-1150](../src/dexalot_sdk/core/transfer.py#L1110)

**Implementation plan:**
Wrap the approval + main transaction in a try/except. On failure of the main transaction,
send a revocation approval (set to 0):
```python
await self._ensure_allowance(w3, token_address, contract.address, amount_wei)
try:
    tx_hash = await self._execute_erc20_deposit(...)
except Exception:
    # Attempt to revoke approval; best-effort, don't raise if it fails
    try:
        await self._ensure_allowance(w3, token_address, contract.address, 0)
    except Exception:
        pass
    raise
```

**Acceptance criteria:**
- If the main deposit tx reverts, a follow-up approval(0) transaction is attempted.
- Test (mock-based): verify approval revocation is called when deposit fails.

---

### M-3: Synchronous WebSocket library mixed into async codebase

**Status:** ✅ Resolved — replaced `websocket-client`+threading with the `websockets` async library. `WebSocketManager` now runs entirely on the asyncio event loop: `connect()` schedules an `asyncio.Task` for the background `_run()` coroutine; `disconnect()` is `async def` and cancels that task. No `threading.Thread` remains in the WebSocket implementation. `close_websocket()` in `clob.py` simplified to `await asyncio.wait_for(mgr.disconnect(), ...)`. `listen_to_events()` (a separate one-shot sync method that used `websocket-client` directly) was removed as it was superseded by `subscribe_to_events()`. Unit tests in `TestWebSocketManager` fully rewritten for the async implementation.

**Finding:**
`WebSocketManager` uses the `websocket-client` library (synchronous) with `threading.Thread`.
This creates a hybrid threading/asyncio model that is hard to reason about and cannot be
cancelled via `asyncio.Task.cancel()`.

**Affected files:**
- [src/dexalot_sdk/utils/websocket_manager.py](../src/dexalot_sdk/utils/websocket_manager.py)
- [pyproject.toml](../pyproject.toml)

**Implementation plan:**
1. Evaluate replacing `websocket-client` with the `websockets` library or `aiohttp`'s built-in
   WebSocket client.
2. Rewrite `WebSocketManager` as a fully async class (using `async def connect`, `async for`
   message loop, `asyncio.Task` for the background loop).
3. This is a significant refactor — scope it as a separate task with its own branch.
4. Maintain the existing public API (`subscribe`, `unsubscribe`, `disconnect`) to avoid
   breaking changes for SDK consumers.

**Acceptance criteria:**
- No `threading.Thread` in the WebSocket implementation.
- `disconnect()` is `async def` and cleanly cancels the background task.
- Existing WebSocket integration tests pass.

---

### M-4: No TLS configuration options for RPC endpoints

**Status:** ✅ Resolved — `allow_insecure_rpc: bool = False` added to `DexalotConfig` (env: `DEXALOT_ALLOW_INSECURE_RPC`). `_reject_insecure_rpc_urls` helper raises `ValueError` for any `http://` URL when the flag is `False`; called in `_get_rpc_urls` (primary gate for all three resolution paths) and `_create_provider_fallback` (defence-in-depth). 15 unit tests added across `test_config.py` and `test_base.py`.

**Finding:**
RPC URLs are used as-is with default SSL settings. There is no way to enforce minimum TLS
version, disable insecure `http://` RPC URLs in production, or pin certificates.

**Affected files:**
- [src/dexalot_sdk/core/base.py:800-803](../src/dexalot_sdk/core/base.py#L800)
- [src/dexalot_sdk/utils/provider_manager.py:94](../src/dexalot_sdk/utils/provider_manager.py#L94)

**Implementation plan:**
1. Add a `DexalotConfig` field `allow_insecure_rpc: bool = False`.
2. In `_get_rpc_urls` and `add_providers`, if `allow_insecure_rpc is False`, reject any URL
   that starts with `http://` (not `https://`), logging a warning or raising `ValueError`.
3. Document the setting in the SDK README.

**Acceptance criteria:**
- By default, `http://` RPC URLs raise `ValueError` at provider setup time.
- Setting `allow_insecure_rpc=True` permits `http://` URLs.

---

## Performance — High

### P-1: Rate limiter serializes all concurrent requests through a blocking sleep

**Status:** ✅ Resolved — `_last_call` is now advanced speculatively under the lock and the lock is released before `asyncio.sleep`. Concurrent callers each sleep independently in their assigned time slot instead of queueing behind the lock. `test_rate_limiter_concurrent_calls` updated to remove the now-invalid per-completion spacing assertion; `test_rate_limiter_concurrent_throughput` added as the acceptance-criteria benchmark (10 calls at 5 rps ≤ 3 s).

**Finding:**
`AsyncRateLimiter.acquire` holds the asyncio lock while sleeping (`await asyncio.sleep` inside
`async with self._lock`). This means all concurrent callers queue behind the lock sequentially,
making concurrency effectively serial for any burst of requests.

**Affected files:**
- [src/dexalot_sdk/utils/rate_limit.py:35-46](../src/dexalot_sdk/utils/rate_limit.py#L35)

**Implementation plan:**
Release the lock before sleeping. Calculate the required sleep time under the lock, update
`_last_call` speculatively, then sleep outside the lock:
```python
async def acquire(self):
    async with self._lock:
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed
        else:
            sleep_time = 0.0
        # Advance last_call by the sleep we're about to do
        self._last_call = now + sleep_time

    if sleep_time > 0:
        await asyncio.sleep(sleep_time)
```
This allows the lock to be released quickly while each waiter sleeps on its own. Waiters are
effectively serialized via their calculated sleep times, not via the lock.

**Acceptance criteria:**
- Benchmark: 10 concurrent `acquire()` calls at 5 rps complete in ~(10/5) = 2 s, not ~10 s.
- Existing rate limit unit tests pass.

---

### P-2: `MemoryCache._cleanup` does full dict rebuild on every `set`

**Status:** ✅ Resolved — TTL expiry sweep (`_cleanup`) is now amortized: called only once every `_CLEANUP_INTERVAL` (50) writes via `_write_count`. The max_size cap (`_trim`) remains on every write to keep the size bound immediate. Three unit tests added: `test_memory_cache_cleanup_amortized` (verifies _cleanup call count), `test_memory_cache_max_size_enforced_immediately` (verifies size cap still applies per-write), `test_memory_cache_ttl_cleanup_removes_expired` (verifies expired entries are swept at interval).

**Finding:**
`_cleanup` is called on every `set()`, rebuilding the entire dict with a dict-comprehension
(O(n)) and trimming. With up to 512 entries in `_BALANCE_CACHE`, this is called on every
balance cache miss.

**Affected files:**
- [src/dexalot_sdk/utils/cache.py:13-26](../src/dexalot_sdk/utils/cache.py#L13)

**Implementation plan:**
Amortize cleanup: only run it every N writes or every T seconds:
```python
class MemoryCache:
    _CLEANUP_INTERVAL = 50  # run cleanup every 50 writes

    def __init__(self, ttl_seconds, max_size=256):
        ...
        self._write_count = 0

    def set(self, key, value):
        self._store[key] = (time.time(), value)
        self._write_count += 1
        if self._write_count >= self._CLEANUP_INTERVAL:
            self._cleanup()
            self._write_count = 0
```
Additionally, consider an LRU eviction policy (using `collections.OrderedDict`) instead of
FIFO to be smarter about which entries to evict when at capacity.

**Acceptance criteria:**
- `_cleanup` is called at most once per N `set()` calls (verifiable via mock/counter).
- Cache size stays bounded at `max_size`.
- TTL eviction still works correctly.

---

### P-3: Cache decorator has no stampede protection — concurrent requests duplicate work

**Status:** ✅ Resolved — per-key `asyncio.Future` coalescing added to `async_ttl_cached` in `cache.py`. `_pending` dict and `_pending_lock` are closure-scoped per decorated function. Concurrent callers for the same uncached key now wait on a shared future; the underlying function is called exactly once. Two unit tests added: `test_async_ttl_cached_stampede_protection` (call count = 1 for 5 concurrent callers) and `test_async_ttl_cached_stampede_exception_propagates` (all waiters receive the exception).

**Finding:**
Two concurrent coroutines can both see a cache miss, both execute the underlying function,
and both write to the cache (last-write wins). For expensive RPC calls this wastes resources
and can cause inconsistency.

**Affected files:**
- [src/dexalot_sdk/utils/cache.py:77-102](../src/dexalot_sdk/utils/cache.py#L77)

**Implementation plan:**
Use a per-key asyncio lock (or a "pending" future) to coalesce concurrent requests:
```python
_pending: dict[Hashable, asyncio.Future] = {}
_pending_lock = asyncio.Lock()

async def wrapper(*args, **kwargs):
    key = ...
    cached = cache.get(key)
    if cached is not None:
        return cached

    async with _pending_lock:
        if key in _pending:
            fut = _pending[key]
        else:
            fut = asyncio.get_event_loop().create_future()
            _pending[key] = fut
            do_work = True

    if not do_work:
        return await asyncio.shield(fut)

    try:
        result = await func(*args, **kwargs)
        cache.set(key, result)
        fut.set_result(result)
        return result
    except Exception as e:
        fut.set_exception(e)
        raise
    finally:
        async with _pending_lock:
            _pending.pop(key, None)
```

Note: This is a significant change to cache semantics. Start with the highest-value methods
(balance queries, deployment fetches) and roll it out incrementally.

**Acceptance criteria:**
- When N concurrent callers request the same uncached key, the underlying function is
  called exactly once.
- All concurrent waiters receive the same result.
- Unit test verifying call count with `AsyncMock`.

---

### P-4: `_fetch_erc20_balances_list` fires unbounded parallel RPC calls

**Status:** ✅ Resolved — `asyncio.Semaphore(self.config.erc20_balance_concurrency)` added to `_fetch_erc20_balances_list` in `transfer.py`. Default cap is 10 concurrent `balanceOf` calls, configurable via `DexalotConfig.erc20_balance_concurrency` (env: `DEXALOT_ERC20_BALANCE_CONCURRENCY`). Two unit tests added: `test_fetch_erc20_balances_list_concurrency_limit` (verifies max in-flight ≤ limit) and `test_fetch_erc20_balances_list_concurrency_config` (verifies correct results at concurrency=1).

**Finding:**
All tokens on a chain are queried in a single `asyncio.gather(*tasks)` with no concurrency
limit. With 50+ tokens this creates 50+ simultaneous RPC connections, overwhelming the RPC
rate limiter and the provider.

**Affected files:**
- [src/dexalot_sdk/core/transfer.py:391-447](../src/dexalot_sdk/core/transfer.py#L391)

**Implementation plan:**
Use `asyncio.Semaphore` to cap concurrency:
```python
async def _fetch_erc20_balances_list(self, chain_id, chain_name, w3_provider, address):
    sem = asyncio.Semaphore(10)  # max 10 concurrent balance calls

    async def fetch_one(contract, symbol, token_address, decimals):
        async with sem:
            balance_wei = await contract.functions.balanceOf(address).call()
            return symbol, token_address, decimals, balance_wei

    tasks = [fetch_one(...) for ...]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ...
```

The semaphore limit (10) should be configurable via `DexalotConfig`.

**Acceptance criteria:**
- No more than `N` concurrent `balanceOf` calls at any time (configurable, default 10).
- Total time for 50-token fetch with a slow provider is measured and documented.

---

### P-5: `get_all_portfolio_balances` paginates sequentially

**Status:** ✅ Resolved

**Finding:**
The pagination loop in `_get_all_portfolio_balances_cached` makes one sequential RPC call per
page (up to 10). Each page waits for the previous one to complete.

**Affected files:**
- [src/dexalot_sdk/core/transfer.py:537-562](../src/dexalot_sdk/core/transfer.py#L537)

**Implementation plan:**
Since the contract's `getBalances(address, page)` call doesn't require knowing the total count
upfront, fetch several pages in parallel and stop when an empty page is returned:
```python
async def fetch_page(page_num):
    data = await contract.functions.getBalances(query_address, page_num).call()
    return page_num, data

# Fetch first 5 pages in parallel; if all non-empty, fetch next 5, etc.
BATCH_SIZE = 5
page = 0
while True:
    pages = await asyncio.gather(*[fetch_page(page + i) for i in range(BATCH_SIZE)],
                                 return_exceptions=True)
    # Process results, stop when an empty page is encountered
    got_empty = False
    for _, data in sorted(pages, key=lambda x: x[0]):
        if not data[0]:
            got_empty = True
            break
        # ... process symbols ...
    if got_empty:
        break
    page += BATCH_SIZE
    if page >= 50:  # hard cap
        break
```

**Acceptance criteria:**
- For portfolios with 3 pages of balances, total RPC round-trips reduced from 3 sequential
  to at most 2 parallel batches.
- Result is identical to the sequential version.

---

## Performance — Medium

### P-6: `ProviderManager.get_provider` holds a per-chain lock for read-only selection

**Status:** ✅ Resolved

**Finding:**
`get_provider` acquires a full write-lock even though it is a read-mostly operation (it only
writes when recovering an unhealthy provider). Under high concurrency all threads reading
the same chain's provider queue behind a single lock.

**Affected files:**
- [src/dexalot_sdk/utils/provider_manager.py:127](../src/dexalot_sdk/utils/provider_manager.py#L127)

**Implementation plan:**
Short-term: Split selection logic into a fast-path that avoids locking when the current
provider is healthy:
```python
async def get_provider(self, chain_name):
    providers = self._providers.get(chain_name)
    if not providers:
        return None
    idx = self._current_provider_index.get(chain_name, 0)
    health = self._health[chain_name][idx]
    # Fast path: no lock needed if provider is healthy
    if health.is_healthy and health.can_retry(...):
        return providers[idx]
    # Slow path: acquire lock for failover logic
    async with self._locks[chain_name]:
        ...
```

Long-term: Consider `asyncio.RWLock` (available in `aiofiles` or custom implementation).

**Acceptance criteria:**
- Under 100 concurrent `get_provider` calls with a healthy provider, no measurable lock
  contention (benchmark with `time.perf_counter`).

**Resolution:** Added a lock-free fast path in `get_provider`: when the current provider
is healthy, the provider index and health are read without acquiring the per-chain lock.
The slow path (lock-protected failover) is only entered when the current provider is
unhealthy. Safe in asyncio because there is no `await` between the fast-path reads, so no
other coroutine can mutate state in between. Tests added:
`test_get_provider_fast_path_skips_lock`, `test_get_provider_fast_path_concurrent`,
`test_get_provider_creates_lock_on_slow_path`.

---

### P-7: `AsyncNonceManager` uses a global `_dict_lock` for all chains at startup

**Status:** ✅ Resolved

**Finding:**
`_dict_lock` serializes lock creation for all `(chain_id, address)` combinations. During
the first wave of concurrent transactions, all callers contend on this single lock.

**Affected files:**
- [src/dexalot_sdk/utils/nonce_manager.py:21](../src/dexalot_sdk/utils/nonce_manager.py#L21)

**Implementation plan:**
Use `defaultdict` with a factory that creates locks, or use `setdefault` with a
pre-constructed lock to avoid the outer lock in the common case:
```python
# Thread-safe lock creation without a global lock:
async def _get_lock(self, key: str) -> asyncio.Lock:
    # Create a candidate lock without holding any lock
    candidate = asyncio.Lock()
    # setdefault is atomic in CPython due to GIL; for asyncio this is fine
    # since we're single-threaded within the event loop
    return self._locks.setdefault(key, candidate)
```

Since asyncio is single-threaded within the event loop, `dict.setdefault` is effectively
atomic and `_dict_lock` is unnecessary.

**Acceptance criteria:**
- `_dict_lock` is removed.
- Existing nonce manager tests pass.
- Under concurrent usage, nonces are still monotonically increasing per key.

**Resolution:** Removed `_dict_lock` and converted `_get_lock` to a synchronous method
using `dict.setdefault`. Both callers (`get_nonce`, `reset_nonce`) updated to call without
`await`. Tests added: `test_no_dict_lock`, `test_get_lock_returns_same_instance`,
`test_concurrent_lock_creation_same_key`. All 626 unit tests pass. Commit: 3c23409.

---

### P-8: Float division used for on-chain price/quantity values

**Status:** ✅ Resolved

**Finding:**
Orderbook prices and quantities are divided using Python float arithmetic:
`p / (10 ** pair_data["quote_decimals"])`. For values with 18 decimals, this loses
significant precision.

**Affected files:**
- [src/dexalot_sdk/core/clob.py:227-230](../src/dexalot_sdk/core/clob.py#L227)
- [src/dexalot_sdk/core/clob.py:233-236](../src/dexalot_sdk/core/clob.py#L233)
- [src/dexalot_sdk/core/transfer.py:383-384](../src/dexalot_sdk/core/transfer.py#L383) — `_get_erc20_balance`
- [src/dexalot_sdk/core/transfer.py:437](../src/dexalot_sdk/core/transfer.py#L437) — `_fetch_erc20_balances_list`

**Implementation plan:**
Use `Utils.unit_conversion` (which already uses `Decimal`) consistently:
```python
# Instead of:
"price": p / (10 ** pair_data["quote_decimals"])
# Use:
"price": Utils.unit_conversion(p, pair_data["quote_decimals"], to_base=False)
```

This also centralizes the conversion logic.

**Acceptance criteria:**
- All price and quantity values returned by `get_orderbook` are `float` produced from `Decimal`
  arithmetic (no precision loss for amounts representable in 18 decimal places).
- `Utils.unit_conversion` is used at all division sites for token amounts.

**Resolution:** Replaced all four raw float division sites with `Utils.unit_conversion(..., to_base=False)`:
`clob.py` bids/asks price+quantity, `transfer.py` `_get_erc20_balance`, and `transfer.py`
`_fetch_erc20_balances_list`. Tests added verifying correct delegation to `Utils.unit_conversion`.
All 628 unit tests pass. Commit: TBD.

---

### P-9: `track_method` decorator wraps async methods with a synchronous context manager

**Status:** ✅ Resolved

**Finding:**
`track_method` returns a **synchronous** `wrapper` that calls `func(self, *args, **kwargs)`.
For `async def` decorated methods, this returns a coroutine object — the `with track_operation`
block exits immediately (timing near-zero). The actual async execution time is not measured.

**Affected files:**
- [src/dexalot_sdk/utils/observability.py:304-323](../src/dexalot_sdk/utils/observability.py#L304)

**Implementation plan:**
Detect async functions and provide an async wrapper:
```python
import inspect

def track_method(operation: str, **extra_context):
    def decorator(func):
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(self, *args, **kwargs):
                logger = getattr(self, "logger", logging.getLogger(func.__module__))
                context = {"function": func.__name__, **extra_context}
                with track_operation(logger, operation, **context):
                    return await func(self, *args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(self, *args, **kwargs):
                logger = getattr(self, "logger", logging.getLogger(func.__module__))
                context = {"function": func.__name__, **extra_context}
                with track_operation(logger, operation, **context):
                    return func(self, *args, **kwargs)
            return sync_wrapper
    return decorator
```

This is the highest-value observability fix — without it, all timing data in logs is wrong.

**Acceptance criteria:**
- Log output for `add_order`, `deposit`, etc. shows correct elapsed time (matches
  `time.perf_counter` measurement from outside the call).
- Existing tests for `track_method` pass.
- New test: `track_method` on an `async def` that `asyncio.sleep(0.1)`s logs duration ≥ 100 ms.

**Resolution:** Added `inspect.iscoroutinefunction` check in `track_method`; async methods now
get an `async_wrapper` that `await`s the function inside the `with track_operation` block.
Three new tests added in `tests/unit/test_observability.py` covering sync behavior, async timing
correctness, and external elapsed-time measurement. All 631 unit tests pass.

---

## Dependency Hygiene

### D-1: Unpinned runtime dependencies

**Status:** ✅ Resolved

**Finding:**
`aiohttp`, `python-dotenv`, `eth-account`, and `websocket-client` have no upper-bound version
pins. A major-version bump in any of them can silently break the SDK.

**Affected files:**
- [pyproject.toml:12-17](../pyproject.toml#L12)

**Implementation plan:**
1. Run `uv pip compile` or `pip-compile` to generate a `requirements.lock` or
   `uv.lock` file capturing current known-good versions.
2. Add upper-bound pins to `pyproject.toml` for the most volatile dependencies:
   ```toml
   dependencies = [
       "web3>=6.0.0,<8",
       "aiohttp>=3.9,<4",
       "python-dotenv>=1.0,<2",
       "eth-account>=0.11,<1",
       "websocket-client>=1.7,<2",
   ]
   ```
3. Set up a Dependabot or Renovate bot to raise PRs when new versions are released.

**Acceptance criteria:**
- `pyproject.toml` has upper-bound pins for all runtime dependencies.
- A lock file exists for reproducible installs in CI.

**Resolution:** Added upper-bound pins to all four unbound runtime dependencies in `pyproject.toml`
(`web3<8`, `aiohttp<4`, `python-dotenv<2`, `eth-account<1`). `websockets` already had `<15`.
`uv.lock` was already present. All 631 unit tests pass; lint and mypy clean.

---

## Suggested Implementation Order

This ordering balances risk reduction, effort, and interdependency:

| Phase | Items | Rationale |
|-------|-------|-----------|
| **1 — Quick wins** | C-1, H-3, H-5, P-9, P-7 | Small, isolated, high-impact changes |
| **2 — Security core** | C-2, H-1, H-2, H-4 | Core security hardening; H-1/H-2 are related |
| **3 — Performance core** | P-1, P-2, P-8 | Improves throughput and correctness for all users |
| **4 — Resilience** | P-3, P-4, M-2, M-4 | Stampede protection, parallel fetching, approval safety |
| **5 — Larger refactors** | M-3, P-5, P-6, D-1 | WebSocket rewrite, pagination, lock optimization |
| **6 — Documentation / ops** | M-1, D-1 | Coordination with backend and CI tooling |
