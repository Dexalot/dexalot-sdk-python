# Chain IDs
CHAIN_ID_AVAX_MAINNET = 43114
CHAIN_ID_AVAX_FUJI = 43113

# Environments
ENV_PROD_MULTI_AVAX = "production-multi-avax"
ENV_FUJI_MULTI_AVAX = "fuji-multi-avax"
ENV_PROD_MULTI_SUBNET = "production-multi-subnet"
ENV_FUJI_MULTI_SUBNET = "fuji-multi-subnet"

# API URLs
API_BASE_URL_MAINNET = "https://api.dexalot.com"
API_BASE_URL_TESTNET = "https://api.dexalot-test.com"


def ws_api_url_for_rest_base(rest_api_base_url: str | None) -> str:
    """
    Build the Dexalot WebSocket URL from the REST API base URL.

    See docs/websocket.md (Server Urls): wss://api.dexalot.com/api/ws and
    wss://api.dexalot-test.com/api/ws.
    """
    base = (rest_api_base_url or API_BASE_URL_MAINNET).strip().rstrip("/")
    if base.startswith("https://"):
        host = base[len("https://") :]
        return f"wss://{host}/api/ws"
    if base.startswith("http://"):
        host = base[len("http://") :]
        return f"ws://{host}/api/ws"
    raise ValueError(f"Unsupported REST API base URL for WebSocket: {rest_api_base_url!r}")


# Default WebSocket URL (mainnet). Prefer ws_api_url_for_rest_base(client.api_base_url).
WS_API_URL = ws_api_url_for_rest_base(API_BASE_URL_MAINNET)

# API Endpoints
ENDPOINT_TRADING_PAIRS = "/privapi/trading/pairs"
ENDPOINT_TRADING_ENVIRONMENTS = "/privapi/trading/environments"
ENDPOINT_TRADING_TOKENS = "/privapi/trading/tokens"
ENDPOINT_TRADING_DEPLOYMENT = "/privapi/trading/deployment"
ENDPOINT_SIGNED_ORDERS = "/privapi/signed/orders"
# Paginated per-account order history (any status) under the
# `/api/trading/signed/` mountpoint.  Requires the `x-signature` header.
# Distinct from `ENDPOINT_SIGNED_ORDERS` above — `/privapi/signed/orders`
# returns the currently-open orders for the connected wallet (used by
# `get_open_orders`), while `/api/trading/signed/orders` returns the
# full historical order list (any status, paginated, supports
# `pair` / `status` / `limit` / `offset` filters and an explicit
# `traderaddress`).  The trade-kit's `clob_get_orders_by_account` tool
# hits this path via its `signedGet("orders", ...)` helper which mounts
# at `${baseUrl}/trading/signed/<path>`.
ENDPOINT_TRADING_SIGNED_ORDERS_HISTORY = "/api/trading/signed/orders"
ENDPOINT_RFQ_PAIRS = "/api/rfq/pairs"
ENDPOINT_RFQ_FIRM_QUOTE = "/api/rfq/firmQuote"
ENDPOINT_RFQ_PAIR_PRICE = "/api/rfq/pairprice"
# Public market-data endpoints live under /api/, not /privapi/.  The candle
# count-back endpoint and global market snapshot are only mounted on the public
# /api/ tree on the backend (not duplicated under /privapi/).
ENDPOINT_TRADING_CANDLE_CHUNK = "/api/trading/candle-chunk"
ENDPOINT_STATS_MARKET_SNAPSHOT = "/api/stats/market-snapshot"
# Public info tree (no auth, no `env` query at the backend — the host
# determines the network — but the SDK still forwards `env` for parity
# with the TypeScript SDK and cache-key namespacing on the client).
ENDPOINT_INFO_USD_PRICES = "/api/info/usd-prices"
# Daily and hourly USD price history per token.  Backend ignores
# `from`/`to`/`env` query params today (host determines network, range
# is fixed window) but the SDK forwards them for forward-compat and
# cache-key namespacing; if a caller supplies `from_ts`/`to_ts` the
# response is additionally filtered client-side so the contract remains
# useful when the backend gains range support.
ENDPOINT_INFO_PRICE_HISTORY = "/api/info/token-usd-price-history"
ENDPOINT_INFO_HOURLY_PRICE_HISTORY = "/api/info/token-usd-price-history-hourly"
# Unified transfer history (deposits + withdrawals + p2p + gas) under the
# `/api/trading/signed/` mountpoint. Requires the `x-signature` header.
# The backend route does not exist under `/privapi/...` — confirmed
# empirically (404 on /privapi/trading/signed/transferscombined,
# 204 OPTIONS on /api/trading/signed/transferscombined).
ENDPOINT_TRADING_COMBINED_TRANSFERS = "/api/trading/signed/transferscombined"

# Default Values
DEFAULT_DECIMALS = 18
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEFAULT_TAKER_ADDRESS = (
    "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"  # Vitalik's address as placeholder
)
GAS_BUFFER = 1.2

# Bridge Constants
BRIDGE_ID_LZ = 0
BRIDGE_ID_ICM = 2
ICM_CHAINS = ["Avalanche", "Fuji"]
