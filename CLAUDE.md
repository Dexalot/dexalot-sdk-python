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

Caches are **module-level singletons shared across all client instances**, not per-instance. Per-instance `_cache_enabled` flag can bypass the cache entirely. There is no per-environment isolation — running testnet and mainnet clients simultaneously will share cache incorrectly.

### Result[T] pattern — no exceptions

All SDK methods return `Result(success, data, error)` and never raise. Callers must always check `.success` before accessing `.data`. Factory methods: `Result.ok(data)` and `Result.fail(error_msg)`.

### Config loading and validation

Precedence: constructor kwargs → env vars → `.env` file → defaults. `PARENTENV` selects environment: `fuji-multi` (testnet, default) or `production-multi` (mainnet). **`config.validate()` is not called automatically** — call it explicitly after construction or invalid configs won't surface until the first method call.

### WebSocket uses threading, not asyncio

`websocket-client` doesn't support async; threading avoids event loop blocking. WebSocket is opt-in (`ws_manager_enabled=False` by default). WebSocket callbacks cannot be directly `await`-ed in async code — bridges are required.

### Rate limiters are per-instance

Each client instance has its own rate limiter (default: 5 API calls/sec, 10 RPC calls/sec). Multiple concurrent instances do **not** share quotas and can collectively exceed them. No centralized rate limiter exists.

### Nonce manager: correctness over throughput

Per-(chain_id, address) asyncio locks enforce sequential nonce acquisition. High-frequency transaction batching will contend on these locks — this is intentional to prevent double-nonce errors.

### Multi-provider RPC failover

`ProviderManager` tracks failure counts per provider and auto-recovers after a configurable cooldown (default: 60s). Falls back to secondary providers on failure.

---

## Dev Workflow

- **Package manager**: `uv` — not pip, not poetry
- **Python version**: >=3.12, <3.14 (uses match statements, PEP 695 generics)
- **Setup**: `make setup` (runs `uv venv && uv sync --group dev`)
- **Test**: `make test` (unit, fast) / `make int` (integration, requires live env)
- **Lint**: `make lint` / `make lint-fix` (ruff, line-length=100)
- **Types**: `make mypy` (strict mode)
- **Coverage**: `make cov`

Unit tests in `tests/unit/` have no external dependencies. Integration tests in `tests/integration/` require a live API environment.

- `VERSION` file at repo root holds the current version (currently 0.4.0)
- `.env` files: never commit; use `env.example` as template
- The error sanitizer strips file paths, URLs, and stack traces from user-facing errors. Use `log_level="DEBUG"` locally to get full context for debugging.

---

## Non-Obvious Decisions

- **Private key handling**: After `Account` creation, `config.private_key` is zeroed out. Prefer passing a pre-built signer object so the raw key never touches the config at all.
- **Cache key generation**: Uses `(func_name, args, frozenset(kwargs.items()))` — kwarg ordering affects cache hits; deep objects may produce false misses.
- **Config validation timing**: `config.validate()` must be called explicitly after construction; see above.
- **Error sanitization is lossy**: Regex stripping makes production debugging harder. Use DEBUG logging in development.
- **Python 3.12+ is required**: CI must enforce this. Match statements and PEP 695 generics are used throughout.
- **Cache key for multi-env**: If you ever need simultaneous testnet/mainnet clients, the module-level caches will conflict — this is a known limitation.

---

## Remediation Workflow

Security and performance issues are tracked in `docs/python-sdk-remediation-plan.md`. When working on these items:

1. **One at a time** — fix issues individually (or closely related ones together)
2. **Plan first** — enter plan mode to discuss approach before writing code; decide: resolve, mitigate, or skip
3. **Implement** — update code based on the agreed approach
4. **Test** — add unit tests covering the changes
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
