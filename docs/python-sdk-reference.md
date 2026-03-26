# API Reference

Auto-generated from source docstrings via [mkdocstrings](https://mkdocstrings.github.io/). For protocol-level details see [REST API](rest-api.md), [WebSocket Protocol](websocket.md), and [Simple Swap](simple-swap.md).

---

## Module layout

```
dexalot_sdk/
├── core/
│   ├── client.py        ← DexalotClient (entry point)
│   ├── base.py          ← DexalotBaseClient (auth, HTTP, Web3, cache)
│   ├── clob.py          ← CLOBClient (order book, trading)
│   ├── swap.py          ← SwapClient (RFQ, simple swap)
│   ├── transfer.py      ← TransferClient (balances, deposit, withdraw)
│   └── config.py        ← DexalotConfig
└── utils/
    ├── result.py         ← Result[T]
    ├── cache.py          ← MemoryCache, async_ttl_cached
    ├── websocket_manager.py
    ├── rate_limit.py
    ├── retry.py
    ├── nonce_manager.py
    ├── provider_manager.py
    └── error_sanitizer.py
```

**Inheritance chain** (`DexalotClient` MRO, simplified):

```
DexalotClient
├── CLOBClient
├── SwapClient
├── TransferClient
└── DexalotBaseClient   ← common base for all three mixins
```

---

## DexalotClient

::: dexalot_sdk.core.client.DexalotClient

---

## DexalotBaseClient

::: dexalot_sdk.core.base.DexalotBaseClient

---

## CLOBClient

::: dexalot_sdk.core.clob.CLOBClient

---

## SwapClient

::: dexalot_sdk.core.swap.SwapClient

---

## TransferClient

::: dexalot_sdk.core.transfer.TransferClient

---

## Result\[T\]

::: dexalot_sdk.utils.result.Result

---

## DexalotConfig

::: dexalot_sdk.core.config.DexalotConfig

---

## MemoryCache

::: dexalot_sdk.utils.cache.MemoryCache
