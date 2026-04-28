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
ENDPOINT_RFQ_PAIRS = "/api/rfq/pairs"
ENDPOINT_RFQ_FIRM_QUOTE = "/api/rfq/firmQuote"
ENDPOINT_RFQ_PAIR_PRICE = "/api/rfq/pairprice"
# Public market-data endpoints live under /api/, not /privapi/.  The candle
# count-back endpoint and global market snapshot are only mounted on the public
# /api/ tree on the backend (not duplicated under /privapi/).
ENDPOINT_TRADING_CANDLE_CHUNK = "/api/trading/candle-chunk"
ENDPOINT_STATS_MARKET_SNAPSHOT = "/api/stats/market-snapshot"

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
