from ..utils import Utils
from ..utils.observability import configure_logging
from .clob import CLOBClient
from .swap import SwapClient
from .transfer import TransferClient


class DexalotClient(CLOBClient, SwapClient, TransferClient):
    """
    Main Dexalot Client class.
    Inherits functionality from modular components:
    - DexalotBaseClient (via mixins): Initialization, Auth, Web3
    - CLOBClient: Order Book, Trading
    - SwapClient: RFQ, SimpleSwap
    - TransferClient: Balances, Deposits, Withdrawals
    """

    def __init__(
        self,
        signer=None,
        parent_env=None,
        enable_cache=True,
        cache_ttl_static=3600,
        cache_ttl_semi_static=900,
        cache_ttl_balance=10,
        cache_ttl_orderbook=1,
        config=None,
        **kwargs,
    ):
        """Initialize DexalotClient with optional cache configuration.

        Args:
            signer: Optional web3 Account for signing transactions.
            parent_env: Optional environment override (e.g., 'fuji-multi').
            enable_cache: Enable caching for read operations (default: True).
            cache_ttl_static: TTL in seconds for static data (default: 3600).
            cache_ttl_semi_static: TTL in seconds for semi-static data (default: 900).
            cache_ttl_balance: TTL in seconds for balance data (default: 10).
            cache_ttl_orderbook: TTL in seconds for orderbook data (default: 1).
            config: Optional DexalotConfig object.
        """
        super().__init__(
            signer=signer,
            parent_env=parent_env,
            enable_cache=enable_cache,
            cache_ttl_static=cache_ttl_static,
            cache_ttl_semi_static=cache_ttl_semi_static,
            cache_ttl_balance=cache_ttl_balance,
            cache_ttl_orderbook=cache_ttl_orderbook,
            config=config,
            **kwargs,
        )
        self._ws_manager = None

    @staticmethod
    def unit_conversion(amount, decimals, to_base=True):
        return Utils.unit_conversion(amount, decimals, to_base)

    @staticmethod
    def configure_logging(log_level: str | None = None, log_format: str = "console"):
        """
        Configure SDK logging.

        Args:
            log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                       If None, uses DEXALOT_LOG_LEVEL env var or defaults to INFO.
            log_format: Output format - "json" or "console".
                        If not specified, uses DEXALOT_LOG_FORMAT env var or defaults to "console".

        Example:
            >>> DexalotClient.configure_logging(log_level="DEBUG", log_format="json")
        """
        configure_logging(log_level=log_level, log_format=log_format)

    def get_revert_reason(self, error_msg):
        """Expose internal revert reason parser."""
        return self._parse_revert_reason(error_msg)
