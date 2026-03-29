# Dexalot Python SDK — Claude Code Context

Python SDK for the Dexalot DEX. Security and performance findings are tracked in `docs/python-sdk-remediation-plan.md` — do not replicate that content here.

---

## Architecture Decisions

### Modular client composition

`DexalotClient` composes `CLOBClient`, `SwapClient`, and `TransferClient`, all extending `DexalotBaseClient` via multiple inheritance. Core files live in `src/dexalot_sdk/core/`.

### 4-tier caching — module-level singletons

| Tier | TTL | Data |
|---|---|---|
| Static | 1h | Environments, deployments |
| Semi-Static | 15m | Tokens, trading pairs |
| Balance | 10s | Account balances |
| Orderbook | 1s | Order book snapshots |

Caches are **module-level singletons shared across all client instances**, not per-instance. Per-instance `_cache_enabled` flag can bypass the cache entirely. Cache keys are namespaced by `api_base_url`, so testnet and mainnet data do not collide, but tests must still clear module-level caches between runs to avoid cross-test contamination.

### Result[T] pattern — no exceptions

Most async operational SDK methods return `Result(success, data, error)`. Construction, validation, and a few helper / WebSocket methods can still raise on programmer or configuration errors. Callers should check `.success` before accessing `.data` on Result-returning methods. Factory methods: `Result.ok(data)` and `Result.fail(error_msg)`.

### Config loading and validation

Precedence: constructor kwargs → env vars → `.env` file → defaults. `PARENTENV` selects environment: `fuji-multi` (testnet, default) or `production-multi` (mainnet). **`config.validate()` is called automatically** in `DexalotBaseClient.__init__` — invalid configs raise immediately on construction.

### WebSocket uses asyncio, not threading

`WebSocketManager` uses the `websockets` async library; all I/O runs on the asyncio event loop. No threading is used. `connect()` and `subscribe()`/`unsubscribe()` are synchronous entry points that schedule work on the running loop via `loop.create_task()`. `disconnect()` is `async def` and can be awaited. WebSocket is opt-in (`ws_manager_enabled=False` by default). Callbacks run on the event loop and can `await` normally.

### Rate limiters are per-instance

Each client instance has its own rate limiter (default: 5 API calls/sec, 10 RPC calls/sec). Multiple concurrent instances do **not** share quotas and can collectively exceed them. No centralized rate limiter exists.

### Nonce manager: correctness over throughput

Per-(chain_id, address) asyncio locks enforce sequential nonce acquisition. High-frequency transaction batching will contend on these locks — this is intentional to prevent double-nonce errors.

### Multi-provider RPC failover

`ProviderManager` tracks failure counts per provider and auto-recovers after a configurable cooldown (default: 60s). Falls back to secondary providers on failure.

---

## Dev Workflow

- **Python interpreter**: always use `.venv/bin/python` (never the system `python3`)
- **Package manager**: `uv` — not pip, not poetry
- **Python version**: >=3.12, <3.14 (uses match statements, PEP 695 generics)
- **Setup**: `make setup` (runs `uv venv && uv sync --group dev`)
- **Test**: `make test` (unit, fast) / `make int` (integration, requires live env)
- **Lint**: `make lint` / `make lint-fix` (ruff, line-length=100)
- **Types**: `make mypy` (strict mode)
- **Coverage**: `make cov`

Unit tests in `tests/unit/` have no external dependencies. Integration tests in `tests/integration/` require a live API environment.

### Async mocking — avoid "coroutine never awaited"

- **Sync entrypoint** (calls `loop.create_task` / `asyncio.run`): patch the scheduler or the async method with `MagicMock`. Never let a real coroutine be created without being awaited.
- **Async test** (`@pytest.mark.asyncio`): use `AsyncMock` for async dependencies; await the coroutine under test.
- Pattern used in this repo: `patch.object(manager, "connect")` to block sync entrypoints that internally schedule `_run()`. Alternatively, patch `loop.create_task` with a side effect that calls `coro.close()` before returning a `MagicMock`.

- `VERSION` file at repo root holds the current version (currently 0.4.0)
- `.env` files: never commit; use `env.example` as template
- **`env.example` must be updated** whenever a new `DexalotConfig` field or env var is added — it is the canonical reference for operators
- The error sanitizer strips file paths, URLs, and stack traces from user-facing errors. Use `log_level="DEBUG"` locally to get full context for debugging.

---

## Non-Obvious Decisions

- **Private key handling**: After `Account` creation, `config.private_key` is cleared from config. Prefer passing a pre-built signer object so the raw key never touches the config at all.
- **Cache key generation**: Uses `(func_name, api_base_url, args[1:], frozenset(kwargs.items()))` — `self` is excluded; keys are namespaced by `api_base_url`. Kwarg ordering affects cache hits; deep objects may produce false misses.
- **Config validation timing**: `config.validate()` is called automatically inside `DexalotBaseClient.__init__`. Invalid configs raise at construction time.
- **Error sanitization is lossy**: Regex stripping makes production debugging harder. Use DEBUG logging in development.
- **Python 3.12+ is required**: CI must enforce this. Match statements and PEP 695 generics are used throughout.
- **Cache key for multi-env**: Cache keys are namespaced by `api_base_url`, so simultaneous testnet/mainnet clients do not share cached data. Test suites that use module-level caches must clear them between tests (e.g. `_SEMI_STATIC_CACHE.clear()`) since the key is env-based, not instance-based.
- **`timestamped_auth` flag**: `_get_auth_headers` supports timestamped signing (`f"dexalot{ts}"` + `x-timestamp` header) via `config.timestamped_auth = True` (env: `DEXALOT_TIMESTAMPED_AUTH=true`). Defaults to `False` — the backend currently only accepts the static `"dexalot"` message. Enable only after backend confirms timestamp window validation. See remediation plan C-2.
- **Cache stampede protection**: `async_ttl_cached` coalesces concurrent callers for the same uncached key using `asyncio.Future`. Only the first caller fetches; the rest await the same future. Prevents thundering herd on cache misses.
- **Cached state rehydration cleanup**: Methods that return a payload and also rebuild internal SDK state from cached `Result` values currently use per-method rehydration hooks. A small shared helper/pattern for this could simplify the code later, but it is optional and not urgent now that behavior is consistent and fully covered.
- **Cache cleanup is amortized**: `MemoryCache._cleanup()` runs every 50 writes (`_CLEANUP_INTERVAL`), not on every `set`. Size enforcement (`_trim()`) runs on every write. Separate concerns.
- **Rate limiter concurrent sleeps**: `AsyncRateLimiter` acquires the lock only to reserve the slot, then releases before sleeping. Multiple waiters sleep independently — no serialization of the wait itself.
- **Nonce manager lock is now lock-free on lookup**: The global `_dict_lock` was removed. `_get_lock()` uses `dict.setdefault()`, which is safe in asyncio's single-threaded model. Per-(chain_id, address) `asyncio.Lock` objects are still used for sequential nonce acquisition.
- **ERC20 balance concurrency**: `_fetch_erc20_balances_list` uses `asyncio.Semaphore(config.erc20_balance_concurrency)` (default 10) to cap simultaneous `balanceOf` RPC calls. Prevents RPC overload during bulk balance fetches.
- **RPC security enforcement**: `_reject_insecure_rpc_urls()` in `base.py` rejects plain `http://` RPC endpoints at provider setup time unless `config.allow_insecure_rpc=True`. Fail-fast before any traffic is sent over plaintext.

---

## Remediation Workflow

Security and performance issues are tracked in `docs/python-sdk-remediation-plan.md`. When working on these items:

1. **One at a time** — fix issues individually (or closely related ones together)
2. **Plan first** — enter plan mode to discuss approach before writing code; decide: resolve, mitigate, or skip
3. **Implement** — update code based on the agreed approach
4. **Test** — add unit tests covering the changes; all of the following must pass before a change is ready:
   - `make test` — unit tests
   - `make lint` — ruff linting
   - `make mypy` — strict type checking
5. **Document** — update README.md and this CLAUDE.md as needed
6. **Update plan doc** — mark the issue status (✅ Resolved / 🔶 Mitigated / ⏭️ Skipped) and add commit/PR reference

---

## Key File Reference

All paths relative to `src/dexalot_sdk/`.

| Component | Path |
|---|---|
| Entry point | `core/client.py` |
| Config | `core/config.py` |
| Base client | `core/base.py` |
| Caching | `utils/cache.py` |
| Result type | `utils/result.py` |
| Retry | `utils/retry.py` |
| Rate limiting | `utils/rate_limit.py` |
| Nonce manager | `utils/nonce_manager.py` |
| Provider failover | `utils/provider_manager.py` |
| WebSocket | `utils/websocket_manager.py` |
| Error sanitizer | `utils/error_sanitizer.py` |
| Observability | `utils/observability.py` |
| Input validation | `utils/input_validators.py` |
| Token/pair normalization | `utils/token_normalization.py`, `data/token_aliases.json` |
