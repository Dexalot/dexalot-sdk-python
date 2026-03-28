from typing import Any

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
        enable_cache=None,
        cache_ttl_static=None,
        cache_ttl_semi_static=None,
        cache_ttl_balance=None,
        cache_ttl_orderbook=None,
        config=None,
        **kwargs,
    ):
        """Initialize DexalotClient with optional cache configuration.

        Args:
            signer: Optional web3 Account for signing transactions.
            parent_env: Optional environment override (e.g., 'fuji-multi').
            enable_cache: Override cache enablement. If ``None``, use env / config defaults.
            cache_ttl_static: Override static-cache TTL in seconds.
            cache_ttl_semi_static: Override semi-static-cache TTL in seconds.
            cache_ttl_balance: Override balance-cache TTL in seconds.
            cache_ttl_orderbook: Override orderbook-cache TTL in seconds.
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
    def unit_conversion(amount: float, decimals: int, to_base: bool = True) -> Any:
        """Convert between human-readable and atomic (wei-like) units.

        Args:
            amount: The value to convert.
            decimals: Number of decimal places for the token (e.g. 18 for AVAX, 6 for USDC).
            to_base: If True, multiply by 10**decimals (human → atomic).
                     If False, divide by 10**decimals (atomic → human).

        Returns:
            Converted amount as a float.

        Example:
            >>> DexalotClient.unit_conversion(1.5, 18)       # → 1500000000000000000
            >>> DexalotClient.unit_conversion(1_000_000, 6, to_base=False)  # → 1.0
        """
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

    def get_revert_reason(self, error_msg: Any) -> str:
        """Parse a contract revert error message into a human-readable description.

        Looks up the error code in the loaded ``errors.json`` table and returns
        a descriptive message when a match is found. Returns the original message
        unchanged if no code matches.

        Args:
            error_msg: Raw error string from a failed transaction, typically
                       containing a 4-byte error selector or named error code.

        Returns:
            Human-readable error description, or the original ``error_msg`` if
            no matching code is found.
        """
        return str(self._parse_revert_reason(error_msg))
