# Architecture Overview

Internal architecture of the Dexalot Python SDK for contributors and advanced users.

---

## Client composition

`DexalotClient` is assembled via multiple inheritance. The full MRO is:

```
DexalotClient
├── CLOBClient        (core/clob.py)      — order book, trading
├── SwapClient        (core/swap.py)      — RFQ, simple swap
├── TransferClient    (core/transfer.py)  — balances, deposit, withdraw
└── DexalotBaseClient (core/base.py)      — auth, HTTP, Web3, cache, config
```

All three mixins inherit from `DexalotBaseClient`. Python's C3 linearization ensures `DexalotBaseClient.__init__` is called exactly once via `super().__init__()` cooperative chaining.

**Why multiple inheritance?**  The mixin pattern keeps domain logic separated (trading vs. transfers vs. swaps) while sharing the common infrastructure (HTTP session, signer, nonce manager, rate limiters) defined in `DexalotBaseClient`. Users get a single `DexalotClient` object that does everything.

---

## 4-tier cache

Cache instances are **module-level singletons** defined in `core/base.py`. They are shared across all `DexalotClient` instances in the same process.

| Tier | Variable | Default TTL | Max size | Data |
|---|---|---|---|---|
| Static | `_STATIC_CACHE` | 3600 s (1 h) | 128 | Environments, deployments |
| Semi-Static | `_SEMI_STATIC_CACHE` | 900 s (15 m) | 256 | Tokens, trading pairs |
| Balance | `_BALANCE_CACHE` | 10 s | 512 | Portfolio and wallet balances |
| Orderbook | `_ORDERBOOK_CACHE` | 1 s | 256 | Order book snapshots |

TTLs are configurable via `DexalotConfig` fields (or `cache_ttl_*` constructor kwargs). The module-level singletons are reconfigured on the first `DexalotBaseClient.__init__` call with the given TTL values.

**Cache key structure:**  `(func_name, api_base_url, args[1:], frozenset(kwargs.items()))`.  `self` is excluded; keys are namespaced by `api_base_url`, so testnet and mainnet clients in the same process have independent cache namespaces.

**Stampede protection:**  `async_ttl_cached` coalesces concurrent callers for the same uncached key using an `asyncio.Future`. Only the first caller fetches; the rest await the same future. This prevents thundering herd on cache miss.

**Cache cleanup:**  `MemoryCache._cleanup()` runs every 50 writes (`_CLEANUP_INTERVAL`) to evict expired entries. Size enforcement (`_trim()`) runs on every `set()` call. These are separate concerns intentionally — cleanup is amortized, trimming is immediate.

**Bypassing the cache:**  Pass `enable_cache=False` to the constructor, or call `client.invalidate_cache()` to clear all tiers.

**Multi-env caveat:**  Running testnet and mainnet clients simultaneously in the same process is safe because cache keys are namespaced by `api_base_url`. However, test suites that populate module-level caches must call `_SEMI_STATIC_CACHE.clear()` (etc.) between tests to avoid cross-test contamination.

See [Caching Guide](sdk-caching.md) for TTL tuning and per-tier guidance.

---

## Async model

All I/O is async. The SDK is built on `asyncio` — no threading is used.

**HTTP:** `aiohttp.ClientSession` with a configurable connection pool (`connection_pool_limit`, `connection_pool_limit_per_host`). A single session is created in `initialize_client()` and reused for the lifetime of the client.

**WebSocket:** `websockets` async library. `WebSocketManager.connect()` and `subscribe()`/`unsubscribe()` are synchronous entry points that schedule coroutines on the running event loop via `loop.create_task()`. `disconnect()` is `async def`. WebSocket is opt-in (`ws_manager_enabled=False` by default).

**Callbacks:** WebSocket event callbacks run on the asyncio event loop and can `await` normally.

**Sync entry points and test mocking:**  The sync entry points (`connect()`, `subscribe()`) call `loop.create_task(coro)`. In unit tests, patch these entry points with `MagicMock` (not `AsyncMock`) to prevent unawaitd coroutines — or patch `loop.create_task` with a side effect that calls `coro.close()` and returns a `MagicMock`. See `CLAUDE.md` → "Async mocking" for the exact pattern used in this repo.

---

## Rate limiting

Each client instance has its own rate limiter (not shared across instances):

- **API limiter:** `rate_limit_requests_per_second` (default: 5 r/s)
- **RPC limiter:** `rate_limit_rpc_per_second` (default: 10 r/s)

`AsyncRateLimiter` uses a token-bucket algorithm. The lock is acquired only to reserve the slot, then released before sleeping. Multiple waiters sleep independently — the wait is not serialized.

**Multi-client caveat:** Multiple concurrent `DexalotClient` instances do not share quotas and can collectively exceed the server-side limit. There is no centralized rate limiter.

---

## Nonce manager

`AsyncNonceManager` enforces sequential nonce acquisition per `(chain_id, address)` pair.

- The global `_dict_lock` was removed in favor of `dict.setdefault()`, which is safe in asyncio's single-threaded model.
- Per-`(chain_id, address)` `asyncio.Lock` objects are still held for the full nonce fetch-and-increment cycle to prevent double-nonce errors.

This is intentionally "correctness over throughput" — high-frequency transaction batching will contend on these locks. This is the expected behavior.

---

## RPC provider failover

`ProviderManager` (utils/provider_manager.py) tracks failure counts per provider URL.

- A provider is marked unhealthy after `provider_failover_max_failures` consecutive failures (default: 3).
- Unhealthy providers enter a cooldown period of `provider_failover_cooldown` seconds (default: 60 s) before being retried.
- If all providers are unhealthy, the last known provider is used as fallback.

RPC URLs can be overridden per chain via `DEXALOT_RPC_<CHAIN_ID>` environment variables (comma-separated for multiple providers):

```bash
DEXALOT_RPC_43114=https://primary.rpc.example.com,https://backup.rpc.example.com
```

---

## Security decisions

### Private key handling

After `Account` creation, `config.private_key` is zeroed out. Prefer passing a pre-built `signer` object to the constructor so the raw key never touches the config object.

### RPC URL rejection

`_reject_insecure_rpc_urls()` in `base.py` rejects plain `http://` RPC endpoints at provider setup time unless `config.allow_insecure_rpc=True`. Fail-fast before any traffic is sent over plaintext.

### Error sanitization

`error_sanitizer.py` strips file paths, RPC URLs, and stack traces from user-facing error messages. This prevents accidental leaking of infrastructure details via error responses. At `DEBUG` log level, full context is emitted to logs before sanitization — `result.error` is always sanitized regardless of log level.

### Timestamped auth

`config.timestamped_auth` (env: `DEXALOT_TIMESTAMPED_AUTH`) controls whether auth headers include a timestamp and use `"dexalot{ts}"` as the signing message. Default is `False` — the backend currently accepts only the static `"dexalot"` message. Enable only after backend confirms timestamp window validation.

---

## Key files

All paths relative to `src/dexalot_sdk/`.

| Component | Path | Purpose |
|---|---|---|
| Entry point | `core/client.py` | `DexalotClient` — user-facing class |
| Base client | `core/base.py` | Auth, HTTP, Web3, cache, config loading |
| Config | `core/config.py` | `DexalotConfig` dataclass and validation |
| CLOB | `core/clob.py` | Order book and trading operations |
| Swap | `core/swap.py` | RFQ and simple swap operations |
| Transfer | `core/transfer.py` | Balances, deposits, withdrawals |
| Result type | `utils/result.py` | `Result[T]` — no-exception return type |
| Cache | `utils/cache.py` | `MemoryCache`, `async_ttl_cached` decorator |
| Rate limiter | `utils/rate_limit.py` | Token-bucket `AsyncRateLimiter` |
| Retry | `utils/retry.py` | `async_retry` decorator with exponential backoff |
| Nonce manager | `utils/nonce_manager.py` | Per-address sequential nonce acquisition |
| Provider mgr | `utils/provider_manager.py` | Multi-provider RPC failover |
| WebSocket | `utils/websocket_manager.py` | WebSocket lifecycle and subscription management |
| Error sanitizer | `utils/error_sanitizer.py` | Strip sensitive context from error messages |
| Observability | `utils/observability.py` | Logging configuration, structured events |
| Input validators | `utils/input_validators.py` | Validation helpers used across mixins |
