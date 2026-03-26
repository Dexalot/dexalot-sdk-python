import asyncio
from typing import Any, cast

from ..constants import (
    BRIDGE_ID_ICM,
    BRIDGE_ID_LZ,
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
from .base import _BALANCE_CACHE, _SEMI_STATIC_CACHE, DexalotBaseClient


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
        # Fallback to mainnet_providers if provider manager is disabled or returns None
        return self.mainnet_providers.get(chain)

    def _get_available_chains(self) -> list[str]:
        """
        Get list of available chains.

        Returns:
            List of chain names
        """
        # Always include chains from mainnet_providers (for backwards compatibility)
        chains = set(self.mainnet_providers.keys())

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

            # Update instance variable for backward compatibility
            self.token_data = token_data

            if token in token_data:
                return Result.ok(token_data[token])
            return Result.fail(f"Token {token} not found.")
        except Exception as e:
            error_msg = self._sanitize_error(e, "getting token details")
            return Result.fail(error_msg)

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
        resolved_address = address or (
            cast(str, cast(Any, self.account).address) if self.account else None
        )
        return cast(
            Result[dict[Any, Any]],
            await self._get_chain_wallet_balance_cached(chain, token, resolved_address),
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

        # Check connected mainnet
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
        resolved_address = address or (
            cast(str, cast(Any, self.account).address) if self.account else None
        )
        return cast(
            Result[dict[Any, Any]],
            await self._get_chain_wallet_balances_cached(chain, resolved_address),
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

        # Check connected mainnet
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
    async def get_all_chain_wallet_balances(self, address: str | None = None) -> Result[dict]:
        """Get all token balances across all connected chain wallets.

        Queries Dexalot L1 (native ALOT) and all connected mainnet chains
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

        # Connected Mainnet Network Balances
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
        """Get native token balance on a mainnet chain."""
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
                    "total": balance_data[0] / (10**decimals),
                    "available": balance_data[1] / (10**decimals),
                    "locked": balance_data[2] / (10**decimals),
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
                        total = totals[i] / (10**decimals)
                        available = availables[i] / (10**decimals)
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
    async def add_gas(self, amount: float, wait_for_receipt: bool = True) -> Result[str]:
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
            amount_wei = w3.to_wei(amount, "ether")
            tx_hash = await self._build_and_send_tx(
                w3,
                contract.functions.withdrawNative(from_addr, amount_wei),
                wait_for_receipt=wait_for_receipt,
            )
            return Result.ok(f"Add Gas transaction sent: {tx_hash}")
        except Exception as e:
            error_msg = self._sanitize_error(e, "adding gas")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def remove_gas(self, amount: float, wait_for_receipt: bool = True) -> Result[str]:
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
            amount_wei = w3.to_wei(amount, "ether")
            tx_hash = await self._build_and_send_tx(
                w3,
                contract.functions.depositNative(from_addr, 0),
                value=amount_wei,
                wait_for_receipt=wait_for_receipt,
            )
            return Result.ok(f"Remove Gas transaction sent: {tx_hash}")
        except Exception as e:
            error_msg = self._sanitize_error(e, "removing gas")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def transfer_portfolio(
        self, token: str, amount: float, to_address: str, wait_for_receipt: bool = True
    ) -> Result[str]:
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
            return cast(Result[str], transfer_params_result)

        contract = self.portfolio_sub_contract
        if not contract:
            return Result.fail("Portfolio Subnet Contract not initialized.")

        try:
            decimals = self._get_token_decimals(token, self.subnet_chain_id) or 18
            amount_wei = int(amount * (10**decimals))
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
            return Result.ok(f"Transfer transaction sent: {tx_hash}")

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
        self, token: str, amount: float, source_chain: str,
        use_layerzero: bool = False, wait_for_receipt: bool = True
    ) -> Result[str]:
        """Deposit a token from a mainnet chain into the Dexalot portfolio.

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
        # Validate parameters
        validation_result = self._validate_deposit_params(token, amount, source_chain)
        if not validation_result.success:
            return Result.fail(validation_result.error or "Invalid deposit parameters")

        w3 = self.w3_mainnet
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

            amount_wei = int(amount * (10**decimals))
            bridge_id = self._get_bridge_id(source_chain, use_layerzero)
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

            return Result.ok(f"Deposit transaction sent: {tx_hash}")

        except Exception as e:
            error_msg = self._sanitize_error(e, "depositing")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def withdraw(
        self, token: str, amount: float, destination_chain: str,
        use_layerzero: bool = False, wait_for_receipt: bool = True
    ) -> Result[str]:
        """Withdraw a token from the Dexalot portfolio to a mainnet chain wallet.

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
            return cast(Result[str], token_result)

        # Validate amount
        amount_result = validate_positive_float(amount, "amount")
        if not amount_result.success:
            return cast(Result[str], amount_result)

        # Validate destination_chain exists
        if not isinstance(destination_chain, str) or not destination_chain.strip():
            return Result.fail(
                f"Invalid destination_chain: must be non-empty string, got {type(destination_chain).__name__}"
            )

        chain_config = self.chain_config.get(destination_chain)
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
                    f"Token {token} not supported on destination chain {destination_chain} (ID {dest_chain_id})."
                )

            amount_wei = int(amount * (10**decimals))
            bridge_id = self._get_bridge_id(destination_chain, use_layerzero)
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
            return Result.ok(f"Withdraw transaction sent: {tx_hash}")

        except Exception as e:
            error_msg = self._sanitize_error(e, "withdrawing")
            return Result.fail(error_msg)

    @track_method("transfer")
    async def get_deposit_bridge_fee(self, token: str, amount: float, source_chain: str) -> Result[float]:
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
        if source_chain not in self.chain_config:
            return Result.fail(f"Source chain '{source_chain}' not known.")

        w3 = self.w3_mainnet
        contract = self.portfolio_main_avax_contract

        if not w3 or not contract:
            return Result.fail("L1 Provider or Portfolio Contract not initialized.")

        try:
            src_chain_id = self.chain_config[source_chain]["chain_id"]
            decimals = self._get_token_decimals(token, src_chain_id)
            if decimals is None:
                return Result.fail(
                    f"Token {token} not supported on source chain {source_chain} (ID {src_chain_id})."
                )

            amount_wei = int(amount * (10**decimals))
            symbol_bytes32 = Utils.to_bytes32(token)
            bridge_id = self._get_bridge_id(source_chain, False)  # Assuming default for fee check

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
        from_addr = cast(str, cast(Any, self.account).address)

        w3 = self.w3_l1
        contract = self.portfolio_sub_contract

        if not w3 or not contract:
            return Result.fail("Subnet Provider or Portfolio Contract not initialized.")

        try:
            decimals = self._get_token_decimals(token, self.subnet_chain_id)
            if decimals is None:
                decimals = self._get_token_decimals(token, self.chain_id) or 18

            amount_wei = int(amount * (10**decimals))
            symbol_bytes32 = Utils.to_bytes32(token)

            tx_hash = await self._build_and_send_tx(
                w3,
                contract.functions.transferToken(
                    from_addr, to_address, symbol_bytes32, amount_wei
                ),
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
        allowance = await token_contract.functions.allowance(
            owner_addr, spender_address
        ).call()

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
