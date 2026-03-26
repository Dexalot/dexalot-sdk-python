# Dexalot Python SDK

Python SDK for the [Dexalot](https://dexalot.com) decentralized exchange. Supports limit-order trading (CLOB), RFQ-based simple swaps, portfolio management, and real-time WebSocket event streaming.

---

## Quick start

```python
import asyncio
from dexalot_sdk import DexalotClient

async def main():
    async with DexalotClient() as client:
        result = await client.get_trading_pairs()
        if result.success:
            for pair in result.data:
                print(pair["pair"])

asyncio.run(main())
```

Install:

```bash
pip install dexalot-sdk
```

---

## Documentation

| Section | Description |
|---|---|
| [User Guide](python-sdk-user-guide.md) | Installation, concepts, and end-to-end usage walkthrough |
| [API Reference](python-sdk-reference.md) | Auto-generated reference for all public classes and methods |
| [Error Handling](python-sdk-error-handling.md) | `Result[T]` pattern, revert reasons, and debugging |
| [Architecture](python-sdk-architecture.md) | Internals: caching, async model, rate limiting, nonce management |
| [Caching Guide](sdk-caching.md) | Cache tiers, TTL tuning, and invalidation |
| [WebSocket Protocol](websocket.md) | WebSocket message format and event types |
| [REST API](rest-api.md) | Underlying REST API endpoints |
| [Simple Swap](simple-swap.md) | RFQ swap flow and quote lifecycle |

---

## Key features

- **No exceptions** — all methods return `Result(success, data, error)`; never raises
- **Async-first** — built on `asyncio`; context-manager lifecycle (`async with`)
- **4-tier cache** — static, semi-static, balance, and orderbook tiers with configurable TTLs
- **WebSocket events** — subscribe to live order, trade, and balance updates
- **Multi-provider RPC** — automatic failover across configured RPC endpoints
- **Rate limiting** — per-instance token-bucket limiters for API and RPC calls
- **Error sanitization** — strips file paths and stack traces from user-facing errors in production

---

## Version

Current release: **0.4.0** — Python ≥ 3.12
