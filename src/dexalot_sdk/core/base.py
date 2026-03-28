import asyncio
import copy
import json
import logging
import os
import sys
from typing import Any, cast

import aiohttp
from eth_account import Account
from web3 import AsyncHTTPProvider, AsyncWeb3

from ..constants import (
    CHAIN_ID_AVAX_FUJI,
    CHAIN_ID_AVAX_MAINNET,
    ENDPOINT_RFQ_PAIRS,
    ENDPOINT_TRADING_DEPLOYMENT,
    ENDPOINT_TRADING_ENVIRONMENTS,
    ENDPOINT_TRADING_TOKENS,
    ENV_FUJI_MULTI_AVAX,
    ENV_FUJI_MULTI_SUBNET,
    ENV_PROD_MULTI_AVAX,
    ENV_PROD_MULTI_SUBNET,
)
from ..utils.cache import MemoryCache, async_ttl_cached
from ..utils.error_sanitizer import sanitize_error_message
from ..utils.nonce_manager import AsyncNonceManager
from ..utils.observability import configure_logging, get_logger, log_event, track_operation
from ..utils.provider_manager import ProviderManager
from ..utils.rate_limit import AsyncRateLimiter
from ..utils.result import Result
from ..utils.retry import async_retry
from .config import DexalotConfig

_ALLOWED_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_ALLOWED_RPC_METHODS = frozenset(
    {
        "eth.gas_price",
        "eth.send_raw_transaction",
        "eth.wait_for_transaction_receipt",
        "eth.get_transaction_count",
    }
)

# Module-level caches (shared across all client instances)
# These can be reconfigured via DexalotClient constructor parameters
_STATIC_CACHE = MemoryCache(ttl_seconds=3600, max_size=128)  # 1 hour
_SEMI_STATIC_CACHE = MemoryCache(ttl_seconds=900, max_size=256)  # 15 minutes
_BALANCE_CACHE = MemoryCache(ttl_seconds=10, max_size=512)  # 10 seconds
_ORDERBOOK_CACHE = MemoryCache(ttl_seconds=1, max_size=256)  # 1 second


class DexalotBaseClient:
    # Instance attribute annotations (mypy / optional Web3 and limiter state)
    _http_rate_limiter: AsyncRateLimiter | None
    _rpc_rate_limiter: AsyncRateLimiter | None
    _nonce_manager: AsyncNonceManager | None
    _provider_manager: ProviderManager | None

    # Constants
    CHAIN_ID_AVAX_MAINNET = CHAIN_ID_AVAX_MAINNET
    CHAIN_ID_AVAX_FUJI = CHAIN_ID_AVAX_FUJI
    ENV_PROD_MULTI_AVAX = ENV_PROD_MULTI_AVAX
    ENV_FUJI_MULTI_AVAX = ENV_FUJI_MULTI_AVAX
    ENV_PROD_MULTI_SUBNET = ENV_PROD_MULTI_SUBNET
    ENV_FUJI_MULTI_SUBNET = ENV_FUJI_MULTI_SUBNET

    def __init__(
        self,
        signer: "Account | None" = None,
        parent_env: str | None = None,
        enable_cache: bool | None = None,
        cache_ttl_static: int | None = None,
        cache_ttl_semi_static: int | None = None,
        cache_ttl_balance: int | None = None,
        cache_ttl_orderbook: int | None = None,
        config: "DexalotConfig | None" = None,
    ):
        """Initialize DexalotBaseClient.

        Args:
            signer: Optional web3 Account for signing transactions.
                    If None, falls back to PRIVATE_KEY in config.
            parent_env: Optional environment override (e.g., 'fuji-multi').
            enable_cache: Override cache enablement. If ``None``, use env / config defaults.
            cache_ttl_static: Override static-cache TTL in seconds.
            cache_ttl_semi_static: Override semi-static-cache TTL in seconds.
            cache_ttl_balance: Override balance-cache TTL in seconds.
            cache_ttl_orderbook: Override orderbook-cache TTL in seconds.
            config: Optional DexalotConfig object. If provided, other config args are ignored.
        """
        self.config = self._initialize_config(
            config,
            parent_env,
            enable_cache,
            cache_ttl_static,
            cache_ttl_semi_static,
            cache_ttl_balance,
            cache_ttl_orderbook,
        )
        self.config.validate()

        self.parent_env = self.config.parent_env
        self.api_base_url = self.config.api_base_url

        self.chain_id: int | None = None
        self.subnet_chain_id: int | None = None
        self.w3_l1: AsyncWeb3[Any] | None = None
        self.w3_connected_chain: AsyncWeb3[Any] | None = None
        self.connected_chain_providers: dict[str, AsyncWeb3[Any]] = {}
        self.account: Account | None = None
        self._session: aiohttp.ClientSession | None = None

        self._setup_logging()
        self.error_codes = self._load_error_codes()
        self.account = self._setup_account(signer)
        self._initialize_data_structures()
        self._configure_caches()
        self._setup_rate_limiters()
        self._setup_nonce_manager()
        self._setup_provider_manager()

    def _initialize_config(
        self,
        config: "DexalotConfig | None",
        parent_env: str | None,
        enable_cache: bool | None,
        cache_ttl_static: int | None,
        cache_ttl_semi_static: int | None,
        cache_ttl_balance: int | None,
        cache_ttl_orderbook: int | None,
    ) -> "DexalotConfig":
        """Initialize configuration from provided config or environment."""
        if config is None:
            kwargs: dict[str, Any] = {}
            if parent_env is not None:
                kwargs["parent_env"] = parent_env

            if enable_cache is not None:
                kwargs["enable_cache"] = enable_cache
            if cache_ttl_static is not None:
                kwargs["cache_ttl_static"] = cache_ttl_static
            if cache_ttl_semi_static is not None:
                kwargs["cache_ttl_semi_static"] = cache_ttl_semi_static
            if cache_ttl_balance is not None:
                kwargs["cache_ttl_balance"] = cache_ttl_balance
            if cache_ttl_orderbook is not None:
                kwargs["cache_ttl_orderbook"] = cache_ttl_orderbook

            return DexalotConfig.from_env(**kwargs)
        return config

    def _setup_logging(self):
        """Configure logging if not already configured."""
        logger = logging.getLogger("dexalot_sdk")
        if not logger.handlers:
            configure_logging(
                log_level=self.config.log_level,
                log_format=self.config.log_format,
            )
        self.logger = get_logger(__name__)

    def _load_error_codes(self) -> dict:
        """Load error codes from errors.json file."""
        error_codes = {}
        try:
            errors_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "errors.json")
            with open(errors_path) as f:
                error_codes = json.load(f)
        except Exception:
            pass
        return error_codes

    def _setup_account(self, signer: "Account | None") -> "Account | None":
        """Set up account from signer or config private key."""
        if signer:
            return signer

        private_key = self.config.private_key
        if private_key:
            try:
                account = Account.from_key(private_key)
                self.config.private_key = None
                return cast(Account | None, account)
            except Exception:
                self.config.private_key = None
                return None
        return None

    def _initialize_data_structures(self):
        """Initialize data structure attributes."""
        self.deployments: dict[str, Any] = {
            "TradePairs": {},
            "PortfolioMain": {},
            "PortfolioSub": {},
            "MainnetRFQ": {},
        }
        self.pairs: dict[str, Any] = {}
        self.token_data: dict[str, Any] = {}
        self.rfq_pairs: dict[Any, Any] = {}
        self.chain_config: dict[str, Any] = {}

        # Contract instances
        self.trade_pairs_contract = None
        self.portfolio_main_avax_contract = None
        self.portfolio_sub_contract = None

    def _configure_caches(self):
        """Configure module-level caches with custom TTL if provided."""
        self._cache_enabled = self.config.enable_cache
        if not self._cache_enabled:
            return

        global _STATIC_CACHE, _SEMI_STATIC_CACHE, _BALANCE_CACHE, _ORDERBOOK_CACHE
        if self.config.cache_ttl_static != 3600:
            _STATIC_CACHE = MemoryCache(ttl_seconds=self.config.cache_ttl_static, max_size=128)
        if self.config.cache_ttl_semi_static != 900:
            _SEMI_STATIC_CACHE = MemoryCache(
                ttl_seconds=self.config.cache_ttl_semi_static, max_size=256
            )
        if self.config.cache_ttl_balance != 10:
            _BALANCE_CACHE = MemoryCache(ttl_seconds=self.config.cache_ttl_balance, max_size=512)
        if self.config.cache_ttl_orderbook != 1:
            _ORDERBOOK_CACHE = MemoryCache(
                ttl_seconds=self.config.cache_ttl_orderbook, max_size=256
            )

    def _setup_rate_limiters(self):
        """Set up rate limiters if enabled."""
        if self.config.rate_limit_enabled:
            self._http_rate_limiter = AsyncRateLimiter(self.config.rate_limit_requests_per_second)
            self._rpc_rate_limiter = AsyncRateLimiter(self.config.rate_limit_rpc_per_second)
        else:
            self._http_rate_limiter = None
            self._rpc_rate_limiter = None

    def _setup_nonce_manager(self):
        """Set up nonce manager if enabled."""
        if self.config.nonce_manager_enabled:
            self._nonce_manager = AsyncNonceManager()
        else:
            self._nonce_manager = None

    def _setup_provider_manager(self):
        """Set up provider manager if failover is enabled."""
        if self.config.provider_failover_enabled:
            self._provider_manager = ProviderManager(self.config)
        else:
            self._provider_manager = None

    async def connect(self):
        """Initialize the aiohttp HTTP session if it is not already open.

        This is called automatically by ``initialize_client()`` and by the
        async context manager (``async with client``). You only need to call
        it directly if you construct the client outside a context manager and
        before calling ``initialize_client()``.

        Returns:
            Self, to allow method chaining (e.g. ``await client.connect()``).
        """
        if self._session is None or self._session.closed:
            # Configure timeout from config
            timeout = aiohttp.ClientTimeout(
                total=self.config.timeouts[1],  # read timeout
                connect=self.config.timeouts[0],  # connect timeout
            )
            # Set enable_cleanup_closed=True only for Python < 3.14
            # Python 3.14+ has this fix built-in, so the parameter is ignored and causes a warning
            if sys.version_info < (3, 14):
                connector = aiohttp.TCPConnector(
                    limit=self.config.connection_pool_limit,
                    limit_per_host=self.config.connection_pool_limit_per_host,
                    enable_cleanup_closed=True,
                )
            else:
                connector = aiohttp.TCPConnector(
                    limit=self.config.connection_pool_limit,
                    limit_per_host=self.config.connection_pool_limit_per_host,
                )
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self

    async def close(self):
        """Close aiohttp session, WebSocket connections, and web3 provider sessions.

        Properly closes all HTTP sessions and waits for graceful shutdown.
        """
        # Close WebSocket if CLOBClient is initialized (has _ws_manager attribute)
        if hasattr(self, "_ws_manager") and self._ws_manager is not None:
            if hasattr(self, "close_websocket"):
                await self.close_websocket()

        # Close web3 provider sessions (AsyncHTTPProvider creates internal aiohttp sessions)
        await self._close_web3_providers()

        # Close SDK's aiohttp session
        if self._session and not self._session.closed:
            await self._session.close()

        # Clear reference to help garbage collector
        self._session = None

        # Small delay to allow SSL connections to close gracefully
        await asyncio.sleep(0.250)

    async def _close_web3_providers(self):
        """Close all web3 AsyncHTTPProvider sessions."""
        # Close w3_l1 provider
        if hasattr(self, "w3_l1") and self.w3_l1 is not None:
            try:
                await self.w3_l1.provider.disconnect()
            except Exception:
                pass  # Ignore errors during cleanup
            self.w3_l1 = None

        # Close connected-chain provider
        if hasattr(self, "w3_connected_chain") and self.w3_connected_chain is not None:
            try:
                await self.w3_connected_chain.provider.disconnect()
            except Exception:
                pass
            self.w3_connected_chain = None

        # Close all connected-chain providers
        if hasattr(self, "connected_chain_providers"):
            for _name, provider in list(self.connected_chain_providers.items()):
                try:
                    if provider:
                        await provider.provider.disconnect()
                except Exception:
                    pass
            self.connected_chain_providers.clear()

    async def __aenter__(self):
        return await self.connect()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def __repr__(self) -> str:
        """String representation that redacts sensitive information."""
        account_info = "None"
        if self.account:
            # Only show address, never the private key
            account_info = f"Account(address={cast(str, cast(Any, self.account).address)})"
        return (
            f"{self.__class__.__name__}("
            f"parent_env={self.parent_env!r}, "
            f"api_base_url={self.api_base_url!r}, "
            f"account={account_info}"
            ")"
        )

    def _parse_revert_reason(self, error_msg):
        """Parses the revert reason from an exception message and returns a descriptive error."""
        error_str = str(error_msg)
        for code, description in self.error_codes.items():
            if code in error_str:
                return f"{code}: {description}"
        return error_str

    def _sanitize_error(self, error: Exception, context: str = "") -> str:
        """Sanitize an error to prevent leaking sensitive information.

        This method:
        1. Attempts to parse contract revert reasons using _parse_revert_reason()
        2. Sanitizes the error message to remove file paths, URLs, stack traces
        3. Logs the full error details internally for debugging

        Args:
            error: The exception to sanitize
            context: Optional context string describing the operation

        Returns:
            A sanitized, user-safe error message
        """
        # First, try to parse revert reason if it's a contract error
        error_str = str(error)
        parsed_reason = self._parse_revert_reason(error_str)

        # If we got a parsed reason (different from original), use it
        if parsed_reason != error_str:
            # Still sanitize to remove any paths/URLs that might be in the description
            sanitized = sanitize_error_message(parsed_reason, context)
        else:
            # Full sanitization for non-contract errors
            sanitized = sanitize_error_message(error, context)

        # Log full error details internally (with stack trace)
        self.logger.error(
            f"Error in {context or 'operation'}",
            exc_info=error,
            extra={"error_type": type(error).__name__, "sanitized_message": sanitized},
        )

        return sanitized

    def set_signer(self, signer: "Account") -> None:
        """Set signer for write operations. Lightweight - no contract reinitialization.

        Args:
            signer: web3 Account for signing transactions.
        """
        self.account = signer

    def invalidate_cache(self, level: str | None = None) -> None:
        """Invalidate cache at specified level or all levels.

        Args:
            level: Cache level to invalidate: 'static', 'semi_static', 'balance', 'orderbook',
                   or None to invalidate all levels.
        """
        if level is None or level == "static":
            _STATIC_CACHE._store.clear()
        if level is None or level == "semi_static":
            _SEMI_STATIC_CACHE._store.clear()
        if level is None or level == "balance":
            _BALANCE_CACHE._store.clear()
        if level is None or level == "orderbook":
            _ORDERBOOK_CACHE._store.clear()

    async def _make_http_request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        """
        Make HTTP request with retry and rate limiting.

        Args:
            method: HTTP method ('get', 'post', 'put', 'delete', etc.)
            url: Request URL
            **kwargs: Additional arguments to pass to aiohttp session method

        Returns:
            aiohttp.ClientResponse context manager

        Raises:
            aiohttp.ClientError: On HTTP errors (after retries exhausted)
        """
        if method.lower() not in _ALLOWED_HTTP_METHODS:
            raise ValueError(f"Unsupported HTTP method: {method!r}")

        # Apply rate limiting if enabled
        if self._http_rate_limiter:
            await self._http_rate_limiter.acquire()

        # Create retry wrapper function
        async def _do_request():
            session_method = getattr(self._session, method.lower())
            return session_method(url, **kwargs)

        # Apply retry logic if enabled
        if self.config.retry_enabled:
            retry_func = async_retry(
                max_attempts=self.config.retry_max_attempts,
                initial_delay=self.config.retry_initial_delay,
                max_delay=self.config.retry_max_delay,
                exponential_base=self.config.retry_exponential_base,
                retry_on_status=self.config.retry_on_status,
                retry_on_exceptions=self.config.retry_on_exceptions,
            )(_do_request)
            return cast(aiohttp.ClientResponse, await retry_func())
        else:
            return cast(aiohttp.ClientResponse, await _do_request())

    def _find_chain_for_provider(self, w3: AsyncWeb3) -> str | None:
        """
        Find which chain a provider belongs to (for backwards compatibility).

        Args:
            w3: AsyncWeb3 instance to find

        Returns:
            Chain name if found, None otherwise
        """
        # Check w3_l1
        if self.w3_l1 and self.w3_l1 == w3:
            return "DEXALOT_L1"
        # Check w3_connected_chain
        if self.w3_connected_chain is not None and self.w3_connected_chain == w3:
            for name, provider in self.connected_chain_providers.items():
                if provider == w3:
                    return name
        # Check connected_chain_providers
        for name, provider in self.connected_chain_providers.items():
            if provider == w3:
                return name
        return None

    async def _execute_single_rpc_call(self, w3: AsyncWeb3, method_name: str, *args, **kwargs):
        """
        Execute a single RPC call on a specific provider (without failover).

        Args:
            w3: AsyncWeb3 instance
            method_name: Method name to call
            *args: Positional arguments for the RPC method
            **kwargs: Keyword arguments for the RPC method

        Returns:
            Result of the RPC call
        """
        if method_name not in _ALLOWED_RPC_METHODS:
            raise ValueError(f"RPC method not allowed: {method_name!r}")
        # Navigate to the method/property (e.g., w3.eth.get_transaction_count or w3.eth.gas_price)
        obj = w3
        parts = method_name.split(".")
        for part in parts:
            obj = getattr(obj, part)

        # Check if it's callable (a method) or a property/attribute
        if callable(obj):
            result = obj(*args, **kwargs)
        else:
            # It's a property or attribute - might be an awaitable
            result = obj

        # Handle both coroutines and awaitables
        if asyncio.iscoroutine(result):
            return await result
        # Check if it has __await__ method (other awaitable types)
        if hasattr(result, "__await__"):
            return await result
        return result

    async def _rpc_call_with_failover(self, chain_name: str, method_name: str, *args, **kwargs):
        """
        Execute RPC call with automatic provider failover.

        Args:
            chain_name: Name of the chain
            method_name: Method name to call
            *args: Positional arguments for the RPC method
            **kwargs: Keyword arguments for the RPC method

        Returns:
            Result of the RPC call

        Raises:
            Exception: If all providers fail
        """
        pm = self._provider_manager
        if pm is None:
            raise RuntimeError("Provider manager is required for failover RPC calls")
        last_error = None
        max_providers = pm.get_provider_count(chain_name)

        for _attempt in range(max_providers):
            w3 = await pm.get_provider(chain_name)
            if not w3:
                # All providers exhausted
                error_msg = f"All RPC providers failed for chain '{chain_name}'"
                if last_error:
                    raise Exception(f"{error_msg}. Last error: {last_error}") from last_error
                raise Exception(error_msg)

            # Get provider index for tracking
            provider_index = pm.get_provider_index(chain_name, w3)
            if provider_index is None:
                provider_index = 0

            try:
                # Apply rate limiting
                if self._rpc_rate_limiter:
                    await self._rpc_rate_limiter.acquire()

                # Execute RPC call (with existing retry logic if enabled)
                if self.config.retry_enabled:
                    # Capture w3 in lambda default argument to avoid closure issue
                    retry_func = async_retry(
                        max_attempts=self.config.retry_max_attempts,
                        initial_delay=self.config.retry_initial_delay,
                        max_delay=self.config.retry_max_delay,
                        exponential_base=self.config.retry_exponential_base,
                        retry_on_status=self.config.retry_on_status,
                        retry_on_exceptions=self.config.retry_on_exceptions,
                    )(lambda w3=w3: self._execute_single_rpc_call(w3, method_name, *args, **kwargs))
                    result = await retry_func()
                else:
                    result = await self._execute_single_rpc_call(w3, method_name, *args, **kwargs)

                # Mark success
                await pm.mark_success(chain_name, provider_index)
                return result

            except Exception as e:
                last_error = e
                await pm.mark_failure(chain_name, provider_index)
                # Continue to next provider

        # All providers exhausted
        error_msg = f"All RPC providers failed for chain '{chain_name}'"
        if last_error:
            raise Exception(f"{error_msg}. Last error: {last_error}") from last_error
        raise Exception(error_msg)

    async def _rpc_call(self, w3: AsyncWeb3, method_name: str, *args, **kwargs):
        """
        Execute RPC call with retry, rate limiting, and provider failover.

        Args:
            w3: AsyncWeb3 instance
            method_name: Method name to call (e.g., 'eth.get_transaction_count' or 'eth.gas_price')
                         For properties like gas_price, don't include parentheses
            *args: Positional arguments for the RPC method (not used for properties)
            **kwargs: Keyword arguments for the RPC method (not used for properties)

        Returns:
            Result of the RPC call

        Raises:
            Exception: On RPC errors (after retries exhausted)
        """
        # If provider manager is enabled, try to use failover
        if self._provider_manager:
            chain_name = self._find_chain_for_provider(w3)
            if chain_name and self._provider_manager.get_provider_count(chain_name) > 0:
                return await self._rpc_call_with_failover(chain_name, method_name, *args, **kwargs)

        # Fallback to original implementation (single provider, no failover)
        # Apply rate limiting if enabled
        if self._rpc_rate_limiter:
            await self._rpc_rate_limiter.acquire()

        # Create retry wrapper function
        async def _do_rpc_call():
            return await self._execute_single_rpc_call(w3, method_name, *args, **kwargs)

        # Apply retry logic if enabled
        if self.config.retry_enabled:
            retry_func = async_retry(
                max_attempts=self.config.retry_max_attempts,
                initial_delay=self.config.retry_initial_delay,
                max_delay=self.config.retry_max_delay,
                exponential_base=self.config.retry_exponential_base,
                retry_on_status=self.config.retry_on_status,
                retry_on_exceptions=self.config.retry_on_exceptions,
            )(_do_rpc_call)
            return await retry_func()
        else:
            return await _do_rpc_call()

    async def _get_nonce(self, w3: AsyncWeb3, chain_id: int | None = None) -> int:
        """
        Get the next nonce for the current account on the given chain.

        Args:
            w3: AsyncWeb3 instance
            chain_id: Chain ID (if None, will be fetched from w3)

        Returns:
            Next nonce to use for the transaction

        Note:
            If account is None, this will raise an error. Callers should check
            for account before calling this method.
        """
        if not self.account:
            raise ValueError("Account is required for nonce management")

        # Use nonce manager if enabled
        acct_addr = cast(str, cast(Any, self.account).address)
        if self._nonce_manager:
            return await self._nonce_manager.get_nonce(w3, acct_addr, chain_id)
        else:
            # Fall back to direct RPC call if nonce manager is disabled
            return cast(
                int,
                await self._rpc_call(w3, "eth.get_transaction_count", acct_addr, "pending"),
            )

    async def initialize_client(self) -> Result[str]:
        """Fetch all configuration from the Dexalot API and set up on-chain contracts.

        Must be called once before using any trading, swap, or transfer methods.
        Fetches in order: environments (sets Web3 providers), then in parallel:
        tokens, RFQ pairs, contract deployments, and CLOB trading pairs.

        Returns:
            Result containing ``"Client initialized with all configurations."`` on
            success, or an error message on failure.

        Example:
            >>> async with DexalotClient() as client:
            ...     result = await client.initialize_client()
            ...     if not result.success:
            ...         raise RuntimeError(result.error)
        """
        await self.connect()
        with track_operation(self.logger, "initialize_client", parent_env=self.parent_env):
            try:
                # Fetch environments first (sets w3_l1, w3_connected_chain needed for deployments)
                await self._fetch_environments()
                # Then fetch other data in parallel
                await asyncio.gather(
                    self._fetch_tokens(),
                    self._fetch_rfq_pairs(),
                    self._fetch_deployments(),
                    self._fetch_clob_pairs(),
                )
                return Result.ok("Client initialized with all configurations.")
            except Exception as e:
                error_msg = self._sanitize_error(e, "initializing client")
                return Result.fail(error_msg)

    async def _fetch_environments(self):
        env_url = f"{self.api_base_url}{ENDPOINT_TRADING_ENVIRONMENTS}"
        async with await self._make_http_request("get", env_url) as response:
            response.raise_for_status()
            environments = await response.json()

        await self._apply_environment_state(environments)

    async def _apply_environment_state(self, environments: list[dict[str, Any]]) -> None:
        """Apply environment metadata to provider and chain state."""
        for env in environments:
            chain_id = env.get("chainid") or env.get("chain_id")
            if chain_id == self.CHAIN_ID_AVAX_MAINNET:
                self.env = self.ENV_PROD_MULTI_AVAX
            elif chain_id == self.CHAIN_ID_AVAX_FUJI:
                self.env = self.ENV_FUJI_MULTI_AVAX

            if env.get("env") == self.ENV_FUJI_MULTI_SUBNET:
                self.subnet_chain_id = chain_id
            elif env.get("env") == self.ENV_PROD_MULTI_SUBNET:
                self.subnet_chain_id = chain_id

        # Map chain names to IDs and configs
        self.chain_config = {}
        self.connected_chain_providers = {}
        self.chain_id = None
        self.w3_connected_chain = None
        self.w3_l1 = None
        for env in environments:
            await self._process_environment_config(env)

    async def _rehydrate_cached_get_environments(self, cached: Result[list]) -> None:
        """Restore provider and chain state when environments come from cache."""
        if not cached.success or cached.data is None:
            return
        await self._apply_environment_state(cached.data)

    def _reject_insecure_rpc_urls(self, urls: list[str]) -> list[str]:
        """
        Raise ValueError if any URL uses http:// and allow_insecure_rpc is False.

        Returns the list unchanged when all checks pass.
        Raises ValueError (not a silent filter) so misconfiguration is immediately visible.
        """
        insecure = [u for u in urls if u.lower().startswith("http://")]
        if insecure and not self.config.allow_insecure_rpc:
            raise ValueError(
                f"Insecure RPC URL(s) rejected (http://): {insecure}. "
                "Set allow_insecure_rpc=True or DEXALOT_ALLOW_INSECURE_RPC=true "
                "to permit http:// endpoints."
            )
        return urls

    def _get_rpc_urls(
        self, chain_id: int | None, native_token_symbol: str | None, api_rpc: str | None
    ) -> list[str]:
        """
        Get RPC URLs from env var override or API response.

        Args:
            chain_id: Numeric chain identifier (e.g., 43114 for Avalanche)
            native_token_symbol: Native token symbol (e.g., "AVAX", "ETH", "ALOT")
            api_rpc: RPC URL from API response (can be comma-separated)

        Returns:
            List of RPC URLs (primary first, fallbacks after)
        """
        # Check chain_id first (preferred method)
        if chain_id is not None:
            env_key_chain_id = f"DEXALOT_RPC_{chain_id}"
            env_rpc = os.getenv(env_key_chain_id)
            if env_rpc:
                urls = [url.strip() for url in env_rpc.split(",") if url.strip()]
                return self._reject_insecure_rpc_urls(urls)

        # Fall back to native_token_symbol
        if native_token_symbol:
            env_key_symbol = f"DEXALOT_RPC_{native_token_symbol.upper()}"
            env_rpc = os.getenv(env_key_symbol)
            if env_rpc:
                urls = [url.strip() for url in env_rpc.split(",") if url.strip()]
                return self._reject_insecure_rpc_urls(urls)

        # Final fallback to API response
        if api_rpc:
            urls = [url.strip() for url in api_rpc.split(",") if url.strip()]
            return self._reject_insecure_rpc_urls(urls)

        return []

    async def _process_environment_config(self, env):
        env_type = env.get("env_type") or env.get("type")
        rpc = env.get("rpc") or env.get("chain_instance")

        if env_type == "mainnet":
            await self._process_connected_chain_config(env, rpc)
        elif env_type == "subnet":
            await self._process_subnet_config(env, rpc)

    async def _process_connected_chain_config(self, env, rpc):
        """Process connected-chain environment configuration."""
        chain_id = env.get("chainid") or env.get("chain_id")
        name = env.get("network") or env.get("chain_display_name")
        native_symbol = env.get("native_token_symbol", "ETH")

        self.chain_config[name] = {
            "chain_id": chain_id,
            "rpc": rpc,
            "explorer": env.get("explorer"),
            "native_symbol": native_symbol,
        }

        # Get RPC URLs: API response (can be comma-separated) + env var override
        rpc_urls = self._get_rpc_urls(chain_id, native_symbol, rpc)

        await self._setup_connected_chain_provider(name, rpc_urls)

        if (name == "Avalanche" or name == "Fuji") and rpc_urls:
            await self._setup_avalanche_fuji_provider(name, chain_id, rpc_urls)

    async def _setup_connected_chain_provider(self, name, rpc_urls):
        """Set up a connected-chain provider with failover support."""
        if not rpc_urls:
            return

        if self._provider_manager:
            await self._provider_manager.add_providers(name, rpc_urls)
            primary_provider = await self._provider_manager.get_provider(name)
            if primary_provider:
                self.connected_chain_providers[name] = primary_provider
            else:
                fallback_provider = self._create_provider_fallback(rpc_urls[0], name)
                if fallback_provider:
                    self.connected_chain_providers[name] = fallback_provider
        else:
            fallback_provider = self._create_provider_fallback(rpc_urls[0], name)
            if fallback_provider:
                self.connected_chain_providers[name] = fallback_provider

    async def _setup_avalanche_fuji_provider(self, name, chain_id, rpc_urls):
        """Set up Avalanche or Fuji connected-chain provider."""
        if self._provider_manager:
            self.w3_connected_chain = await self._provider_manager.get_provider(name)
            if not self.w3_connected_chain:
                self.w3_connected_chain = self.connected_chain_providers.get(name)
        else:
            self.w3_connected_chain = self.connected_chain_providers.get(name)
        self.chain_id = chain_id

    async def _process_subnet_config(self, env, rpc):
        """Process subnet environment configuration."""
        chain_id = env.get("chainid") or env.get("chain_id")
        native_symbol = env.get("native_token_symbol", "ALOT")

        rpc_urls = self._get_rpc_urls(chain_id, native_symbol, rpc)
        if not rpc_urls:
            return

        if self._provider_manager:
            await self._provider_manager.add_providers("DEXALOT_L1", rpc_urls)
            self.w3_l1 = await self._provider_manager.get_provider("DEXALOT_L1")
            if not self.w3_l1:
                self.w3_l1 = self._create_provider_fallback(rpc_urls[0], "DEXALOT_L1")
        else:
            self.w3_l1 = self._create_provider_fallback(rpc_urls[0], "DEXALOT_L1")

    def _create_provider_fallback(self, rpc_url: str, chain_name: str) -> "AsyncWeb3 | None":
        """Create a provider directly as fallback when provider manager fails."""
        try:
            self._reject_insecure_rpc_urls([rpc_url])  # defence-in-depth
            return AsyncWeb3(AsyncHTTPProvider(rpc_url))
        except ValueError:
            raise  # insecure-URL errors must propagate, not be swallowed
        except Exception as e:
            log_event(
                self.logger,
                "warning",
                "provider_init_failed",
                chain=chain_name,
                error=str(e),
            )
            return None

    async def _fetch_tokens(self):
        token_url = f"{self.api_base_url}{ENDPOINT_TRADING_TOKENS}"
        async with await self._make_http_request("get", token_url) as response:
            response.raise_for_status()
            tokens = await response.json()
        for t in tokens:
            if t["symbol"] not in self.token_data:
                self.token_data[t["symbol"]] = {}
            self.token_data[t["symbol"]][t["env"]] = t

    async def _fetch_rfq_pairs(self):
        rfq_url = f"{self.api_base_url}{ENDPOINT_RFQ_PAIRS}"
        self.rfq_pairs = {}
        fetch_tasks = []

        async def fetch_one(cid, chain_name):
            try:
                async with await self._make_http_request(
                    "get", rfq_url, params={"chainid": cid}
                ) as response:
                    response.raise_for_status()
                    self.rfq_pairs[cid] = await response.json()
            except Exception as e:
                log_event(
                    self.logger,
                    "warning",
                    "rfq_pairs_fetch_failed",
                    chain_id=cid,
                    chain_name=chain_name,
                    error=str(e),
                )

        for chain_name, config in self.chain_config.items():
            cid = config.get("chain_id")
            if cid:
                fetch_tasks.append(fetch_one(cid, chain_name))

        if fetch_tasks:
            await asyncio.gather(*fetch_tasks)

        if self.CHAIN_ID_AVAX_MAINNET not in self.rfq_pairs:
            try:
                async with await self._make_http_request(
                    "get", rfq_url, params={"chainid": self.CHAIN_ID_AVAX_MAINNET}
                ) as response:
                    if response.status == 200:
                        self.rfq_pairs[self.CHAIN_ID_AVAX_MAINNET] = await response.json()
            except Exception:
                pass

    async def _fetch_deployments(self):
        deploy_url = f"{self.api_base_url}{ENDPOINT_TRADING_DEPLOYMENT}"

        await asyncio.gather(
            self._fetch_contract_deployment(deploy_url, "TradePairs"),
            self._fetch_contract_deployment(deploy_url, "Portfolio"),
            self._fetch_contract_deployment(deploy_url, "MainnetRFQ"),
        )

    async def _fetch_clob_pairs(self):
        """Fetch CLOB pairs if get_clob_pairs method is available.

        This is a wrapper around get_clob_pairs() to make it consistent
        with other _fetch_* methods that raise exceptions on error.
        """
        if hasattr(self, "get_clob_pairs"):
            result = await self.get_clob_pairs()
            if not result.success:
                raise Exception(f"Failed to fetch CLOB pairs: {result.error}")

    async def reinitialize(self, force_refresh: bool = False) -> Result[str]:
        """Refresh all configuration data loaded during ``initialize_client()``.

        Use this in long-running processes when trading pairs, token metadata, or
        contract deployments may have changed.  Refreshes:

        - Environments (``chain_config``, Web3 providers, ``chain_id``, ``subnet_chain_id``)
        - Tokens (``token_data``)
        - RFQ pairs (``rfq_pairs``)
        - Deployments (``deployments``, contract instances)
        - CLOB pairs (``pairs``)

        Args:
            force_refresh: If ``True``, clears the static and semi-static caches
                before fetching, guaranteeing fresh data regardless of TTL.
                Defaults to ``False``.

        Returns:
            Result containing ``"Client reinitialized with all configurations."`` on
            success, or an error message on failure.
        """
        await self.connect()
        with track_operation(self.logger, "reinitialize", parent_env=self.parent_env):
            try:
                # Clear caches if force_refresh is requested
                if force_refresh:
                    self.invalidate_cache(level="static")
                    self.invalidate_cache(level="semi_static")

                # Fetch environments first (sets w3_l1, w3_connected_chain needed for deployments)
                await self._fetch_environments()
                # Then fetch other data in parallel
                await asyncio.gather(
                    self._fetch_tokens(),
                    self._fetch_rfq_pairs(),
                    self._fetch_deployments(),
                    self._fetch_clob_pairs(),
                )
                return Result.ok("Client reinitialized with all configurations.")
            except Exception as e:
                error_msg = self._sanitize_error(e, "reinitializing client")
                return Result.fail(error_msg)

    async def _fetch_contract_deployment(self, deploy_url, contract_type):
        async with await self._make_http_request(
            "get", deploy_url, params={"contracttype": contract_type, "returnabi": "true"}
        ) as response:
            response.raise_for_status()
            data = await response.json()
            for item in data:
                # Transform deployment item to standardized field names
                transformed = self._transform_deployment_from_api(item)
                self._process_deployment_item(transformed, contract_type)

    def _process_deployment_item(self, item: dict[str, Any], contract_type: str) -> None:
        env = item.get("env")
        abi_data = item.get("abi")
        if abi_data:
            if isinstance(abi_data, dict) and "abi" in abi_data:
                abi = abi_data["abi"]
            else:
                abi = abi_data
        else:
            abi = []
        address = item.get("address")

        if contract_type == "TradePairs":
            if env in [self.ENV_PROD_MULTI_SUBNET, self.ENV_FUJI_MULTI_SUBNET]:
                self.deployments["TradePairs"] = {"address": address, "abi": abi}
                if self.w3_l1:
                    self.trade_pairs_contract = self.w3_l1.eth.contract(address=address, abi=abi)

        elif contract_type == "Portfolio":
            if env in [self.ENV_PROD_MULTI_SUBNET, self.ENV_FUJI_MULTI_SUBNET]:
                self.deployments["PortfolioSub"] = {"address": address, "abi": abi}
                if self.w3_l1:
                    self.portfolio_sub_contract = self.w3_l1.eth.contract(address=address, abi=abi)
            elif env in [self.ENV_PROD_MULTI_AVAX, self.ENV_FUJI_MULTI_AVAX]:
                if "PortfolioMain" not in self.deployments:
                    self.deployments["PortfolioMain"] = {}
                self.deployments["PortfolioMain"]["Avalanche"] = {"address": address, "abi": abi}
                if self.w3_connected_chain:
                    self.portfolio_main_avax_contract = self.w3_connected_chain.eth.contract(
                        address=address, abi=abi
                    )

        elif contract_type == "MainnetRFQ":
            if env in [self.ENV_PROD_MULTI_AVAX, self.ENV_FUJI_MULTI_AVAX]:
                self.deployments["MainnetRFQ"]["Avalanche"] = {"address": address, "abi": abi}

    def _transform_deployment_from_api(self, item: dict) -> dict:
        """Transform API deployment response to match standardized field names.

        Maps lowercase/camelCase API fields to lowercase SDK fields to match
        Python naming conventions.

        Args:
            item: Raw deployment dict from API response

        Returns:
            Transformed deployment dict with standardized field names
        """
        transformed = dict(item)  # Start with all original fields

        # Map env: prefer existing lowercase, fallback to variations
        if "env" not in transformed:
            if "Env" in item:
                transformed["env"] = item["Env"]
            elif "environment" in item:
                transformed["env"] = item["environment"]

        # Map address: prefer existing lowercase, fallback to variations
        if "address" not in transformed:
            if "Address" in item:
                transformed["address"] = item["Address"]
            elif "contractAddress" in item:
                transformed["address"] = item["contractAddress"]

        # Map abi: prefer existing lowercase, fallback to variations
        # Note: ABI can be an array or nested object, so we preserve the structure
        if "abi" not in transformed:
            if "Abi" in item:
                transformed["abi"] = item["Abi"]
            elif "ABI" in item:
                transformed["abi"] = item["ABI"]

        return transformed

    def _transform_environment_from_api(self, env: dict) -> dict:
        """Transform API environment response to match standardized field names.

        Maps lowercase/snake_case API fields to snake_case SDK fields to match
        Python naming conventions.

        Args:
            env: Raw environment dict from API response

        Returns:
            Transformed environment dict with standardized field names
        """
        transformed = dict(env)  # Start with all original fields

        # Map chainId: prefer existing snake_case, fallback to lowercase
        if "chain_id" not in transformed:
            if "chainid" in env:
                transformed["chain_id"] = env["chainid"]
            elif "chainId" in env:
                transformed["chain_id"] = env["chainId"]

        # Map envType: prefer existing snake_case, fallback to type/lowercase
        if "env_type" not in transformed:
            if "type" in env:
                transformed["env_type"] = env["type"]
            elif "envType" in env:
                transformed["env_type"] = env["envType"]

        # Map rpc: prefer existing, fallback to chain_instance
        if "rpc" not in transformed and "chain_instance" in env:
            transformed["rpc"] = env["chain_instance"]

        # Map network: prefer existing, fallback to chain_display_name
        if "network" not in transformed and "chain_display_name" in env:
            transformed["network"] = env["chain_display_name"]

        return transformed

    @async_ttl_cached(_STATIC_CACHE)
    async def get_environments(self) -> Result[list]:
        """Fetch the list of Dexalot trading environments from the API.

        Each environment entry describes one blockchain network (subnet or connected chain)
        including its chain ID, RPC endpoint, and environment type.  Results are
        normalised to snake_case field names before returning.

        Note:
            Cached for 1 hour (static cache tier).

        Returns:
            Result containing a list of environment dicts on success, or an error
            message on failure.
        """
        if not self._cache_enabled:
            # Bypass cache by clearing it for this call
            key: tuple[Any, ...] = ("get_environments", (self,), frozenset())
            _STATIC_CACHE._store.pop(key, None)

        try:
            async with await self._make_http_request(
                "get", f"{self.api_base_url}{ENDPOINT_TRADING_ENVIRONMENTS}"
            ) as response:
                response.raise_for_status()
                data = await response.json()
                # Transform environments to standardized field names
                transformed = [self._transform_environment_from_api(env) for env in data]
                await self._apply_environment_state(transformed)
                return Result.ok(transformed)
        except Exception as e:
            error_msg = self._sanitize_error(e, "getting environments")
            return Result.fail(error_msg)

    @async_ttl_cached(_STATIC_CACHE)
    async def get_chains(self) -> Result[dict]:
        """Return a mapping of connected chain IDs to their display names.

        Note:
            Cached for 1 hour (static cache tier).

        Returns:
            Result containing ``{chain_id: chain_name}`` on success, or an error
            message on failure.

        Example:
            >>> result = await client.get_chains()
            >>> # {43114: "Avalanche", 1: "Ethereum", ...}
        """
        if not self._cache_enabled:
            # Bypass cache by clearing it for this call
            key: tuple[Any, ...] = ("get_chains", (self,), frozenset())
            _STATIC_CACHE._store.pop(key, None)

        try:
            # Fetch environments to get chain_config (cache decorator handles TTL)
            envs_result = await self.get_environments()
            if not envs_result.success:
                return Result.fail(f"Failed to fetch environments: {envs_result.error}")

            chains = {}
            for env in envs_result.data or []:
                cid = env.get("chain_id")
                name = env.get("network")
                if cid:
                    chains[cid] = name
            return Result.ok(chains)
        except Exception as e:
            error_msg = self._sanitize_error(e, "getting chains")
            return Result.fail(error_msg)

    def _transform_token_from_api(self, token: dict) -> dict:
        """Transform API token response to match standardized field names.

        Maps lowercase/snake_case API fields to snake_case SDK fields to match
        Python naming conventions.

        Args:
            token: Raw token dict from API response

        Returns:
            Transformed token dict with standardized field names
        """
        transformed = dict(token)  # Start with all original fields

        # Map evmDecimals: prefer existing snake_case, fallback to lowercase/decimals
        if "evm_decimals" not in transformed:
            if "evmdecimals" in token:
                transformed["evm_decimals"] = token["evmdecimals"]
            elif "decimals" in token:
                transformed["evm_decimals"] = token["decimals"]
            elif "evmDecimals" in token:
                transformed["evm_decimals"] = token["evmDecimals"]

        # Map chainId: prefer existing snake_case, fallback to lowercase
        if "chain_id" not in transformed:
            if "chainid" in token:
                transformed["chain_id"] = token["chainid"]
            elif "chainId" in token:
                transformed["chain_id"] = token["chainId"]

        # Map network: prefer existing, fallback to chain_display_name
        if "network" not in transformed and "chain_display_name" in token:
            transformed["network"] = token["chain_display_name"]

        return transformed

    @async_ttl_cached(_SEMI_STATIC_CACHE)
    async def get_tokens(self) -> Result[list]:
        """Fetch the list of tokens available on Dexalot (connected-chain tokens only).

        Returns one entry per unique token symbol, keyed to a connected chain.
        Dexalot L1 does not allow ERC20 deployments, so only connected-chain token
        addresses are included.  Results are normalised to snake_case fields.

        Note:
            Cached for 15 minutes (semi-static cache tier).

        Returns:
            Result containing a list of token dicts (``symbol``, ``name``,
            ``decimals``, ``address``, ``chain``, ``chain_id``) on success, or an
            error message on failure.
        """
        if not self._cache_enabled:
            # Bypass cache by clearing it for this call
            key: tuple[Any, ...] = ("get_tokens", (self,), frozenset())
            _SEMI_STATIC_CACHE._store.pop(key, None)

        try:
            # Get connected-chain IDs - fetch environments if chain_config is not available
            connected_chain_ids = set()
            if not self.chain_config:
                # Fetch environments to get chain_config
                envs_result = await self.get_environments()
                if not envs_result.success:
                    return Result.fail(f"Failed to fetch environments: {envs_result.error}")

            for config in self.chain_config.values():
                chain_id = config.get("chain_id")
                if chain_id:
                    connected_chain_ids.add(chain_id)

            # Always fetch fresh from API (cache decorator handles TTL)
            async with await self._make_http_request(
                "get", f"{self.api_base_url}{ENDPOINT_TRADING_TOKENS}"
            ) as response:
                response.raise_for_status()
                tokens = await response.json()

            # Transform tokens to standardized field names
            transformed_tokens = [self._transform_token_from_api(token) for token in tokens]

            unique_tokens = []
            seen_symbols = set()
            for token in transformed_tokens:
                symbol = token.get("symbol")
                chain_id = token.get("chain_id")

                if symbol and symbol not in seen_symbols and chain_id:
                    # Only include connected-chain tokens (not L1 subnet tokens)
                    if chain_id in connected_chain_ids:
                        unique_tokens.append(
                            {
                                "symbol": symbol,
                                "name": token.get("name", symbol),
                                "decimals": token.get("evm_decimals") or token.get("decimals", 18),
                                "address": token.get("address"),
                                "chain": token.get("network")
                                or token.get("chain_display_name", ""),
                                "chain_id": chain_id,
                            }
                        )
                        seen_symbols.add(symbol)
            return Result.ok(unique_tokens)
        except Exception as e:
            error_msg = self._sanitize_error(e, "getting tokens")
            return Result.fail(error_msg)

    @async_ttl_cached(_STATIC_CACHE)
    async def get_deployment(self) -> Result[dict]:
        """Fetch contract deployment configuration (addresses and ABIs) from the API.

        Populates and returns ``self.deployments`` with entries for
        ``TradePairs``, ``PortfolioMain``, ``PortfolioSub``, and ``MainnetRFQ``.
        Also wires up on-chain contract instances (``trade_pairs_contract``,
        ``portfolio_sub_contract``, ``portfolio_main_avax_contract``).

        Note:
            Cached for 1 hour (static cache tier).

        Returns:
            Result containing the ``deployments`` dictionary on success, or an
            error message on failure.
        """
        if not self._cache_enabled:
            # Bypass cache by clearing it for this call
            key: tuple[Any, ...] = ("get_deployment", (self,), frozenset())
            _STATIC_CACHE._store.pop(key, None)

        try:
            # Ensure environments are fetched first (needed for w3_l1, w3_connected_chain)
            if not self.chain_config:
                envs_result = await self.get_environments()
                if not envs_result.success:
                    return Result.fail(f"Failed to fetch environments: {envs_result.error}")

            # Always fetch fresh from API (cache decorator handles TTL)
            # Rebuild deployments dict from API
            deploy_url = f"{self.api_base_url}{ENDPOINT_TRADING_DEPLOYMENT}"

            # Initialize deployments structure if not already initialized
            if not self.deployments:
                self.deployments = {
                    "TradePairs": {},
                    "PortfolioMain": {},
                    "PortfolioSub": {},
                    "MainnetRFQ": {},
                }
            else:
                # Clear existing deployments to rebuild fresh
                self.deployments = {
                    "TradePairs": {},
                    "PortfolioMain": {},
                    "PortfolioSub": {},
                    "MainnetRFQ": {},
                }

            # Fetch all contract types in parallel
            await asyncio.gather(
                self._fetch_contract_deployment(deploy_url, "TradePairs"),
                self._fetch_contract_deployment(deploy_url, "Portfolio"),
                self._fetch_contract_deployment(deploy_url, "MainnetRFQ"),
            )

            return Result.ok(self.deployments)
        except Exception as e:
            error_msg = self._sanitize_error(e, "getting deployment")
            return Result.fail(error_msg)

    def _apply_deployment_state(self, deployments: dict[str, Any]) -> None:
        """Restore deployment mappings and contract handles from cached data."""
        self.deployments = copy.deepcopy(deployments)
        if self.w3_l1 and self.deployments.get("TradePairs"):
            trade_pairs = self.deployments["TradePairs"]
            if trade_pairs.get("address") and trade_pairs.get("abi") is not None:
                self.trade_pairs_contract = self.w3_l1.eth.contract(
                    address=trade_pairs["address"], abi=trade_pairs["abi"]
                )
        if self.w3_l1 and self.deployments.get("PortfolioSub"):
            portfolio_sub = self.deployments["PortfolioSub"]
            if portfolio_sub.get("address") and portfolio_sub.get("abi") is not None:
                self.portfolio_sub_contract = self.w3_l1.eth.contract(
                    address=portfolio_sub["address"], abi=portfolio_sub["abi"]
                )
        if self.w3_connected_chain and self.deployments.get("PortfolioMain", {}).get("Avalanche"):
            portfolio_main = self.deployments["PortfolioMain"]["Avalanche"]
            if portfolio_main.get("address") and portfolio_main.get("abi") is not None:
                self.portfolio_main_avax_contract = self.w3_connected_chain.eth.contract(
                    address=portfolio_main["address"], abi=portfolio_main["abi"]
                )

    async def _rehydrate_cached_get_deployment(self, cached: Result[dict]) -> None:
        """Restore deployment state when ``get_deployment`` is served from cache."""
        if not cached.success or cached.data is None:
            return
        if not self.chain_config or not self.w3_l1:
            envs_result = await self.get_environments()
            if not envs_result.success:
                return
        self._apply_deployment_state(cached.data)
