import asyncio
import os
from dataclasses import dataclass
from typing import Any, cast

import aiohttp
from dotenv import load_dotenv

from ..constants import (
    API_BASE_URL_MAINNET,
    API_BASE_URL_TESTNET,
)


@dataclass
class DexalotConfig:
    """
    Configuration for DexalotClient.
    Centralizes all configuration parameters and handles loading from environment variables.
    """

    # connection settings
    parent_env: str = "fuji-multi"
    api_base_url: str | None = None
    private_key: str | None = None
    # When True, signs "dexalot{ts}" and sends x-timestamp header instead of static "dexalot".
    # Prevents signature replay attacks but requires backend support — default False until
    # backend confirms timestamp window validation. See remediation plan C-2.
    timestamped_auth: bool = False
    connection_pool_limit: int = 100  # Total connection pool size across all hosts
    connection_pool_limit_per_host: int = 30  # Maximum connections per individual host

    # cache settings
    enable_cache: bool = True
    cache_ttl_static: int = 3600
    cache_ttl_semi_static: int = 900
    cache_ttl_balance: int = 10
    cache_ttl_orderbook: int = 1

    # timeout settings (connect, read)
    timeouts: tuple[int, int] = (5, 30)

    # logging settings
    log_level: str = "INFO"
    log_format: str = "console"

    # retry settings
    retry_enabled: bool = True
    retry_max_attempts: int = 3
    retry_initial_delay: float = 1.0  # seconds
    retry_max_delay: float = 10.0  # seconds
    retry_exponential_base: float = 2.0
    retry_on_status: tuple[int, ...] = (429, 500, 502, 503, 504)  # HTTP status codes
    retry_on_exceptions: tuple[type[BaseException], ...] = (
        aiohttp.ClientError,
        asyncio.TimeoutError,
    )

    # rate limiting settings
    rate_limit_enabled: bool = True
    rate_limit_requests_per_second: float = 5.0  # API calls per second
    rate_limit_rpc_per_second: float = 10.0  # RPC calls per second

    # nonce manager settings
    nonce_manager_enabled: bool = True  # Enable nonce manager to prevent race conditions

    # websocket settings
    ws_manager_enabled: bool = False  # Enable/disable WebSocket Manager (persistent connections)
    ws_ping_interval: int = 30  # Seconds between ping messages
    ws_ping_timeout: int = 10  # Seconds to wait for pong before reconnecting
    ws_reconnect_initial_delay: float = 1.0  # Initial reconnect delay in seconds
    ws_reconnect_max_delay: float = 60.0  # Maximum reconnect delay in seconds
    ws_reconnect_exponential_base: float = 2.0  # Exponential backoff multiplier
    ws_reconnect_max_attempts: int = 10  # Maximum reconnection attempts (0 = infinite)
    # Clock skew compensation for WebSocket private-topic signatures.
    # Added to the system timestamp (ms) when generating auth signatures.
    # Use a negative value if the local clock is ahead of the server.
    # The backend accepts timestamps within ±30 000 ms of server time.
    ws_time_offset_ms: int = 0

    # provider failover settings
    provider_failover_enabled: bool = True  # Enable/disable provider failover
    provider_failover_cooldown: int = 60  # Seconds before retrying failed provider
    provider_failover_max_failures: int = 3  # Max failures before marking provider unhealthy

    # ERC20 balance concurrency limit
    # Maximum number of concurrent balanceOf RPC calls in _fetch_erc20_balances_list.
    # Prevents overwhelming the RPC provider when a chain has many tokens.
    erc20_balance_concurrency: int = 10

    # RPC security settings
    # If False (default), http:// RPC URLs are rejected at provider setup time with ValueError.
    # Set to True only for local development or trusted private networks.
    allow_insecure_rpc: bool = False

    @classmethod
    def from_env(cls, **kwargs) -> "DexalotConfig":
        """
        Load configuration starting from environment variables, then overriding with kwargs.
        Precedence:
        1. kwargs (Constructor Arguments)
        2. os.environ (System Environment Variables)
        3. .env file (Loaded if not present in os.environ)
        4. Defaults (Class Attributes)
        """
        # Load .env file without overriding existing environment variables
        # Try multiple locations: project root (3 levels up) and current working directory
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_paths = [
            os.path.join(base_path, ".env"),  # Project root
            os.path.join(os.getcwd(), ".env"),  # Current working directory
        ]
        for env_path in env_paths:
            if os.path.exists(env_path):
                load_dotenv(dotenv_path=env_path, override=False)
                break
        else:
            # If no .env found, try loading from default location (current dir)
            load_dotenv(override=False)

        # Helper to get env var with fallback
        def get_env_bool(key: str, default: bool) -> bool:
            val = os.getenv(key)
            if val is None:
                return default
            return val.lower() in ("true", "1", "t")

        def get_env_int(key: str, default: int) -> int:
            val = os.getenv(key)
            if val is None:
                return default
            try:
                return int(val)
            except ValueError:
                return default

        def get_env_float(key: str, default: float) -> float:
            val = os.getenv(key)
            if val is None:
                return default
            try:
                return float(val)
            except ValueError:
                return default

        # Base configuration from environment
        config_data = {
            "parent_env": os.getenv("PARENTENV", "fuji-multi"),
            "private_key": os.getenv("PRIVATE_KEY"),
            "enable_cache": get_env_bool("DEXALOT_ENABLE_CACHE", True),
            "cache_ttl_static": get_env_int("DEXALOT_CACHE_TTL_STATIC", 3600),
            "cache_ttl_semi_static": get_env_int("DEXALOT_CACHE_TTL_SEMI_STATIC", 900),
            "cache_ttl_balance": get_env_int("DEXALOT_CACHE_TTL_BALANCE", 10),
            "cache_ttl_orderbook": get_env_int("DEXALOT_CACHE_TTL_ORDERBOOK", 1),
            "log_level": os.getenv("DEXALOT_LOG_LEVEL", "INFO"),
            "log_format": os.getenv("DEXALOT_LOG_FORMAT", "console"),
            "retry_enabled": get_env_bool("DEXALOT_RETRY_ENABLED", True),
            "retry_max_attempts": get_env_int("DEXALOT_RETRY_MAX_ATTEMPTS", 3),
            "retry_initial_delay": get_env_float("DEXALOT_RETRY_INITIAL_DELAY", 1.0),
            "retry_max_delay": get_env_float("DEXALOT_RETRY_MAX_DELAY", 10.0),
            "rate_limit_enabled": get_env_bool("DEXALOT_RATE_LIMIT_ENABLED", True),
            "rate_limit_requests_per_second": get_env_float(
                "DEXALOT_RATE_LIMIT_REQUESTS_PER_SECOND", 5.0
            ),
            "rate_limit_rpc_per_second": get_env_float("DEXALOT_RATE_LIMIT_RPC_PER_SECOND", 10.0),
            "nonce_manager_enabled": get_env_bool("DEXALOT_NONCE_MANAGER_ENABLED", True),
            "timestamped_auth": get_env_bool("DEXALOT_TIMESTAMPED_AUTH", False),
            "connection_pool_limit": get_env_int("DEXALOT_CONNECTION_POOL_LIMIT", 100),
            "connection_pool_limit_per_host": get_env_int(
                "DEXALOT_CONNECTION_POOL_LIMIT_PER_HOST", 30
            ),
            "ws_manager_enabled": get_env_bool("DEXALOT_WS_MANAGER_ENABLED", False),
            "ws_ping_interval": get_env_int("DEXALOT_WS_PING_INTERVAL", 30),
            "ws_ping_timeout": get_env_int("DEXALOT_WS_PING_TIMEOUT", 10),
            "ws_reconnect_initial_delay": get_env_float("DEXALOT_WS_RECONNECT_INITIAL_DELAY", 1.0),
            "ws_reconnect_max_delay": get_env_float("DEXALOT_WS_RECONNECT_MAX_DELAY", 60.0),
            "ws_reconnect_exponential_base": get_env_float(
                "DEXALOT_WS_RECONNECT_EXPONENTIAL_BASE", 2.0
            ),
            "ws_reconnect_max_attempts": get_env_int("DEXALOT_WS_RECONNECT_MAX_ATTEMPTS", 10),
            "ws_time_offset_ms": get_env_int("DEXALOT_WS_TIME_OFFSET_MS", 0),
            "provider_failover_enabled": get_env_bool("DEXALOT_PROVIDER_FAILOVER_ENABLED", True),
            "provider_failover_cooldown": get_env_int("DEXALOT_PROVIDER_FAILOVER_COOLDOWN", 60),
            "provider_failover_max_failures": get_env_int(
                "DEXALOT_PROVIDER_FAILOVER_MAX_FAILURES", 3
            ),
            "allow_insecure_rpc": get_env_bool("DEXALOT_ALLOW_INSECURE_RPC", False),
            "erc20_balance_concurrency": get_env_int("DEXALOT_ERC20_BALANCE_CONCURRENCY", 10),
        }

        # API Base URL logic
        # Logic from base.py:
        # if "fuji" in parent_env.lower():
        #     api_base_url = os.getenv("API_BASE_URL_TESTNET", API_BASE_URL_TESTNET)
        # else:
        #     api_base_url = os.getenv("API_BASE_URL_MAINNET", API_BASE_URL_MAINNET)

        # We start with what's in env vars for specific URLs
        parent_env_for_logic = str(
            kwargs.get("parent_env", config_data["parent_env"]) or config_data["parent_env"]
        )
        if "fuji" in parent_env_for_logic.lower():
            default_url = os.getenv("API_BASE_URL_TESTNET", API_BASE_URL_TESTNET)
        else:
            default_url = os.getenv("API_BASE_URL_MAINNET", API_BASE_URL_MAINNET)

        config_data["api_base_url"] = default_url

        # Override with kwargs if present
        # We only update keys that exist in our dataclass fields to avoid unexpected kwargs
        # but the class is valid, so we can just iterate over kwargs that match fields
        valid_fields = cls.__dataclass_fields__.keys()
        for k, v in kwargs.items():
            if k in valid_fields and v is not None:
                config_data[k] = v

        return cls(**cast(Any, config_data))

    def validate(self):  # noqa: C901
        """
        Validate the configuration.
        """
        if not self.parent_env:
            raise ValueError("parent_env cannot be empty")

        if self.api_base_url and self.api_base_url.endswith("/"):
            self.api_base_url = self.api_base_url[:-1]

        if self.private_key:
            # Private key must start with "0x" and be exactly 66 characters total
            # (32 bytes = 64 hex characters + "0x" prefix)
            if not self.private_key.startswith("0x"):
                raise ValueError('private_key must start with "0x"')
            if len(self.private_key) != 66:
                raise ValueError(
                    f'private_key must be 66 characters (including "0x"), got {len(self.private_key)} characters'
                )

        # Validate retry settings
        if self.retry_max_attempts < 1:
            raise ValueError("retry_max_attempts must be at least 1")
        if self.retry_initial_delay < 0:
            raise ValueError("retry_initial_delay must be non-negative")
        if self.retry_max_delay < self.retry_initial_delay:
            raise ValueError("retry_max_delay must be >= retry_initial_delay")
        if self.retry_exponential_base < 1.0:
            raise ValueError("retry_exponential_base must be >= 1.0")

        # Validate rate limit settings
        if self.rate_limit_requests_per_second <= 0:
            raise ValueError("rate_limit_requests_per_second must be positive")
        if self.rate_limit_rpc_per_second <= 0:
            raise ValueError("rate_limit_rpc_per_second must be positive")

        # Validate connection pool settings
        if self.connection_pool_limit < 1:
            raise ValueError("connection_pool_limit must be at least 1")
        if self.connection_pool_limit_per_host < 1:
            raise ValueError("connection_pool_limit_per_host must be at least 1")
        if self.connection_pool_limit_per_host > self.connection_pool_limit:
            raise ValueError("connection_pool_limit_per_host must be <= connection_pool_limit")

        # Validate websocket settings
        if self.ws_ping_interval < 1:
            raise ValueError("ws_ping_interval must be at least 1")
        if self.ws_ping_timeout < 1:
            raise ValueError("ws_ping_timeout must be at least 1")
        if self.ws_reconnect_initial_delay < 0:
            raise ValueError("ws_reconnect_initial_delay must be non-negative")
        if self.ws_reconnect_max_delay < self.ws_reconnect_initial_delay:
            raise ValueError("ws_reconnect_max_delay must be >= ws_reconnect_initial_delay")
        if self.ws_reconnect_exponential_base < 1.0:
            raise ValueError("ws_reconnect_exponential_base must be >= 1.0")
        if self.ws_reconnect_max_attempts < 0:
            raise ValueError("ws_reconnect_max_attempts must be non-negative")

        # Validate provider failover settings
        if self.provider_failover_cooldown < 0:
            raise ValueError("provider_failover_cooldown must be non-negative")
        if self.provider_failover_max_failures < 1:
            raise ValueError("provider_failover_max_failures must be at least 1")
