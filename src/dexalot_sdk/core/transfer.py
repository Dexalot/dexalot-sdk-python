import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from ..constants import (
    BRIDGE_ID_ICM,
    BRIDGE_ID_LZ,
    ENDPOINT_INFO_HOURLY_PRICE_HISTORY,
    ENDPOINT_INFO_PRICE_HISTORY,
    ENDPOINT_INFO_USD_PRICES,
    ENDPOINT_TRADING_COMBINED_TRANSFERS,
    ENDPOINT_TRADING_TOKENS,
    GAS_BUFFER,
    ICM_CHAINS,
)
from ..utils import Utils
from ..utils.cache import async_ttl_cached
from ..utils.input_validators import (
    validate_address,
    validate_positive_float,
    validate_token_symbol,
    validate_transfer_params,
)
from ..utils.observability import track_method
from ..utils.result import Result
from .base import _BALANCE_CACHE, _SEMI_STATIC_CACHE, _STATIC_CACHE, DexalotBaseClient

# ---------------------------------------------------------------------------
# Shared types returned by transfer client methods
# ---------------------------------------------------------------------------
# Kept in this module (next to the methods that produce them) to follow the
# existing SDK convention — e.g. ``ResolvedChain`` lives next to the chain
# resolver in ``base.py``.

TransferStatus = Literal["COMPLETED", "INFLIGHT", "DELAYED"]

TransferActionType = Literal[
    "WITHDRAWN",
    "DEPOSITED",
    "SENT",
    "RECEIVED",
    "RECOVERED",
    "ADD_GAS",
    "REMOVE_GAS",
    "AUTO_FILL",
    "WITHDRAW_PENDING",
    "DEPOSIT_PENDING",
]

TransferBridge = Literal["NATIVE", "LAYER0", "CELER", "ICM"]


@dataclass(frozen=True)
class PricePoint:
    """One USD price observation in a price-history series.

    Returned by :meth:`TransferClient.get_token_price_history` (daily) and
    :meth:`TransferClient.get_token_hourly_price_history` (hourly).  The
    backend ships rows as ``{date: ISO-8601-string, price: stringified-decimal}``
    sorted descending by date; the SDK normalizes to ascending unix-seconds
    + numeric price so callers can chart, filter, or interpolate without
    re-parsing.
    """

    timestamp: int  # unix seconds (UTC)
    price: float


@dataclass(frozen=True)
class Transfer:
    """One row of unified transfer history returned by ``get_combined_transfers``.

    Each row aggregates a deposit / withdrawal / gas top-up / portfolio
    P2P send-or-receive / bridge recovery involving the connected wallet.
    The backend ships rows as ``DBTransfer`` (snake_case + numeric enums);
    the SDK lifts the numeric ``status`` / ``action_type`` / ``bridge``
    enums to human-readable strings and parses ``quantity`` / ``fee`` Big
    decimal strings to ``float``.  ``quantity`` / ``fee`` are already
    display-decimal at the backend — there is no wei → human conversion
    to apply.
    """

    action_type: TransferActionType
    status: TransferStatus
    symbol: str
    quantity: float
    fee: float
    trader_address: str
    bridge: TransferBridge
    bridge_url: str
    nonce: int
    source_env: str
    source_chain_id: int
    source_tx: str
    source_ts: int
    # ``target_*`` is ``None`` for non-crossing transfers (no target leg).
    target_env: str | None
    target_chain_id: int | None
    target_tx: str | None
    target_ts: int | None


# Numeric enum → human-readable label lookup tables for the
# ``transferscombined`` REST response. Mirror the
# ``TRANSFER_ACTION_TYPE`` / ``TRANSFER_STATUS`` / ``BRIDGES`` enums
# the official Dexalot frontend uses to render the same rows; keeping
# them in lockstep avoids drift if the backend ever adds new variants.
_TRANSFER_ACTION_TYPE_LABELS: dict[int, TransferActionType] = {
    0: "WITHDRAWN",
    1: "DEPOSITED",
    5: "SENT",
    6: "RECEIVED",
    7: "RECOVERED",
    8: "ADD_GAS",
    9: "REMOVE_GAS",
    10: "AUTO_FILL",
    11: "WITHDRAW_PENDING",
    12: "DEPOSIT_PENDING",
}

_TRANSFER_STATUS_LABELS: dict[int, TransferStatus] = {
    0: "COMPLETED",
    1: "INFLIGHT",
    2: "DELAYED",
}

_TRANSFER_BRIDGE_LABELS: dict[int, TransferBridge] = {
    -1: "NATIVE",
    0: "LAYER0",
    1: "CELER",
    2: "ICM",
}


class TransferClient(DexalotBaseClient):
    async def _get_provider_for_chain(self, chain: str):
        """
        Get provider for a chain, using provider manager if enabled.

        Args:
            chain: Chain name

        Returns:
            AsyncWeb3 provider or None if not found
        """
        if self._provider_manager:
            provider = await self._provider_manager.get_provider(chain)
            if provider:
                return provider
        # Fallback to connected_chain_providers if provider manager is disabled or returns None.
        return self.connected_chain_providers.get(chain)

    def _get_available_chains(self) -> list[str]:
        """
        Get list of available chains.

        Returns:
            List of chain names
        """
        # Always include chains from connected_chain_providers
        chains = set(self.connected_chain_providers.keys())

        if self._provider_manager:
            # Also include chains from provider manager
            for chain_name in self.chain_config.keys():
                if self._provider_manager.get_provider_count(chain_name) > 0:
                    chains.add(chain_name)

        return list(chains)

    @async_ttl_cached(_SEMI_STATIC_CACHE)
    @track_method("transfer")
    async def get_token_details(self, token: str) -> Result[dict]:
        """Fetch metadata for a specific token across all supported environments.

        Note:
            Cached for 15 minutes (semi-static cache tier).

        Args:
            token: Token symbol (e.g. ``"USDC"`` or ``"AVAX"``).

        Returns:
            Result containing a dict keyed by environment name, where each value
            is a token info dict with ``address``, ``decimals``, ``chain_id``,
            etc.  Returns an error message if the token is not found.
        """
        if not self._cache_enabled:
            # Bypass cache by clearing it for this call
            key: tuple[Any, ...] = ("get_token_details", (self, token), frozenset())
            _SEMI_STATIC_CACHE._store.pop(key, None)

        try:
            # Validate token symbol
            validate_result = validate_token_symbol(token, "token")
            if not validate_result.success:
                return cast(Result[dict[Any, Any]], validate_result)

            token = self._normalize_user_token(token)

            # Always fetch token data from API (cache decorator handles TTL)
            token_url = f"{self.api_base_url}{ENDPOINT_TRADING_TOKENS}"
            async with await self._make_http_request("get", token_url) as response:
                response.raise_for_status()
                tokens = await response.json()

            # Build token_data structure from API response
            token_data: dict[str, Any] = {}
            for t in tokens:
                if t["symbol"] not in token_data:
                    token_data[t["symbol"]] = {}
                token_data[t["symbol"]][t["env"]] = t

            self.token_data = token_data

            if token in token_data:
                return Result.ok(token_data[token])
            return Result.fail(f"Token {token} not found.")
        except Exception as e:
            error_msg = self._sanitize_error(e, "getting token details")
            return Result.fail(error_msg)

    def _rehydrate_cached_get_token_details(self, cached: Result[dict], token: str) -> None:
        """Restore ``token_data`` when token details are served from cache."""
        if not cached.success or cached.data is None:
            return
        if not self.token_data:
            self.token_data = {}
        self.token_data[token] = cached.data

    @track_method("transfer")
    async def get_chain_wallet_balance(
        self, chain: str, token: str, address: str | None = None
    ) -> Result[dict]:
        """Get the balance of a specific token on a specific chain wallet.

        Note:
            Cached for 10 seconds (balance cache tier).

        Args:
            chain: Chain display name, e.g. ``"Avalanche"``, ``"Fuji"``, or
                ``"Dexalot L1"`` (for native ALOT balance).
            token: Token symbol, e.g. ``"AVAX"`` or ``"USDC"``.
            address: Wallet address to query.  Defaults to the current account
                address if a signer is configured.

        Returns:
            Result containing a balance dict with ``chain``, ``symbol``,
            ``balance``, and ``type`` fields on success, or an error message
            on failure.
        """
        if not isinstance(chain, str) or not chain.strip():
            return Result.fail(
                f"Invalid chain: must be non-empty string, got {type(chain).__name__}"
            )

        resolved_chain = self.resolve_chain_reference(chain, include_dexalot_l1=True)
        if not resolved_chain.success or resolved_chain.data is None:
            return Result.fail(resolved_chain.error or f"Could not resolve chain '{chain}'.")

        resolved_address = address or (
            cast(str, cast(Any, self.account).address) if self.account else None
        )
        return cast(
            Result[dict[Any, Any]],
            await self._get_chain_wallet_balance_cached(
                resolved_chain.data.canonical_name, token, resolved_address
            ),
        )

    @async_ttl_cached(_BALANCE_CACHE)
    async def _get_chain_wallet_balance_cached(
        self, chain: str, token: str, query_address: str | None
    ) -> Result[dict]:
        """Internal cached implementation of get_chain_wallet_balance."""
        if not query_address:
            return Result.fail("Address required (pass as param or set signer)")

        # Validate address format
        address_result = validate_address(query_address, "address")
        if not address_result.success:
            return cast(Result[dict[Any, Any]], address_result)

        # Validate token symbol
        token_result = validate_token_symbol(token, "token")
        if not token_result.success:
            return cast(Result[dict[Any, Any]], token_result)

        token = self._normalize_user_token(token)

        # Validate chain exists
        if not isinstance(chain, str) or not chain.strip():
            return Result.fail(
                f"Invalid chain: must be non-empty string, got {type(chain).__name__}"
            )

        # Check Dexalot L1
        if chain == "Dexalot L1":
            if token != "ALOT":
                return Result.fail(
                    f"Token {token} not available on Dexalot L1. Only ALOT (native) exists."
                )
            balance = await self._get_l1_native_balance(query_address)
            return Result.ok(balance)

        # Check connected chain
        w3_provider = await self._get_provider_for_chain(chain)
        if not w3_provider:
            available = ["Dexalot L1"] + self._get_available_chains()
            return Result.fail(f"Chain '{chain}' not connected. Available: {available}")

        chain_info = self.chain_config.get(chain, {})
        chain_id = chain_info.get("chain_id")
        native_symbol = chain_info.get("native_symbol", "ETH")

        # Native token check
        if token == native_symbol:
            balance = await self._get_native_balance(
                chain, w3_provider, query_address, native_symbol
            )
            return Result.ok(balance)

        # ERC20 token check
        if not chain_id:
            return Result.fail(f"Chain ID not configured for {chain}")

        balance = await self._get_erc20_balance(chain, chain_id, w3_provider, query_address, token)
        if isinstance(balance, dict) and "error" in balance:
            return Result.fail(balance["error"])
        return Result.ok(balance)

    @track_method("transfer")
    async def get_chain_wallet_balances(
        self, chain: str, address: str | None = None
    ) -> Result[dict]:
        """Get all token balances on a specific chain wallet.

        Fetches native token balance plus all ERC20 token balances in parallel.

        Note:
            Cached for 10 seconds (balance cache tier).

        Args:
            chain: Chain display name, e.g. ``"Avalanche"`` or ``"Dexalot L1"``.
            address: Wallet address to query.  Defaults to the current account.

        Returns:
            Result containing a dict with ``address``, ``chain``, and
            ``chain_balances`` (list of token balance entries) on success, or an
            error message on failure.
        """
        if not isinstance(chain, str) or not chain.strip():
            return Result.fail(
                f"Invalid chain: must be non-empty string, got {type(chain).__name__}"
            )

        resolved_chain = self.resolve_chain_reference(chain, include_dexalot_l1=True)
        if not resolved_chain.success or resolved_chain.data is None:
            return Result.fail(resolved_chain.error or f"Could not resolve chain '{chain}'.")

        resolved_address = address or (
            cast(str, cast(Any, self.account).address) if self.account else None
        )
        return cast(
            Result[dict[Any, Any]],
            await self._get_chain_wallet_balances_cached(
                resolved_chain.data.canonical_name, resolved_address
            ),
        )

    @async_ttl_cached(_BALANCE_CACHE)
    async def _get_chain_wallet_balances_cached(
        self, chain: str, query_address: str | None
    ) -> Result[dict]:
        """Internal cached implementation of get_chain_wallet_balances."""
        if not query_address:
            return Result.fail("Address required (pass as param or set signer)")

        info: dict[str, Any] = {
            "address": query_address,
            "chain": chain,
            "chain_balances": [],
        }

        # Check Dexalot L1
        if chain == "Dexalot L1":
            l1_entry = await self._get_l1_native_balance(query_address)
            if "error" not in l1_entry:
                info["chain_balances"].append(l1_entry)
            return Result.ok(info)

        # Check connected chain
        w3_provider = await self._get_provider_for_chain(chain)
        if not w3_provider:
            available = ["Dexalot L1"] + self._get_available_chains()
            return Result.fail(f"Chain '{chain}' not connected. Available: {available}")

        chain_info = self.chain_config.get(chain, {})
        chain_id = chain_info.get("chain_id")
        native_symbol = chain_info.get("native_symbol", "ETH")

        tasks = [self._get_native_balance(chain, w3_provider, query_address, native_symbol)]
        if chain_id:
            tasks.append(
                self._fetch_erc20_balances_list(chain_id, chain, w3_provider, query_address)
            )

        results = await asyncio.gather(*tasks)
        for res in results:
            if isinstance(res, list):
                info["chain_balances"].extend(res)
            elif isinstance(res, dict) and "error" not in res:
                info["chain_balances"].append(res)

        return Result.ok(info)

    @track_method("transfer")
    async def get_chain_token_balances(
        self,
        chain: str,
        address: str | None = None,
        tokens: list[str] | None = None,
    ) -> Result[dict[str, str]]:
        """Get balances for a specific list of tokens on one chain.

        Returns a flat ``token -> balance`` map suitable for downstream
        consumers that index balances by symbol.  Unlike
        ``get_chain_wallet_balances``, the caller specifies exactly which
        tokens to read; unknown tokens produce an aggregated error rather
        than being silently skipped.

        Note:
            Cached for 10 seconds (balance cache tier).  Cache keys are
            order-insensitive over ``tokens`` (the list is sorted and
            deduplicated internally), so ``["AVAX", "USDC"]`` and
            ``["USDC", "AVAX"]`` share a cache slot.

        Args:
            chain: Chain display name, e.g. ``"Avalanche"``, ``"Fuji"``, or
                ``"Dexalot L1"``.
            address: Wallet address to query.  Defaults to the current account.
            tokens: Token symbols to fetch, e.g. ``["AVAX", "USDC"]``.  Must
                be a non-empty list.

        Returns:
            Result containing a flat dict mapping each requested symbol to its
            balance as a decimal string.  Returns an error on validation
            failure, an unknown chain, an unknown token on the chain, or
            backend error.
        """
        if not isinstance(chain, str) or not chain.strip():
            return Result.fail(
                f"Invalid chain: must be non-empty string, got {type(chain).__name__}"
            )

        if not isinstance(tokens, list) or not tokens:
            return Result.fail("tokens must be a non-empty list of token symbols.")

        for tok in tokens:
            tok_result = validate_token_symbol(tok, "tokens")
            if not tok_result.success:
                return cast(Result[dict[str, str]], tok_result)

        resolved_chain = self.resolve_chain_reference(chain, include_dexalot_l1=True)
        if not resolved_chain.success or resolved_chain.data is None:
            return Result.fail(resolved_chain.error or f"Could not resolve chain '{chain}'.")

        resolved_address = address or (
            cast(str, cast(Any, self.account).address) if self.account else None
        )

        normalized = sorted({self._normalize_user_token(t) for t in tokens})

        return cast(
            Result[dict[str, str]],
            await self._get_chain_token_balances_cached(
                resolved_chain.data.canonical_name,
                resolved_address,
                tuple(normalized),
            ),
        )

    @async_ttl_cached(_BALANCE_CACHE)
    async def _get_chain_token_balances_cached(
        self,
        chain: str,
        query_address: str | None,
        tokens: tuple[str, ...],
    ) -> Result[dict[str, str]]:
        """Internal cached implementation of get_chain_token_balances."""
        if not query_address:
            return Result.fail("Address required (pass as param or set signer)")

        address_result = validate_address(query_address, "address")
        if not address_result.success:
            return cast(Result[dict[str, str]], address_result)

        results = await asyncio.gather(
            *(self._get_chain_wallet_balance_cached(chain, tok, query_address) for tok in tokens),
            return_exceptions=True,
        )

        balances: dict[str, str] = {}
        unknown: list[str] = []
        errors: list[str] = []

        for tok, res in zip(tokens, results, strict=True):
            if isinstance(res, Exception):
                errors.append(f"{tok}: {self._sanitize_error(res, 'fetching balance')}")
                continue
            if isinstance(res, BaseException):
                # Non-Exception BaseException (e.g. CancelledError, KeyboardInterrupt)
                # — propagate rather than swallow.
                raise res
            if not res.success or res.data is None:
                err = res.error or "unknown error"
                if "not available" in err or "not found" in err:
                    unknown.append(tok)
                else:
                    errors.append(f"{tok}: {err}")
                continue
            balances[tok] = str(res.data.get("balance", ""))

        if unknown:
            return Result.fail(f"Unknown tokens on chain {chain}: {unknown}")
        if errors:
            return Result.fail("; ".join(errors))

        return Result.ok(balances)

    @track_method("transfer")
    async def get_all_chain_wallet_balances(self, address: str | None = None) -> Result[dict]:
        """Get all token balances across all connected chain wallets.

        Queries Dexalot L1 (native ALOT) and all connected chains
        concurrently, including native and ERC20 token balances.

        Note:
            Cached for 10 seconds (balance cache tier).

        Args:
            address: Wallet address to query.  Defaults to the current account.

        Returns:
            Result containing a dict with ``address`` and ``chain_balances``
            (list of token balance entries from all chains) on success, or an
            error message on failure.
        """
        resolved_address = address or (
            cast(str, cast(Any, self.account).address) if self.account else None
        )
        return cast(
            Result[dict[Any, Any]],
            await self._get_all_chain_wallet_balances_cached(resolved_address),
        )

    @async_ttl_cached(_BALANCE_CACHE)
    async def _get_all_chain_wallet_balances_cached(
        self, query_address: str | None
    ) -> Result[dict]:
        """Internal cached implementation of get_all_chain_wallet_balances."""
        if not query_address:
            return Result.fail("Address required (pass as param or set signer)")

        info: dict[str, Any] = {
            "address": query_address,
            "chain_balances": [],
        }

        tasks = [self._get_l1_native_balance(query_address)]

        # Connected chain network balances
        for name in self._get_available_chains():
            w3_provider = await self._get_provider_for_chain(name)
            if not w3_provider:
                continue

            chain_info = self.chain_config.get(name, {})
            chain_id = chain_info.get("chain_id")
            native_symbol = chain_info.get("native_symbol", "ETH")

            # Native Balance
            tasks.append(self._get_native_balance(name, w3_provider, query_address, native_symbol))

            # Token Balances (ERC20)
            if chain_id:
                tasks.append(
                    self._fetch_erc20_balances_list(chain_id, name, w3_provider, query_address)
                )

        results = await asyncio.gather(*tasks)

        for res in results:
            if isinstance(res, list):
                info["chain_balances"].extend(res)
            elif isinstance(res, dict) and "error" not in res:
                info["chain_balances"].append(res)

        return Result.ok(info)

    async def _get_l1_native_balance(self, address: str):
        """Get ALOT balance on Dexalot L1."""
        entry = {
            "chain": "Dexalot L1",
            "symbol": "ALOT",
            "balance": "0",
            "type": "Native",
        }
        if self.w3_l1:
            try:
                l1_balance = await self.w3_l1.eth.get_balance(cast(Any, address))
                entry["balance"] = str(self.w3_l1.from_wei(l1_balance, "ether"))
            except Exception as e:
                entry["balance"] = f"Error: {self._sanitize_error(e, 'fetching L1 native balance')}"
        else:
            entry["balance"] = "Not connected"
        return entry

    async def _get_native_balance(
        self, chain_name: str, w3_provider, address: str, native_symbol: str
    ):
        """Get native token balance on a connected chain."""
        entry = {
            "chain": chain_name,
            "symbol": native_symbol,
            "balance": "Error",
            "type": "Native",
        }
        try:
            balance_wei = await w3_provider.eth.get_balance(address)
            balance_eth = w3_provider.from_wei(balance_wei, "ether")
            entry["balance"] = str(balance_eth)
        except Exception as e:
            entry["balance"] = f"Error: {self._sanitize_error(e, 'fetching native balance')}"
        return entry

    async def _get_erc20_balance(
        self, chain_name: str, chain_id: int, w3_provider, address: str, token: str
    ) -> dict:
        """Get a specific ERC20 token balance on a chain.

        Returns:
            Dict with balance information (not a Result, used internally)
        """
        erc20_abi = [
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function",
            },
        ]

        if token not in self.token_data:
            return {"error": f"Token {token} not found in token data."}

        token_info = None
        for _env_key, data in self.token_data[token].items():
            if data.get("chain_id") == chain_id:
                token_info = data
                break

        if not token_info or not token_info.get("address"):
            return {"error": f"Token {token} not available on chain {chain_name}."}

        token_address = token_info.get("address")
        if token_address == "0x0000000000000000000000000000000000000000":
            return {"error": f"Token {token} has zero address on chain {chain_name}."}

        entry = {
            "chain": chain_name,
            "symbol": token,
            "balance": "Error",
            "address": token_address,
            "type": "ERC20",
        }

        try:
            contract = w3_provider.eth.contract(address=token_address, abi=erc20_abi)
            balance_wei = await contract.functions.balanceOf(address).call()
            decimals = token_info.get("evmdecimals", 18)
            balance_fmt = Utils.unit_conversion(balance_wei, decimals, to_base=False)
            entry["balance"] = str(balance_fmt)
        except Exception as e:
            entry["balance"] = f"Error: {self._sanitize_error(e, 'fetching ERC20 balance')}"

        return entry

    async def _fetch_erc20_balances_list(self, chain_id, chain_name, w3_provider, address: str):
        erc20_abi = [
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function",
            },
        ]

        tasks = []
        token_symbols = []

        for symbol, env_data in self.token_data.items():
            token_info = None
            for _env_key, data in env_data.items():
                if data.get("chain_id") == chain_id:
                    token_info = data
                    break

            if token_info and token_info.get("address"):
                token_address = token_info.get("address")
                if token_address == "0x0000000000000000000000000000000000000000":
                    continue

                try:
                    contract = w3_provider.eth.contract(address=token_address, abi=erc20_abi)
                    tasks.append(contract.functions.balanceOf(address).call())
                    token_symbols.append((symbol, token_address, token_info.get("evmdecimals", 18)))
                except Exception:
                    # Skip tokens that fail contract creation
                    continue

        if not tasks:
            return []

        sem = asyncio.Semaphore(self.config.erc20_balance_concurrency)

        async def _guarded(coro):
            async with sem:
                return await coro

        results = await asyncio.gather(*(_guarded(t) for t in tasks), return_exceptions=True)
        balances = []

        for (symbol, token_address, decimals), balance_wei in zip(
            token_symbols, results, strict=False
        ):
            if isinstance(balance_wei, Exception):
                continue

            balance_fmt = Utils.unit_conversion(balance_wei, decimals, to_base=False)
            balances.append(
                {
                    "chain": chain_name,
                    "symbol": symbol,
                    "balance": str(balance_fmt),
                    "address": token_address,
                    "type": "ERC20",
                }
            )
        return balances

    @track_method("transfer")
    async def get_portfolio_balance(self, token: str, address: str | None = None) -> Result[dict]:
        """Get the portfolio balance for a token on the Dexalot L1 subnet.

        Note:
            Cached for 10 seconds (balance cache tier).

        Args:
            token: Token symbol (e.g. ``"USDC"``).
            address: Portfolio address to query.  Defaults to the current account.

        Returns:
            Result containing ``{"total": float, "available": float, "locked": float}``
            on success, or an error message on failure.
        """
        resolved_address = address or (
            cast(str, cast(Any, self.account).address) if self.account else None
        )
        return cast(
            Result[dict[Any, Any]],
            await self._get_portfolio_balance_cached(token, resolved_address),
        )

    @async_ttl_cached(_BALANCE_CACHE)
    async def _get_portfolio_balance_cached(
        self, token: str, query_address: str | None
    ) -> Result[dict]:
        """Internal cached implementation of get_portfolio_balance."""
        # Validate token symbol
        token_result = validate_token_symbol(token, "token")
        if not token_result.success:
            return cast(Result[dict[Any, Any]], token_result)

        token = self._normalize_user_token(token)

        if not query_address:
            return Result.fail("Address required (pass as param or set signer)")

        # Validate address format if provided
        address_result = validate_address(query_address, "address")
        if not address_result.success:
            return cast(Result[dict[Any, Any]], address_result)

        contract = self.portfolio_sub_contract
        if not contract:
            return Result.fail("Portfolio Subnet Contract not initialized.")

        try:
            symbol_bytes32 = Utils.to_bytes32(token)
            balance_data = await contract.functions.getBalance(query_address, symbol_bytes32).call()

            decimals = self._get_token_decimals(token, self.subnet_chain_id)
            if decimals is None:
                # Fallback to primary chain
                decimals = self._get_token_decimals(token, self.chain_id)

            if decimals is None:
                return Result.fail(f"Token {token} not supported on Subnet or Primary Chain.")

            return Result.ok(
                {
                    "total": Utils.unit_conversion(balance_data[0], decimals, to_base=False),
                    "available": Utils.unit_conversion(balance_data[1], decimals, to_base=False),
                    "locked": Utils.unit_conversion(balance_data[2], decimals, to_base=False),
                }
            )
        except Exception as e:
            error_msg = self._sanitize_error(e, "getting portfolio balance")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def get_all_portfolio_balances(self, address: str | None = None) -> Result[dict]:
        """Get all portfolio token balances on the Dexalot L1 subnet.

        Paginates through the contract's ``getBalances`` function in batches
        of 5 pages, up to a hard cap of 50 pages.

        Note:
            Cached for 10 seconds (balance cache tier).

        Args:
            address: Portfolio address to query.  Defaults to the current account.

        Returns:
            Result containing a dict of ``{symbol: {"total": float, "available": float, "locked": float}}``
            on success, or an error message on failure.
        """
        resolved_address = address or (
            cast(str, cast(Any, self.account).address) if self.account else None
        )
        return cast(
            Result[dict[Any, Any]],
            await self._get_all_portfolio_balances_cached(resolved_address),
        )

    @async_ttl_cached(_BALANCE_CACHE)
    async def _get_all_portfolio_balances_cached(self, query_address: str | None) -> Result[dict]:
        """Internal cached implementation of get_all_portfolio_balances."""
        if not query_address:
            return Result.fail("Address required (pass as param or set signer)")

        contract = self.portfolio_sub_contract
        if not contract:
            return Result.fail("Portfolio Subnet Contract not initialized.")

        try:
            all_balances: dict[str, dict[str, float]] = {}
            _BATCH_SIZE = 5
            _PAGE_HARD_CAP = 50
            batch_start = 0

            while batch_start < _PAGE_HARD_CAP:
                batch_pages = range(batch_start, min(batch_start + _BATCH_SIZE, _PAGE_HARD_CAP))
                results = await asyncio.gather(
                    *[contract.functions.getBalances(query_address, p).call() for p in batch_pages],
                    return_exceptions=True,
                )

                got_empty = False
                for data in results:
                    if isinstance(data, BaseException):
                        raise data
                    symbols_bytes = data[0]
                    if not symbols_bytes:
                        got_empty = True
                        break
                    totals = data[1]
                    availables = data[2]
                    for i, sym_bytes in enumerate(symbols_bytes):
                        symbol = Utils.from_bytes32(sym_bytes)
                        decimals = self._get_token_decimals(symbol, self.subnet_chain_id)
                        if decimals is None:
                            decimals = self._get_token_decimals(symbol, self.chain_id) or 18
                        total = Utils.unit_conversion(totals[i], decimals, to_base=False)
                        available = Utils.unit_conversion(availables[i], decimals, to_base=False)
                        all_balances[symbol] = {
                            "total": total,
                            "available": available,
                            "locked": total - available,
                        }

                if got_empty:
                    break
                batch_start += _BATCH_SIZE

            return Result.ok(all_balances)

        except Exception as e:
            error_msg = self._sanitize_error(e, "getting all balances")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def add_gas(self, amount: float, wait_for_receipt: bool = True) -> Result[dict]:
        """Withdraw ALOT from the Dexalot portfolio to the L1 wallet (add gas).

        Calls ``withdrawNative`` on the ``PortfolioSub`` contract, moving
        ALOT from the trading portfolio balance into the wallet so it can
        be used to pay transaction fees on Dexalot L1.

        Args:
            amount: Amount of ALOT to withdraw, in human-readable units.
            wait_for_receipt: If ``True``, block until the transaction is
                confirmed on-chain.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")
        from_addr = cast(str, cast(Any, self.account).address)

        w3 = self.w3_l1
        contract = self.portfolio_sub_contract

        if not w3 or not contract:
            return Result.fail("Subnet Provider or Portfolio Contract not initialized.")

        try:
            amount_wei = Utils.unit_conversion(amount, 18, to_base=True)
            tx_hash = await self._build_and_send_tx(
                w3,
                contract.functions.withdrawNative(from_addr, amount_wei),
                wait_for_receipt=wait_for_receipt,
            )
            return Result.ok({"tx_hash": tx_hash, "operation": "add_gas"})
        except Exception as e:
            error_msg = self._sanitize_error(e, "adding gas")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def remove_gas(self, amount: float, wait_for_receipt: bool = True) -> Result[dict]:
        """Deposit ALOT from the L1 wallet into the Dexalot portfolio (remove gas).

        Calls ``depositNative`` on the ``PortfolioSub`` contract, converting
        native ALOT in the wallet into portfolio balance available for trading.

        Args:
            amount: Amount of ALOT to deposit, in human-readable units.
            wait_for_receipt: If ``True``, block until the transaction is
                confirmed on-chain.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")
        from_addr = cast(str, cast(Any, self.account).address)

        w3 = self.w3_l1
        contract = self.portfolio_sub_contract

        if not w3 or not contract:
            return Result.fail("Subnet Provider or Portfolio Contract not initialized.")

        try:
            amount_wei = Utils.unit_conversion(amount, 18, to_base=True)
            tx_hash = await self._build_and_send_tx(
                w3,
                contract.functions.depositNative(from_addr, 0),
                value=amount_wei,
                wait_for_receipt=wait_for_receipt,
            )
            return Result.ok({"tx_hash": tx_hash, "operation": "remove_gas"})
        except Exception as e:
            error_msg = self._sanitize_error(e, "removing gas")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def transfer_portfolio(
        self, token: str, amount: float, to_address: str, wait_for_receipt: bool = True
    ) -> Result[dict]:
        """Transfer a token from the current portfolio to another address's portfolio on Dexalot L1.

        Args:
            token: Token symbol (e.g. ``"USDC"``).
            amount: Amount to transfer in human-readable units.
            to_address: Destination wallet address (checksummed or lowercase hex).
            wait_for_receipt: If ``True``, block until the transaction is
                confirmed on-chain.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        # Validate transfer parameters
        transfer_params_result = validate_transfer_params(token, amount, to_address)
        if not transfer_params_result.success:
            return cast(Result[dict], transfer_params_result)

        token = self._normalize_user_token(token)

        contract = self.portfolio_sub_contract
        if not contract:
            return Result.fail("Portfolio Subnet Contract not initialized.")

        try:
            decimals = self._get_token_decimals(token, self.subnet_chain_id) or 18
            amount_wei = Utils.unit_conversion(amount, decimals, to_base=True)
            symbol_bytes32 = Utils.to_bytes32(token)

            # Check balance first
            balance_result = await self.get_portfolio_balance(token)
            balance: dict[str, Any]
            if isinstance(balance_result, Result):
                if not balance_result.success:
                    return Result.fail(f"Error checking balance: {balance_result.error}")
                if balance_result.data is None:
                    return Result.fail("Invalid balance response format")
                balance = balance_result.data
            elif isinstance(balance_result, dict):
                balance = balance_result
            else:
                return Result.fail("Invalid balance response format")

            if balance["available"] < amount:
                return Result.fail(
                    f"Insufficient available balance. Available: {balance['available']}, Required: {amount}"
                )

            tx_hash = await self._build_and_send_tx(
                self.w3_l1,
                contract.functions.transferToken(to_address, symbol_bytes32, amount_wei),
                wait_for_receipt=wait_for_receipt,
            )
            return Result.ok({"tx_hash": tx_hash, "operation": "transfer_portfolio"})

        except Exception as e:
            error_msg = self._sanitize_error(e, "transferring portfolio asset")
            return Result.fail(error_msg)

    def _validate_deposit_params(self, token, amount, source_chain) -> Result[None]:
        """Validate deposit parameters.

        Returns:
            Result.ok(None) if valid, Result.fail(error_message) if invalid
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        token_result = validate_token_symbol(token, "token")
        if not token_result.success:
            return token_result

        amount_result = validate_positive_float(amount, "amount")
        if not amount_result.success:
            return amount_result

        if not isinstance(source_chain, str) or not source_chain.strip():
            return Result.fail(
                f"Invalid source_chain: must be non-empty string, got {type(source_chain).__name__}"
            )

        if source_chain not in self.chain_config:
            return Result.fail(
                f"Source chain '{source_chain}' not known. Available: {list(self.chain_config.keys())}"
            )

        config = self.chain_config[source_chain]
        if config["chain_id"] != self.chain_id:
            return Result.fail(
                f"Client initialized for chain ID {self.chain_id}, but requested source chain '{source_chain}' (ID {config['chain_id']}). Multi-chain switching not yet fully implemented."
            )

        return Result.ok(None)

    def _resolve_deposit_decimals(self, token: str) -> Result[int]:
        """Resolve token decimals for deposit.

        Returns:
            Result with decimals (int) on success, or error message on failure
        """
        decimals = self._get_token_decimals(token, self.chain_id)
        if decimals is None:
            if self._get_token_decimals(token, self.subnet_chain_id) is None:
                return Result.fail(f"Token {token} not supported on Subnet or Primary Chain.")
            decimals = 18
        return Result.ok(decimals)

    async def _get_l1_token_info(self, token: str) -> dict | None:
        """Get L1 token info for ERC20 deposit.

        Returns:
            Token info dict if found, None otherwise
        """
        if token not in self.token_data:
            return None

        for _env_key, info in self.token_data[token].items():
            if info.get("chain_id") == self.chain_id:
                return cast(dict[Any, Any], info)
        return None

    async def _execute_avax_deposit(
        self,
        w3,
        contract,
        amount_wei: int,
        bridge_id: int,
        bridge_fee: int,
        wait_for_receipt: bool = True,
    ) -> str:
        """Execute native AVAX deposit transaction.

        Returns:
            Transaction hash (str)
        """
        if not self.account:
            raise ValueError("Account is required for deposit.")
        from_addr = cast(str, cast(Any, self.account).address)
        total_value = amount_wei + bridge_fee
        return cast(
            str,
            await self._build_and_send_tx(
                w3,
                contract.functions.depositNative(from_addr, bridge_id),
                value=total_value,
                wait_for_receipt=wait_for_receipt,
            ),
        )

    async def _execute_erc20_deposit(
        self,
        w3,
        contract,
        token: str,
        amount_wei: int,
        bridge_id: int,
        bridge_fee: int,
        wait_for_receipt: bool = True,
    ) -> str:
        """Execute ERC20 token deposit transaction.

        Returns:
            Transaction hash (str)
        """
        if not self.account:
            raise ValueError("Account is required for deposit.")
        from_addr = cast(str, cast(Any, self.account).address)

        l1_token_info = await self._get_l1_token_info(token)
        if l1_token_info:
            await self._ensure_allowance(
                w3,
                l1_token_info["address"],
                contract.address,
                amount_wei,
                wait_for_receipt=wait_for_receipt,
            )

        symbol_bytes32 = Utils.to_bytes32(token)
        try:
            return cast(
                str,
                await self._build_and_send_tx(
                    w3,
                    contract.functions.depositToken(
                        from_addr, symbol_bytes32, amount_wei, bridge_id
                    ),
                    value=bridge_fee,
                    wait_for_receipt=wait_for_receipt,
                ),
            )
        except Exception:
            if l1_token_info:
                # Best-effort: revoke the approval to prevent a dangling allowance
                try:
                    await self._ensure_allowance(
                        w3,
                        l1_token_info["address"],
                        contract.address,
                        0,
                        wait_for_receipt=wait_for_receipt,
                    )
                except Exception:
                    pass
            raise

    @track_method("transfer")
    async def deposit(
        self,
        token: str,
        amount: float,
        source_chain: str,
        use_layerzero: bool = False,
        wait_for_receipt: bool = True,
    ) -> Result[dict]:
        """Deposit a token from a connected chain into the Dexalot portfolio.

        For AVAX, calls ``depositNative``; for ERC20 tokens, approves the
        ``PortfolioMain`` contract and calls ``depositToken``.  A bridge fee
        (returned by ``get_deposit_bridge_fee``) is added to the transaction
        value automatically.

        Args:
            token: Token symbol (e.g. ``"AVAX"`` or ``"USDC"``).
            amount: Amount to deposit in human-readable units.
            source_chain: Chain display name of the source network
                (e.g. ``"Avalanche"``).  Must be in ``self.chain_config``.
            use_layerzero: If ``True``, force LayerZero bridge instead of the
                default ICM bridge for supported chains.
            wait_for_receipt: If ``True``, block until the deposit transaction
                is confirmed on-chain.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")
        if not isinstance(source_chain, str) or not source_chain.strip():
            return Result.fail(
                f"Invalid source_chain: must be non-empty string, got {type(source_chain).__name__}"
            )

        # Validate parameters
        resolved_source_chain = self.resolve_chain_reference(source_chain)
        if not resolved_source_chain.success or resolved_source_chain.data is None:
            return Result.fail(
                resolved_source_chain.error or f"Could not resolve source chain '{source_chain}'."
            )

        canonical_source_chain = resolved_source_chain.data.canonical_name
        validation_result = self._validate_deposit_params(token, amount, canonical_source_chain)
        if not validation_result.success:
            return Result.fail(validation_result.error or "Invalid deposit parameters")

        token = self._normalize_user_token(token)

        w3 = self.w3_connected_chain
        contract = self.portfolio_main_avax_contract

        if not w3 or not contract:
            return Result.fail("L1 Provider or Portfolio Contract not initialized.")

        try:
            # Resolve decimals
            decimals_result = self._resolve_deposit_decimals(token)
            if not decimals_result.success:
                return Result.fail(decimals_result.error or "Could not resolve token decimals")
            if decimals_result.data is None:
                return Result.fail("Could not resolve token decimals")
            decimals = decimals_result.data

            amount_wei = Utils.unit_conversion(amount, decimals, to_base=True)
            bridge_id = self._get_bridge_id(canonical_source_chain, use_layerzero)
            symbol_bytes32 = Utils.to_bytes32(token)

            # Get bridge fee (default to 0 if calculation fails)
            try:
                bridge_fee = await self._get_bridge_fee_internal(
                    w3, contract, bridge_id, symbol_bytes32, amount_wei
                )
            except Exception:
                bridge_fee = 0

            # Execute deposit based on token type
            if token == "AVAX":
                tx_hash = await self._execute_avax_deposit(
                    w3,
                    contract,
                    amount_wei,
                    bridge_id,
                    bridge_fee,
                    wait_for_receipt=wait_for_receipt,
                )
            else:
                tx_hash = await self._execute_erc20_deposit(
                    w3,
                    contract,
                    token,
                    amount_wei,
                    bridge_id,
                    bridge_fee,
                    wait_for_receipt=wait_for_receipt,
                )

            return Result.ok({"tx_hash": tx_hash, "operation": "deposit"})

        except Exception as e:
            error_msg = self._sanitize_error(e, "depositing")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def withdraw(
        self,
        token: str,
        amount: float,
        destination_chain: str,
        use_layerzero: bool = False,
        wait_for_receipt: bool = True,
    ) -> Result[dict]:
        """Withdraw a token from the Dexalot portfolio to a connected chain wallet.

        Calls the ``PortfolioSub`` contract's withdraw function on Dexalot L1.
        The token arrives in the wallet on ``destination_chain`` after the
        bridge completes (a separate on-chain confirmation on the destination).

        Args:
            token: Token symbol (e.g. ``"USDC"`` or ``"AVAX"``).
            amount: Amount to withdraw in human-readable units.
            destination_chain: Chain display name of the target network
                (e.g. ``"Avalanche"``).  Must be in ``self.chain_config``.
            use_layerzero: If ``True``, force LayerZero bridge instead of the
                default ICM bridge for supported chains.
            wait_for_receipt: If ``True``, block until the withdrawal transaction
                is confirmed on Dexalot L1.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")
        from_addr = cast(str, cast(Any, self.account).address)

        # Validate token symbol
        token_result = validate_token_symbol(token, "token")
        if not token_result.success:
            return cast(Result[dict], token_result)

        token = self._normalize_user_token(token)

        # Validate amount
        amount_result = validate_positive_float(amount, "amount")
        if not amount_result.success:
            return cast(Result[dict], amount_result)

        # Validate destination_chain exists
        if not isinstance(destination_chain, str) or not destination_chain.strip():
            return Result.fail(
                f"Invalid destination_chain: must be non-empty string, got {type(destination_chain).__name__}"
            )

        resolved_destination_chain = self.resolve_chain_reference(destination_chain)
        if not resolved_destination_chain.success or resolved_destination_chain.data is None:
            return Result.fail(
                resolved_destination_chain.error
                or f"Could not resolve destination chain '{destination_chain}'."
            )

        canonical_destination_chain = resolved_destination_chain.data.canonical_name
        chain_config = self.chain_config.get(canonical_destination_chain)
        if not chain_config:
            return Result.fail(
                f"Destination chain '{destination_chain}' not known. Available: {list(self.chain_config.keys())}"
            )

        dest_chain_id = chain_config["chain_id"]
        w3 = self.w3_l1
        contract = self.portfolio_sub_contract

        if not w3 or not contract:
            return Result.fail("Subnet Provider or Portfolio Contract not initialized.")

        try:
            decimals = self._get_token_decimals(token, dest_chain_id)
            if decimals is None:
                return Result.fail(
                    f"Token {token} not supported on destination chain "
                    f"{canonical_destination_chain} (ID {dest_chain_id})."
                )

            amount_wei = Utils.unit_conversion(amount, decimals, to_base=True)
            bridge_id = self._get_bridge_id(canonical_destination_chain, use_layerzero)
            symbol_bytes32 = Utils.to_bytes32(token)

            # Check allowance if needed (subnet token)
            subnet_token_info = None
            if token in self.token_data:
                for _env_key, info in self.token_data[token].items():
                    if info.get("chain_id") == self.subnet_chain_id:
                        subnet_token_info = info
                        break

            tx_hash = await self._execute_erc20_withdrawal(
                w3,
                contract,
                from_addr,
                symbol_bytes32,
                amount_wei,
                bridge_id,
                dest_chain_id,
                subnet_token_info,
                wait_for_receipt=wait_for_receipt,
            )
            return Result.ok({"tx_hash": tx_hash, "operation": "withdraw"})

        except Exception as e:
            error_msg = self._sanitize_error(e, "withdrawing")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def get_deposit_bridge_fee(
        self, token: str, amount: float, source_chain: str
    ) -> Result[float]:
        """Estimate the bridge fee for a deposit transaction.

        Queries the ``PortfolioMain`` contract's ``getBridgeFee`` function.
        The ``deposit()`` method fetches this automatically, but you can call
        this directly for display or pre-flight checks.

        Args:
            token: Token symbol (e.g. ``"USDC"``).
            amount: Deposit amount in human-readable units (used to estimate fee).
            source_chain: Chain display name of the source network
                (e.g. ``"Avalanche"``).

        Returns:
            Result containing the bridge fee in native token units (``float``,
            denominated in ETH/AVAX) on success, or an error message on failure.
        """
        resolved_source_chain = self.resolve_chain_reference(source_chain)
        if not resolved_source_chain.success or resolved_source_chain.data is None:
            return Result.fail(
                resolved_source_chain.error or f"Could not resolve source chain '{source_chain}'."
            )

        canonical_source_chain = resolved_source_chain.data.canonical_name
        if canonical_source_chain not in self.chain_config:
            return Result.fail(f"Source chain '{source_chain}' not known.")

        w3 = self.w3_connected_chain
        contract = self.portfolio_main_avax_contract

        if not w3 or not contract:
            return Result.fail("L1 Provider or Portfolio Contract not initialized.")

        token_result = validate_token_symbol(token, "token")
        if not token_result.success:
            return cast(Result[float], token_result)

        amount_result = validate_positive_float(amount, "amount")
        if not amount_result.success:
            return cast(Result[float], amount_result)

        token = self._normalize_user_token(token)

        try:
            src_chain_id = self.chain_config[canonical_source_chain]["chain_id"]
            decimals = self._get_token_decimals(token, src_chain_id)
            if decimals is None:
                return Result.fail(
                    f"Token {token} not supported on source chain "
                    f"{canonical_source_chain} (ID {src_chain_id})."
                )

            amount_wei = Utils.unit_conversion(amount, decimals, to_base=True)
            symbol_bytes32 = Utils.to_bytes32(token)
            bridge_id = self._get_bridge_id(
                canonical_source_chain, False
            )  # Assuming default for fee check

            bridge_fee = await self._get_bridge_fee_internal(
                w3, contract, bridge_id, symbol_bytes32, amount_wei
            )
            return Result.ok(float(w3.from_wei(bridge_fee, "ether")))

        except Exception as e:
            error_msg = self._sanitize_error(e, "getting bridge fee")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def transfer_token(self, token: str, to_address: str, amount: float) -> Result[str]:
        """Transfer a token from the current account's portfolio to another account on Dexalot L1.

        Calls ``transferToken`` on the ``PortfolioSub`` contract.  Both source
        and destination addresses must have active portfolios on Dexalot L1.

        Args:
            token: Token symbol (e.g. ``"USDC"``).
            to_address: Destination wallet address on Dexalot L1.
            amount: Amount to transfer in human-readable units.

        Returns:
            Result containing a confirmation message with the transaction hash on
            success, or an error message on failure.
        """
        if not self.account:
            return Result.fail("Private key not configured.")

        # Validate transfer parameters
        transfer_params_result = validate_transfer_params(token, amount, to_address)
        if not transfer_params_result.success:
            return cast(Result[str], transfer_params_result)

        token = self._normalize_user_token(token)
        from_addr = cast(str, cast(Any, self.account).address)

        w3 = self.w3_l1
        contract = self.portfolio_sub_contract

        if not w3 or not contract:
            return Result.fail("Subnet Provider or Portfolio Contract not initialized.")

        try:
            decimals = self._get_token_decimals(token, self.subnet_chain_id)
            if decimals is None:
                decimals = self._get_token_decimals(token, self.chain_id) or 18

            amount_wei = Utils.unit_conversion(amount, decimals, to_base=True)
            symbol_bytes32 = Utils.to_bytes32(token)

            tx_hash = await self._build_and_send_tx(
                w3,
                contract.functions.transferToken(from_addr, to_address, symbol_bytes32, amount_wei),
            )
            return Result.ok(f"Transfer Token transaction sent: {tx_hash}")
        except Exception as e:
            error_msg = self._sanitize_error(e, "transferring token")
            return Result.fail(error_msg)

    # --- Helper Methods ---

    def _get_token_decimals(self, token, chain_id):
        """Resolve token decimals for a specific chain ID."""
        if token in self.token_data:
            for _env_key, info in self.token_data[token].items():
                if info.get("chain_id") == chain_id:
                    return info.get("evmdecimals", 18)
        return None

    def _get_bridge_id(self, chain_name, use_layerzero):
        """Determine Bridge ID based on chain name and override flag."""
        is_icm_chain = (
            any(c.lower() in chain_name.lower() for c in ICM_CHAINS) or "gunz" in chain_name.lower()
        )
        if is_icm_chain and not use_layerzero:
            return BRIDGE_ID_ICM
        return BRIDGE_ID_LZ

    async def _get_bridge_fee_internal(self, w3, contract, bridge_id, symbol_bytes32, amount_wei):
        """Internal helper to fetch bridge fee."""
        if not self.account:
            raise ValueError("Account is required for bridge fee lookup.")
        bridge_from = cast(str, cast(Any, self.account).address)

        portfolio_bridge_abi = [
            {
                "name": "getBridgeFee",
                "type": "function",
                "inputs": [
                    {"name": "_bridge", "type": "uint8"},
                    {"name": "_dstChainListOrgChainId", "type": "uint32"},
                    {"name": "", "type": "bytes32"},
                    {"name": "", "type": "uint256"},
                    {"name": "", "type": "address"},
                    {"name": "", "type": "bytes1"},
                ],
                "outputs": [{"name": "bridgeFee", "type": "uint256"}],
                "stateMutability": "view",
            },
            {
                "name": "portfolioBridge",
                "type": "function",
                "inputs": [],
                "outputs": [{"name": "", "type": "address"}],
                "stateMutability": "view",
            },
        ]

        # Propagate exceptions to caller
        bridge_address = await contract.functions.portfolioBridge().call()
        bridge_contract = w3.eth.contract(address=bridge_address, abi=portfolio_bridge_abi)

        return await bridge_contract.functions.getBridgeFee(
            bridge_id,
            self.subnet_chain_id,
            symbol_bytes32,
            amount_wei,
            bridge_from,
            b"\x00",
        ).call()

    async def _execute_erc20_withdrawal(
        self,
        w3,
        contract,
        from_addr: str,
        symbol_bytes32: bytes,
        amount_wei: int,
        bridge_id: int,
        dest_chain_id: int,
        subnet_token_info: dict | None,
        wait_for_receipt: bool = True,
    ) -> str:
        """Execute ERC20 token withdrawal transaction.

        Returns:
            Transaction hash (str)
        """
        if subnet_token_info:
            await self._ensure_allowance(
                w3,
                subnet_token_info["address"],
                contract.address,
                amount_wei,
                wait_for_receipt=wait_for_receipt,
            )

        try:
            return cast(
                str,
                await self._build_and_send_tx(
                    w3,
                    contract.functions.withdrawToken(
                        from_addr, symbol_bytes32, amount_wei, bridge_id, dest_chain_id
                    ),
                    wait_for_receipt=wait_for_receipt,
                ),
            )
        except Exception:
            if subnet_token_info:
                # Best-effort: revoke the approval to prevent a dangling allowance
                try:
                    await self._ensure_allowance(
                        w3,
                        subnet_token_info["address"],
                        contract.address,
                        0,
                        wait_for_receipt=wait_for_receipt,
                    )
                except Exception:
                    pass
            raise

    async def _ensure_allowance(
        self, w3, token_address, spender_address, amount_wei, wait_for_receipt: bool = True
    ):
        """Check and approve ERC20 allowance if needed."""
        if not self.account:
            raise ValueError("Account is required for allowance checks.")
        owner_addr = cast(str, cast(Any, self.account).address)

        erc20_abi = [
            {
                "constant": False,
                "inputs": [
                    {"name": "_spender", "type": "address"},
                    {"name": "_value", "type": "uint256"},
                ],
                "name": "approve",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function",
            },
            {
                "constant": True,
                "inputs": [
                    {"name": "_owner", "type": "address"},
                    {"name": "_spender", "type": "address"},
                ],
                "name": "allowance",
                "outputs": [{"name": "", "type": "uint256"}],
                "type": "function",
            },
        ]
        token_contract = w3.eth.contract(address=token_address, abi=erc20_abi)
        allowance = await token_contract.functions.allowance(owner_addr, spender_address).call()

        if allowance < amount_wei:
            await self._build_and_send_tx(
                w3,
                token_contract.functions.approve(spender_address, amount_wei),
                wait_for_receipt=wait_for_receipt,
            )

    async def _build_and_send_tx(self, w3, function_call, value=0, wait_for_receipt: bool = True):
        """Estimate gas, build, sign, and send transaction."""
        if not self.account:
            raise ValueError(
                "Account is required for signing transactions. Set signer or PRIVATE_KEY."
            )
        from_addr = cast(str, cast(Any, self.account).address)

        nonce = await self._get_nonce(w3)

        tx_params = {"from": from_addr}
        if value > 0:
            tx_params["value"] = value

        # estimate_gas is a method on the contract function, not directly on w3
        # We'll wrap it with retry/rate limiting manually
        if self._rpc_rate_limiter:
            await self._rpc_rate_limiter.acquire()

        if self.config.retry_enabled:
            from ..utils.retry import async_retry

            async def _estimate_gas():
                return await function_call.estimate_gas(tx_params)

            retry_func = async_retry(
                max_attempts=self.config.retry_max_attempts,
                initial_delay=self.config.retry_initial_delay,
                max_delay=self.config.retry_max_delay,
                exponential_base=self.config.retry_exponential_base,
                retry_on_status=self.config.retry_on_status,
                retry_on_exceptions=self.config.retry_on_exceptions,
            )(_estimate_gas)
            gas_estimate = await retry_func()
        else:
            gas_estimate = await function_call.estimate_gas(tx_params)

        gas_price = await self._rpc_call(w3, "eth.gas_price")

        tx = await function_call.build_transaction(
            {
                "from": from_addr,
                "nonce": nonce,
                "gas": int(gas_estimate * GAS_BUFFER),
                "gasPrice": gas_price,
                "value": value,
            }
        )

        # Use Account instance method - never expose private key
        signed_tx = self.account.sign_transaction(tx)
        tx_hash = await self._rpc_call(w3, "eth.send_raw_transaction", signed_tx.raw_transaction)

        # If it's an approval (function name 'approve'), wait for receipt?
        # The original code waited for approval receipt.
        if function_call.fn_name == "approve" or wait_for_receipt:
            receipt = await self._rpc_call(w3, "eth.wait_for_transaction_receipt", tx_hash)
            if wait_for_receipt:
                # Handle both dict and object receipts
                receipt_status = (
                    receipt.status if hasattr(receipt, "status") else receipt.get("status", 1)
                )
                if receipt_status != 1:
                    raise Exception("Transaction reverted")
            return w3.to_hex(tx_hash)

        return w3.to_hex(tx_hash)

    # ----------------------------------------------------------------------
    # USD price endpoints (public /api/info/...)
    # ----------------------------------------------------------------------

    @staticmethod
    def _coerce_usd_price(raw: Any) -> float | None:
        """Coerce a single raw price value into a finite non-negative float.

        Returns ``None`` for anything we cannot confidently interpret as a
        price (non-string/non-number, empty string, NaN, ±Infinity,
        negative).  The backend currently emits decimal strings (including
        scientific notation e.g. ``"1.04662e-7"``), so :func:`float` is
        the right primitive — but we tolerate plain numbers too in case
        the shape ever flips.
        """
        if isinstance(raw, bool):
            # bool is a subclass of int — disallow explicitly to avoid
            # treating ``True``/``False`` as 1/0 prices.
            return None
        if isinstance(raw, int | float):
            n = float(raw)
            if not math.isfinite(n) or n < 0:
                return None
            return n
        if not isinstance(raw, str):
            return None
        trimmed = raw.strip()
        if trimmed == "":
            return None
        try:
            n = float(trimmed)
        except (ValueError, TypeError):
            return None
        if not math.isfinite(n) or n < 0:
            return None
        return n

    @track_method("transfer")
    async def get_token_usd_prices(self, env: str | None = None) -> Result[dict[str, float]]:
        """Fetch current USD prices for every Dexalot-listed token.

        Public endpoint, no signed auth required.  Cached for 15 minutes
        (semi-static cache tier).

        The backend currently emits the price map as a flat
        ``dict[str, str]`` (string prices, including scientific notation
        for very small values); the SDK coerces to ``float`` and silently
        drops entries it cannot interpret.  An array-of-objects fallback
        (``[{"symbol", "price"}, ...]``) is also accepted in case the
        backend shape ever changes.

        The ``env`` query parameter is forwarded for parity with the
        TypeScript SDK and to namespace the cache key per network; the
        backend itself currently determines the network from the API host
        and does not consult the parameter.

        Args:
            env: Optional environment label override.  Defaults to
                ``self.parent_env``.

        Returns:
            ``Result.ok({symbol: price})`` on success,
            ``Result.fail(msg)`` on network failure or unexpected response
            shape.
        """
        target_env = env if env is not None else self.parent_env
        return cast(
            Result[dict[str, float]],
            await self._get_token_usd_prices_cached(target_env),
        )

    @async_ttl_cached(_SEMI_STATIC_CACHE)
    async def _get_token_usd_prices_cached(self, env: str) -> Result[dict[str, float]]:
        """Internal cached implementation of get_token_usd_prices."""
        try:
            data = await self._api_call(
                "get",
                f"{self.api_base_url}{ENDPOINT_INFO_USD_PRICES}",
                params={"env": env},
            )
            out: dict[str, float] = {}
            if isinstance(data, list):
                # Forward-compat: array-of-objects shape.
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    raw_symbol = row.get("symbol")
                    if not isinstance(raw_symbol, str):
                        continue
                    symbol = raw_symbol.strip()
                    if not symbol:
                        continue
                    price = self._coerce_usd_price(row.get("price"))
                    if price is None:
                        continue
                    out[symbol] = price
                return Result.ok(out)
            if isinstance(data, dict):
                # Current shape: flat ``Record<string, string>`` map.
                for symbol, raw in data.items():
                    if not isinstance(symbol, str):
                        continue
                    trimmed_sym = symbol.strip()
                    if not trimmed_sym:
                        continue
                    price = self._coerce_usd_price(raw)
                    if price is None:
                        continue
                    out[trimmed_sym] = price
                return Result.ok(out)
            return Result.fail(
                f"Unexpected USD prices response shape: expected object or array, got {type(data).__name__}."
            )
        except Exception as e:
            return Result.fail(self._sanitize_error(e, "fetching token USD prices"))

    # ----------------------------------------------------------------------
    # USD price history (daily + hourly, public /api/info/...)
    # ----------------------------------------------------------------------

    @staticmethod
    def _coerce_timestamp_seconds(raw: Any) -> int | None:
        """Coerce a raw numeric / numeric-string timestamp to unix seconds.

        Values ``>= 1e12`` are treated as milliseconds and divided by 1000
        (a 32-bit second-precision unix epoch maxes out at ``2^31 ≈ 2.1e9``,
        so 1e12 is a safe boundary).
        """
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int | float):
            n = float(raw)
        elif isinstance(raw, str):
            trimmed = raw.strip()
            if trimmed == "":
                return None
            try:
                n = float(trimmed)
            except ValueError:
                return None
        else:
            return None
        if not math.isfinite(n) or n < 0:
            return None
        if n >= 1e12:
            n = math.floor(n / 1000)
        return int(math.floor(n))

    @staticmethod
    def _coerce_transfer_ts(raw: Any) -> int | None:
        """ISO-8601 string OR numeric/numeric-string -> unix seconds (UTC), or None.

        The combined-transfers backend returns source_ts/target_ts as ISO-8601
        strings; _coerce_timestamp_seconds only handles numerics, so parse ISO
        here first (mirrors the TS SDK's _coerceTransferTs). Falls back to the
        numeric coercer for numeric / numeric-string inputs.
        """
        if isinstance(raw, str) and raw.strip():
            try:
                dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            except ValueError:
                dt = None
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return int(dt.timestamp())
        return TransferClient._coerce_timestamp_seconds(raw)

    @staticmethod
    def _extract_history_timestamp(row: dict[str, Any]) -> int | None:
        """Pull a timestamp out of one raw price-history row.

        Backend ships ``date`` as ISO-8601; we additionally accept the
        numeric aliases ``ts`` / ``timestamp`` / ``time`` (value treated
        as milliseconds when ``>= 1e12``) so the contract is stable if
        the shape ever flips.  Returns unix seconds (UTC) or ``None``.
        """
        raw_date = row.get("date")
        if isinstance(raw_date, str) and raw_date.strip():
            try:
                # Accept ``...Z`` and timezone-naive ISO strings.  Python
                # 3.11+ ``fromisoformat`` handles ``Z`` directly via the
                # explicit ``+00:00`` replacement.
                dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                dt = None
            if dt is not None:
                return int(dt.timestamp())
        for key in ("ts", "timestamp", "time"):
            if key in row:
                coerced = TransferClient._coerce_timestamp_seconds(row[key])
                if coerced is not None:
                    return coerced
        return None

    @track_method("transfer")
    async def get_token_price_history(
        self,
        token: str,
        *,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> Result[list[PricePoint]]:
        """Daily USD price history for one token.

        Returns an ascending-time ordered ``list[PricePoint]`` from the
        public ``/api/info/token-usd-price-history`` endpoint.  Past
        prices do not change, so results are cached in the static tier
        (1 hour TTL); the cache key is path-namespaced so daily and
        hourly never collide.

        The optional ``from_ts`` / ``to_ts`` window is in unix seconds.
        The backend currently ignores it (the host fixes the network and
        the lookback) but the SDK forwards both for forward-compat and
        additionally filters the returned series client-side so the
        caller's range contract holds regardless of backend behavior.

        No authentication required (public endpoint).
        """
        return await self._fetch_price_history(ENDPOINT_INFO_PRICE_HISTORY, token, from_ts, to_ts)

    @track_method("transfer")
    async def get_token_hourly_price_history(
        self,
        token: str,
        *,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> Result[list[PricePoint]]:
        """Hourly USD price history for one token.

        Same contract as :meth:`get_token_price_history` (ascending-time
        ``list[PricePoint]``, static-tier 1 h cache, optional
        ``from_ts``/``to_ts`` window applied client-side) but routes
        through the hourly endpoint.  Useful when more granular series
        is needed than the daily variant — backend currently returns the
        trailing ~24 h at 3-hour granularity.

        No authentication required (public endpoint).
        """
        return await self._fetch_price_history(
            ENDPOINT_INFO_HOURLY_PRICE_HISTORY, token, from_ts, to_ts
        )

    async def _fetch_price_history(
        self,
        path: str,
        token: str,
        from_ts: int | None,
        to_ts: int | None,
    ) -> Result[list[PricePoint]]:
        """Shared validation + cache delegation for daily / hourly history.

        Validates the token symbol up-front (avoids polluting the cache
        with validation failures) then delegates to the cached helper.
        """
        token_result = validate_token_symbol(token, "token")
        if not token_result.success:
            return cast(Result[list[PricePoint]], token_result)
        sym = self._normalize_user_token(token)
        return cast(
            Result[list[PricePoint]],
            await self._fetch_price_history_cached(path, sym, from_ts, to_ts),
        )

    @async_ttl_cached(_STATIC_CACHE)
    async def _fetch_price_history_cached(
        self,
        path: str,
        token: str,
        from_ts: int | None,
        to_ts: int | None,
    ) -> Result[list[PricePoint]]:
        """Internal cached implementation of price-history fetch.

        Cache key includes ``(path, token, from_ts, to_ts)`` so daily
        and hourly never collide on the same slot even with identical
        ``(token, from_ts, to_ts)`` tuples.
        """
        try:
            params: dict[str, Any] = {"token": token}
            if from_ts is not None:
                params["from"] = from_ts
            if to_ts is not None:
                params["to"] = to_ts
            data = await self._api_call(
                "get",
                f"{self.api_base_url}{path}",
                params=params,
            )
            if not isinstance(data, list):
                return Result.fail(
                    f"Unexpected price history response shape: expected array, got {type(data).__name__}."
                )
            points: list[PricePoint] = []
            for row in data:
                if not isinstance(row, dict):
                    continue
                ts = self._extract_history_timestamp(row)
                if ts is None:
                    continue
                price = self._coerce_usd_price(row.get("price"))
                if price is None:
                    continue
                if from_ts is not None and ts < from_ts:
                    continue
                if to_ts is not None and ts > to_ts:
                    continue
                points.append(PricePoint(timestamp=ts, price=price))
            points.sort(key=lambda p: p.timestamp)
            return Result.ok(points)
        except Exception as e:
            return Result.fail(self._sanitize_error(e, "fetching token price history"))

    # ----------------------------------------------------------------------
    # Combined transfer history (signed /api/trading/signed/transferscombined)
    # ----------------------------------------------------------------------

    def _normalize_transfer(self, raw: Any) -> Transfer | None:
        """Normalize one raw ``DBTransfer``-shaped row into a ``Transfer``.

        Returns ``None`` for any row that is missing required fields or
        carries an unknown ``action_type`` / ``status`` enum — the
        public method silently drops these so a single bad row does not
        poison the page.

        ``bridge`` falls back to ``"NATIVE"`` for unknown enum values
        because the frontend treats unrecognised bridges the same way
        (display-only label, no behavior depends on the precise id),
        and the row is otherwise valid.
        """
        if not isinstance(raw, dict):
            return None

        action_raw = raw.get("action_type")
        if not isinstance(action_raw, int) or isinstance(action_raw, bool):
            return None
        action_type = _TRANSFER_ACTION_TYPE_LABELS.get(action_raw)
        if action_type is None:
            return None

        status_raw = raw.get("status")
        if not isinstance(status_raw, int) or isinstance(status_raw, bool):
            return None
        status = _TRANSFER_STATUS_LABELS.get(status_raw)
        if status is None:
            return None

        symbol_raw = raw.get("symbol")
        if not isinstance(symbol_raw, str) or not symbol_raw:
            return None

        quantity = self._coerce_usd_price(raw.get("quantity"))
        if quantity is None:
            return None

        # Fee defaults to 0 if missing or unparseable — many native /
        # portfolio-internal legs report ``"0"`` and some omit the field.
        fee = self._coerce_usd_price(raw.get("fee"))
        if fee is None:
            fee = 0.0

        trader_address_raw = raw.get("traderaddress")
        trader_address = trader_address_raw if isinstance(trader_address_raw, str) else ""

        bridge_raw = raw.get("bridge")
        if isinstance(bridge_raw, int) and not isinstance(bridge_raw, bool):
            bridge = _TRANSFER_BRIDGE_LABELS.get(bridge_raw, "NATIVE")
        else:
            bridge = "NATIVE"

        bridge_url_raw = raw.get("bridge_url")
        bridge_url = bridge_url_raw if isinstance(bridge_url_raw, str) else ""

        nonce_raw = raw.get("nonce")
        nonce = nonce_raw if isinstance(nonce_raw, int) and not isinstance(nonce_raw, bool) else -1

        source_env_raw = raw.get("source_env")
        source_env = source_env_raw if isinstance(source_env_raw, str) else ""

        source_chain_id_raw = raw.get("source_chain_id")
        source_chain_id = (
            source_chain_id_raw
            if isinstance(source_chain_id_raw, int) and not isinstance(source_chain_id_raw, bool)
            else 0
        )

        source_tx_raw = raw.get("source_tx")
        source_tx = source_tx_raw if isinstance(source_tx_raw, str) else ""

        source_ts = self._coerce_transfer_ts(raw.get("source_ts")) or 0

        target_env_raw = raw.get("target_env")
        target_env = target_env_raw if isinstance(target_env_raw, str) else None

        target_chain_id_raw = raw.get("target_chain_id")
        target_chain_id = (
            target_chain_id_raw
            if isinstance(target_chain_id_raw, int) and not isinstance(target_chain_id_raw, bool)
            else None
        )

        target_tx_raw = raw.get("target_tx")
        target_tx = target_tx_raw if isinstance(target_tx_raw, str) else None

        target_ts = self._coerce_transfer_ts(raw.get("target_ts"))

        return Transfer(
            action_type=action_type,
            status=status,
            symbol=symbol_raw,
            quantity=quantity,
            fee=fee,
            trader_address=trader_address,
            bridge=bridge,
            bridge_url=bridge_url,
            nonce=nonce,
            source_env=source_env,
            source_chain_id=source_chain_id,
            source_tx=source_tx,
            source_ts=source_ts,
            target_env=target_env,
            target_chain_id=target_chain_id,
            target_tx=target_tx,
            target_ts=target_ts,
        )

    @track_method("transfer")
    async def get_combined_transfers(
        self,
        *,
        symbol: str | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Result[list[Transfer]]:
        """Paginated history of every deposit, withdrawal, gas top-up,
        portfolio P2P send/receive, and bridge recovery involving the
        connected wallet.

        Routes through the signed REST endpoint
        ``/api/trading/signed/transferscombined``; ``x-signature`` is
        attached via :meth:`_get_auth_headers`.  Returns canonical
        :class:`Transfer` rows with snake_case fields and human-readable
        ``action_type`` / ``status`` / ``bridge`` labels lifted from the
        backend's numeric enums.

        Backend pagination uses ``itemsperpage`` / ``pageno`` (NOT
        ``limit`` / ``offset``); the SDK accepts the more conventional
        ``limit`` / ``offset`` signature and translates internally
        (``pageno = (offset // limit) + 1``).

        Cached for 10 seconds (balance tier) per
        ``(address, symbol, from_ts, to_ts, limit, offset)`` tuple —
        distinct signers and distinct filter combinations never share a
        cache slot.  Returned ``quantity`` / ``fee`` are already display-
        decimal — no wei→human conversion is applied because the backend
        has already done it.

        Args:
            symbol: Optional token symbol filter (forwarded to backend as
                ``symbol``).  Normalised through :meth:`_normalize_user_token`.
            from_ts: Optional unix-seconds lower bound (forwarded as
                ``periodfrom``).
            to_ts: Optional unix-seconds upper bound (forwarded as
                ``periodto``).
            limit: Page size (forwarded as ``itemsperpage``, default 100).
            offset: Row offset (translated to ``pageno`` = ``offset // limit + 1``,
                default 0 → ``pageno=1``).
        """
        if not self.account:
            return Result.fail("get_combined_transfers requires a configured wallet")

        try:
            address = cast(str, cast(Any, self.account).address)
        except Exception as e:
            return Result.fail(self._sanitize_error(e, "resolving wallet address"))

        normalized_symbol: str | None = None
        if symbol is not None:
            normalized_symbol = self._normalize_user_token(symbol)

        # Translate (limit, offset) → (itemsperpage, pageno).  The backend
        # uses 1-indexed pages; offset 0 → page 1.  Defensive ``max(1, ...)``
        # on items-per-page avoids a ZeroDivisionError on a caller-supplied
        # ``limit=0`` while still surfacing it as a likely-empty page.
        items_per_page = max(1, int(limit))
        page_no = (int(offset) // items_per_page) + 1

        return cast(
            Result[list[Transfer]],
            await self._get_combined_transfers_cached(
                address,
                normalized_symbol,
                from_ts,
                to_ts,
                items_per_page,
                page_no,
            ),
        )

    @staticmethod
    def _unix_seconds_to_iso(ts: int) -> str:
        """Convert unix seconds to an ISO-8601 string (e.g. ``2026-05-01T00:00:00.000Z``).

        The combined-transfers backend rejects raw unix integers for
        ``periodfrom``/``periodto`` with "Malformed Request! ISO Date format
        problem"; they must be ISO-8601. Matches the TypeScript SDK's
        ``new Date(ts * 1000).toISOString()`` output byte-for-byte.
        """
        return (
            datetime.fromtimestamp(ts, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @async_ttl_cached(_BALANCE_CACHE)
    async def _get_combined_transfers_cached(
        self,
        address: str,
        symbol: str | None,
        period_from: int | None,
        period_to: int | None,
        items_per_page: int,
        page_no: int,
    ) -> Result[list[Transfer]]:
        """Internal cached implementation of get_combined_transfers.

        Cache key is namespaced by resolved address so signer swaps within
        a single client never collide on the same slot, and by every
        translated opt so different filter combinations are distinct.
        """
        try:
            headers = self._get_auth_headers()
        except Exception as e:
            return Result.fail(self._sanitize_error(e, "fetching combined transfers"))

        params: dict[str, Any] = {
            "itemsperpage": items_per_page,
            "pageno": page_no,
        }
        if symbol is not None:
            params["symbol"] = symbol
        # The backend's periodfrom/periodto expect ISO-8601 date strings, not
        # unix seconds — forwarding the raw integers makes the endpoint reject
        # the request ("Malformed Request! ISO Date format problem"). Convert
        # the ergonomic unix-seconds inputs to ISO-8601 here.
        if period_from is not None:
            params["periodfrom"] = self._unix_seconds_to_iso(period_from)
        if period_to is not None:
            params["periodto"] = self._unix_seconds_to_iso(period_to)

        try:
            data = await self._api_call(
                "get",
                f"{self.api_base_url}{ENDPOINT_TRADING_COMBINED_TRANSFERS}",
                headers=headers,
                params=params,
            )
        except Exception as e:
            return Result.fail(self._sanitize_error(e, "fetching combined transfers"))

        # Backend ships ``{count, rows}``; we tolerate a bare array as a
        # forward-compat fallback.
        if isinstance(data, list):
            rows: list[Any] = data
        elif isinstance(data, dict):
            envelope_rows = data.get("rows")
            rows = envelope_rows if isinstance(envelope_rows, list) else []
        else:
            return Result.fail(
                f"Unexpected transfers response shape: expected object or array, got {type(data).__name__}."
            )

        transfers: list[Transfer] = []
        for row in rows:
            normalized = self._normalize_transfer(row)
            if normalized is not None:
                transfers.append(normalized)
        return Result.ok(transfers)
