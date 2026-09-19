from collections.abc import Awaitable, Callable
from typing import Any, cast

from web3 import Web3

from ..constants import (
    DEFAULT_TAKER_ADDRESS,
    ENDPOINT_RFQ_FIRM_QUOTE,
    ENDPOINT_RFQ_PAIR_PRICE,
    ENDPOINT_RFQ_PAIRS,
)
from ..utils.cache import async_ttl_cached
from ..utils.input_validators import (
    validate_chain_identifier,
    validate_swap_params,
)
from ..utils.observability import track_method
from ..utils.result import Result
from .base import _SEMI_STATIC_CACHE, _STATIC_CACHE, DexalotBaseClient

# MainnetRFQ uses the zero address to mean "the chain's native coin" (e.g. AVAX
# on 43114).  When the taker is selling native, ``msg.value`` must equal
# ``takerAmount``; for ERC20 takers it must be 0.
NATIVE_ZERO_ADDRESS = "0x" + "0" * 40

# Gas headroom applied on top of ``estimate_gas`` for swap and approval txs.
SWAP_GAS_BUFFER = 1.2

# The RFQ API serves firm quotes from several maker contracts (the legacy
# MainnetRFQ plus DexalotRFQ instances), all reachable through DexalotRouter,
# which ``MainnetRFQ.trustedForwarder()`` points at.  Each maker has its own
# EIP-712 domain and swap signer, so a quote is only valid on ``order.maker``
# (called directly, or via the router which forwards the call to it).  The
# deployments endpoint publishes the legacy MainnetRFQ and the DexalotRouter
# but not the individual makers, so the allow-list is read on-chain with these
# ABI fragments (and the router too, when the backend does not publish it).
_RFQ_TRUSTED_FORWARDER_ABI: list[dict[str, Any]] = [
    {
        "name": "trustedForwarder",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    }
]
_ROUTER_ALLOWED_RFQS_ABI: list[dict[str, Any]] = [
    {
        "name": "getAllowedRFQs",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address[]"}],
    }
]
# simpleSwap((uint256,uint128,address,address,address,address,uint256,uint256),bytes)
# is shared by MainnetRFQ, DexalotRFQ and the router's forwarding fallback.
_SIMPLE_SWAP_ABI: list[dict[str, Any]] = [
    {
        "name": "simpleSwap",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "_order",
                "type": "tuple",
                "components": [
                    {"name": "nonceAndMeta", "type": "uint256"},
                    {"name": "expiry", "type": "uint128"},
                    {"name": "makerAsset", "type": "address"},
                    {"name": "takerAsset", "type": "address"},
                    {"name": "maker", "type": "address"},
                    {"name": "taker", "type": "address"},
                    {"name": "makerAmount", "type": "uint256"},
                    {"name": "takerAmount", "type": "uint256"},
                ],
            },
            {"name": "_signature", "type": "bytes"},
        ],
        "outputs": [],
    }
]
_ERC20_ALLOWANCE_ABI: list[dict[str, Any]] = [
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]


class SwapClient(DexalotBaseClient):
    async def _get_w3_l1(self):
        """
        Get w3_l1 provider, using provider manager if enabled.

        Returns:
            AsyncWeb3 instance or None if not available
        """
        if self._provider_manager:
            provider = await self._provider_manager.get_provider("DEXALOT_L1")
            if provider:
                return provider
        # Fallback to direct w3_l1 if provider manager is disabled or returns None
        return self.w3_l1

    @async_ttl_cached(_SEMI_STATIC_CACHE)
    @track_method("swap")
    async def get_swap_pairs(self, chain_identifier: int | str) -> Result[dict]:
        """Fetch available SimpleSwap (RFQ) pairs for a specific chain.

        Note:
            Cached for 15 minutes (semi-static cache tier).

        Args:
            chain_identifier: Chain ID (``int``) or chain name (``str``), e.g.
                ``43114`` or ``"Avalanche"``.

        Returns:
            Result containing a dict of RFQ pair data keyed by pair symbol on
            success, or an error message on failure.
        """
        if not self._cache_enabled:
            # Bypass cache by clearing it for this call
            env_key = getattr(self, "api_base_url", "") or ""
            key: tuple[Any, ...] = ("get_swap_pairs", env_key, (chain_identifier,), frozenset())
            _SEMI_STATIC_CACHE._store.pop(key, None)

        # Validate chain_identifier format
        chain_id_result = validate_chain_identifier(chain_identifier, "chain_identifier")
        if not chain_id_result.success:
            return cast(Result[dict[Any, Any]], chain_id_result)

        resolved_chain_result = self._resolve_chain_id_result(chain_identifier)
        if not resolved_chain_result.success or resolved_chain_result.data is None:
            return Result.fail(
                resolved_chain_result.error
                or f"Could not resolve chain identifier '{chain_identifier}' to a Chain ID."
            )
        chain_id = resolved_chain_result.data

        # Ensure RFQ pairs are loaded (will use cache if available)
        # Since _fetch_rfq_pairs is internal, we'll fetch directly here
        # Note: This uses instance variable, but ensures fresh data via cache TTL
        if not self.rfq_pairs or chain_id not in self.rfq_pairs:
            # Fetch RFQ pairs if not loaded or chain_id not present
            rfq_url = f"{self.api_base_url}{ENDPOINT_RFQ_PAIRS}"
            try:
                async with await self._make_http_request(
                    "get", rfq_url, params={"chainid": chain_id}
                ) as response:
                    response.raise_for_status()
                    pairs_data = await response.json()
                    if not self.rfq_pairs:
                        self.rfq_pairs = {}
                    self.rfq_pairs[chain_id] = pairs_data
            except Exception as e:
                error_msg = self._sanitize_error(e, "fetching RFQ pairs")
                return Result.fail(f"Failed to fetch RFQ pairs: {error_msg}")

        if chain_id not in self.rfq_pairs:
            return Result.fail(
                f"No swap pairs found for Chain ID {chain_id}. (Pairs might not be loaded or chain not supported)"
            )

        return Result.ok(self.rfq_pairs[chain_id])

    def _rehydrate_cached_get_swap_pairs(
        self, cached: Result[dict], chain_identifier: int | str
    ) -> None:
        """Restore ``rfq_pairs`` when ``get_swap_pairs`` returns from cache."""
        if not cached.success or cached.data is None:
            return
        chain_id = self._resolve_chain_id(chain_identifier)
        if chain_id is None:
            return
        if not self.rfq_pairs:
            self.rfq_pairs = {}
        self.rfq_pairs[chain_id] = cached.data

    def _transform_quote_from_api(self, quote: dict) -> dict:
        """Normalize a Dexalot firm-quote response to snake_case keys.

        The HTTP response wraps the executable firm quote inside
        ``{"success": true, "quote": {...}}``.  Unwrap so downstream code
        operates on the inner dict, then apply snake_case aliases for
        top-level identifiers and normalize the inner ``order`` dict.

        After unwrapping, this helper:

        * Adds ``chain_id`` and ``quote_id`` snake_case aliases for the camelCase
          (or lowercase) identifiers the API may emit.
        * Runs ``_transform_order_data_from_api`` over ``quote["order"]`` so nested
          fields like ``nonceAndMeta``/``makerAsset``/``takerAmount`` gain
          snake_case aliases.

        Original keys are preserved; nothing is popped or renamed.

        Args:
            quote: Raw quote dict from API response.  May be the full envelope
                ``{"success": true, "quote": {...}}`` or the already-inner dict.

        Returns:
            Transformed inner-quote dict with snake_case aliases added.
        """
        if isinstance(quote, dict) and "quote" in quote and isinstance(quote["quote"], dict):
            quote = quote["quote"]

        transformed = dict(quote)

        # Map chain_id: prefer existing snake_case, fallback to lowercase/camelCase.
        if "chain_id" not in transformed:
            if "chainid" in quote:
                transformed["chain_id"] = quote["chainid"]
            elif "chainId" in quote:
                transformed["chain_id"] = quote["chainId"]

        # Map quote_id: prefer existing snake_case, fallback to lowercase/camelCase.
        if "quote_id" not in transformed:
            if "quoteid" in quote:
                transformed["quote_id"] = quote["quoteid"]
            elif "quoteId" in quote:
                transformed["quote_id"] = quote["quoteId"]

        # Normalize the inner order dict so downstream code can read snake_case keys.
        if "order" in transformed:
            transformed["order"] = self._transform_order_data_from_api(transformed["order"])

        return transformed

    def _transform_order_data_from_api(self, order_data: dict) -> dict:
        """Transform order data object fields to snake_case.

        Args:
            order_data: Raw order data dict from API response

        Returns:
            Transformed order data dict with standardized field names
        """
        if not order_data:
            return order_data

        transformed = dict(order_data)

        # Map nonce_and_meta: prefer existing snake_case, fallback to camelCase
        if "nonce_and_meta" not in transformed:
            if "nonceAndMeta" in order_data:
                transformed["nonce_and_meta"] = order_data["nonceAndMeta"]

        # Map maker_asset: prefer existing snake_case, fallback to camelCase
        if "maker_asset" not in transformed:
            if "makerAsset" in order_data:
                transformed["maker_asset"] = order_data["makerAsset"]

        # Map taker_asset: prefer existing snake_case, fallback to camelCase
        if "taker_asset" not in transformed:
            if "takerAsset" in order_data:
                transformed["taker_asset"] = order_data["takerAsset"]

        # Map maker_amount: prefer existing snake_case, fallback to camelCase
        if "maker_amount" not in transformed:
            if "makerAmount" in order_data:
                transformed["maker_amount"] = order_data["makerAmount"]

        # Map taker_amount: prefer existing snake_case, fallback to camelCase
        if "taker_amount" not in transformed:
            if "takerAmount" in order_data:
                transformed["taker_amount"] = order_data["takerAmount"]

        return transformed

    async def _get_swap_quote_base(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        chain_id: int | None = 43114,
        firm: bool = False,
    ) -> Result[dict]:
        """Internal helper to request a quote from the RFQ API.

        Args:
            from_token: Symbol of the token to sell.
            to_token: Symbol of the token to buy.
            amount: Amount of ``from_token`` in human-readable units.
            chain_id: Numeric chain ID (defaults to Avalanche C-Chain, 43114).
            firm: If ``True``, request a firm (executable, signed) quote via
                ``firmquote`` endpoint.  If ``False``, request an indicative
                (soft) quote via ``pairprice`` endpoint.

        Returns:
            Result containing a normalised quote dict on success, or an error
            message on failure.
        """
        chain_identifier = chain_id if chain_id is not None else self.chain_id
        resolved_chain_result = self._resolve_chain_id_result(chain_identifier)
        if not resolved_chain_result.success or resolved_chain_result.data is None:
            return Result.fail(
                resolved_chain_result.error
                or f"Could not resolve chain identifier '{chain_identifier}' to a Chain ID."
            )
        chain_id_int = resolved_chain_result.data

        target_pair, trade_side, is_base = await self._resolve_pair(
            from_token, to_token, chain_id_int
        )

        if not target_pair:
            return Result.fail(f"Pair {from_token}/{to_token} not found in RFQ or CLOB pairs.")

        # Choose endpoint
        endpoint = "firmquote" if firm else "pairprice"

        params = {
            "chainid": str(chain_id_int),
            "pair": target_pair["pair"],
            "amount": str(amount),
            "isbase": "1" if is_base else "0",
            "side": str(trade_side),
        }

        if firm:
            endpoint = ENDPOINT_RFQ_FIRM_QUOTE
            params["address"] = (
                cast(str, cast(Any, self.account).address)
                if self.account
                else DEFAULT_TAKER_ADDRESS
            )

        else:
            endpoint = ENDPOINT_RFQ_PAIR_PRICE
            params["taker"] = (
                cast(str, cast(Any, self.account).address)
                if self.account
                else DEFAULT_TAKER_ADDRESS
            )

        url = f"{self.api_base_url}{endpoint}"

        try:
            async with await self._make_http_request("get", url, params=params) as response:
                if response.status == 200:
                    quote_data = await response.json()
                    # Envelope-layer failure: Dexalot RFQ returns
                    # ``{"success": false, "reason": "..."}`` on logical failure
                    # even with HTTP 200.  Surface that as a Result.fail before
                    # handing the payload to the shape-mapping transform.
                    if isinstance(quote_data, dict) and quote_data.get("success") is False:
                        reason = (
                            quote_data.get("reason")
                            or quote_data.get("error")
                            or "Quote API returned success=false"
                        )
                        return Result.fail(f"Cannot execute failed quote: {reason}")
                    transformed_quote = self._transform_quote_from_api(quote_data)
                    return Result.ok(transformed_quote)
                else:
                    error_text = await response.text()
                    error_msg = self._sanitize_error(
                        Exception(f"HTTP {response.status}: {error_text}"), "fetching quote"
                    )
                    return Result.fail(error_msg)
        except Exception as e:
            error_msg = self._sanitize_error(e, "fetching quote")
            return Result.fail(error_msg)

    def _resolve_chain_id(self, chain_identifier):
        """Resolve chain identifier (int or str) to Chain ID (int)."""
        resolved_chain = self.resolve_chain_reference(chain_identifier)
        if resolved_chain.success and resolved_chain.data is not None:
            return resolved_chain.data.chain_id

        return None

    def _resolve_chain_id_result(self, chain_identifier) -> Result[int]:
        """Resolve chain identifier and preserve user-facing error messages."""
        if chain_identifier is None:
            return Result.fail("Chain identifier is required.")

        resolved_chain = self.resolve_chain_reference(chain_identifier)
        if (
            resolved_chain.success
            and resolved_chain.data is not None
            and resolved_chain.data.chain_id
        ):
            return Result.ok(resolved_chain.data.chain_id)

        return Result.fail(
            resolved_chain.error
            or f"Could not resolve chain identifier '{chain_identifier}' to a Chain ID."
        )

    async def _resolve_pair(self, from_token, to_token, chain_id):
        """
        Resolve swap pair from RFQ or CLOB pairs.
        Returns (target_pair, trade_side, is_base).
        trade_side: 0=Buy, 1=Sell
        is_base: True if from_token is base, False if quote
        """
        pair_str = f"{from_token}/{to_token}"
        rev_pair_str = f"{to_token}/{from_token}"

        # Check RFQ pairs first
        rfq_data = getattr(self, "rfq_pairs", {}).get(chain_id, {})

        if pair_str in rfq_data:
            target_pair = rfq_data[pair_str]
            if "pair" not in target_pair:
                target_pair["pair"] = pair_str
            return target_pair, 1, True  # Sell

        if rev_pair_str in rfq_data:
            target_pair = rfq_data[rev_pair_str]
            if "pair" not in target_pair:
                target_pair["pair"] = rev_pair_str
            return target_pair, 0, False  # Buy

        # Fallback to CLOB pairs (provided by CLOBClient in DexalotClient MRO)
        if not self.pairs:
            await cast(Any, self).get_clob_pairs()

        if pair_str in self.pairs:
            return self.pairs[pair_str], 1, True

        if rev_pair_str in self.pairs:
            return self.pairs[rev_pair_str], 0, False

        return None, 0, False

    @track_method("swap")
    async def get_swap_firm_quote(
        self, from_token: str, to_token: str, amount: float, chain_id: int | None = None
    ) -> Result[dict]:
        """Request a firm (signed, executable) quote for a SimpleSwap.

        A firm quote is cryptographically signed by the market maker and can be
        passed directly to ``execute_rfq_swap()``.  Firm quotes have a limited
        validity window.

        Args:
            from_token: Symbol of the token to sell (e.g. ``"AVAX"``).
            to_token: Symbol of the token to buy (e.g. ``"USDC"``).
            amount: Amount of ``from_token`` to sell in human-readable units.
            chain_id: Numeric chain ID.  Defaults to ``self.chain_id`` (set
                during ``initialize_client()``).

        Returns:
            Result containing a firm quote dict (with top-level ``signature``,
            ``order``, ``tx``, and ``quote_id`` fields) on success, or an error
            message on failure.
        """
        # Validate swap parameters
        swap_params_result = validate_swap_params(from_token, to_token, amount)
        if not swap_params_result.success:
            return cast(Result[dict[Any, Any]], swap_params_result)

        from_token = self._normalize_user_token(from_token)
        to_token = self._normalize_user_token(to_token)

        # Validate chain_id if provided
        if chain_id is not None:
            chain_id_result = validate_chain_identifier(chain_id, "chain_id")
            if not chain_id_result.success:
                return cast(Result[dict[Any, Any]], chain_id_result)

        if chain_id is None:
            chain_id = self.chain_id
        return await self._get_swap_quote_base(from_token, to_token, amount, chain_id, firm=True)

    @track_method("swap")
    async def get_swap_soft_quote(
        self, from_token: str, to_token: str, amount: float, chain_id: int | None = None
    ) -> Result[dict]:
        """Request a soft (indicative) quote for a SimpleSwap.

        A soft quote shows an estimated price but is not signed and cannot be
        executed directly.  Use ``get_swap_firm_quote()`` when you need an
        executable quote.

        Args:
            from_token: Symbol of the token to sell (e.g. ``"AVAX"``).
            to_token: Symbol of the token to buy (e.g. ``"USDC"``).
            amount: Amount of ``from_token`` to sell in human-readable units.
            chain_id: Numeric chain ID.  Defaults to ``self.chain_id``.

        Returns:
            Result containing an indicative quote dict on success, or an error
            message on failure.
        """
        # Validate swap parameters
        swap_params_result = validate_swap_params(from_token, to_token, amount)
        if not swap_params_result.success:
            return cast(Result[dict[Any, Any]], swap_params_result)

        from_token = self._normalize_user_token(from_token)
        to_token = self._normalize_user_token(to_token)

        # Validate chain_id if provided
        if chain_id is not None:
            chain_id_result = validate_chain_identifier(chain_id, "chain_id")
            if not chain_id_result.success:
                return cast(Result[dict[Any, Any]], chain_id_result)

        if chain_id is None:
            chain_id = self.chain_id
        return await self._get_swap_quote_base(from_token, to_token, amount, chain_id, firm=False)

    @track_method("swap")
    async def execute_rfq_swap(self, quote: dict, wait_for_receipt: bool = True) -> Result[dict]:
        """Execute a SimpleSwap using a firm quote from ``get_swap_firm_quote()``.

        Signs and submits ``simpleSwap(order, signature)`` to the contract the
        quote is valid on: the quote's ``tx.to`` (the DexalotRouter, which
        forwards to the maker) when present, otherwise ``order.maker``
        directly.  The legacy ``MainnetRFQ`` address from the deployments
        endpoint is never used as the execution target because firm quotes are
        served by several maker contracts, each with its own signer.

        Before broadcasting, the SDK checks on-chain that ``order.maker`` is a
        maker the router allows, that ``tx.to`` is the router or the maker,
        that any ``tx.data``/``tx.value`` the API returned match the call the
        SDK encodes itself, and, for ERC20 sells, that the maker already holds a
        sufficient allowance (see ``approve_rfq_maker``).

        Args:
            quote: Firm quote dict as returned by ``get_swap_firm_quote()``.
                Also accepts a ``Result[dict]`` directly — if the result is
                unsuccessful, fails immediately.
            wait_for_receipt: If ``True``, block until the swap transaction is
                confirmed on-chain.

        Returns:
            Result with ``tx_hash``, ``target`` (contract called) and ``maker``
            on success, or an error message on failure.  Errors carry a
            ``[target=..., maker=..., quote_id=..., ...]`` suffix so failures
            can be traced back to the quote.

        Raises:
            ValueError: If no signer account is configured.
        """
        if not self.account:
            raise ValueError(
                "Account is required for signing transactions. Set signer or PRIVATE_KEY."
            )
        from_addr = cast(str, cast(Any, self.account).address)

        # Check if quote has error (if it's a Result, check success)
        if isinstance(quote, Result):
            if not quote.success:
                return Result.fail(f"Cannot execute failed quote: {quote.error}")
            if quote.data is None:
                return Result.fail("Invalid quote: empty data")
            quote = quote.data

        # Transform quote to ensure standardized field names.  This also
        # unwraps the ``{"success": true, "quote": {...}}`` envelope when the
        # caller passed in the raw API payload.  Envelope-layer failure
        # (``success: false``) is handled by ``_get_swap_quote_base`` before
        # the transform runs, so it never reaches us here.
        quote_typed: dict[Any, Any] = self._transform_quote_from_api(quote)
        quote = quote_typed

        # Extract signature and order data from the firm-quote dict.
        signature = quote.get("signature")
        order_data = quote.get("order")
        if not signature or not order_data:
            return Result.fail("Invalid firm quote: missing 'signature' or 'order' field.")

        # Resolve w3 and the deployments (legacy) contract for the connected
        # chain carried in the quote.  The contract is only used as the anchor
        # for on-chain target discovery, never as the execution target.
        w3, contract = await self._get_rfq_contract(quote.get("chain_id"))
        if not w3 or not contract:
            return Result.fail("RFQ Contract not found or W3 not initialized.")

        target_res = await self._resolve_rfq_execution_target(w3, contract, quote, order_data)
        if not target_res.success or target_res.data is None:
            return Result.fail(target_res.error or "Could not resolve RFQ execution target.")
        target = target_res.data
        maker = Web3.to_checksum_address(order_data["maker"])
        context = self._rfq_error_context(quote, order_data, target)

        try:
            order_tuple = self._construct_rfq_order(order_data)
            # MainnetRFQ requires msg.value == takerAmount for native sells
            # (takerAsset == zero address), 0 otherwise.
            msg_value = self._compute_msg_value(order_data)
            signature_bytes = self._signature_to_bytes(signature)

            target_contract = w3.eth.contract(address=target, abi=_SIMPLE_SWAP_ABI)
            fn_call = target_contract.functions.simpleSwap(order_tuple, signature_bytes)

            envelope_error = self._check_tx_envelope(
                quote, target_contract, order_tuple, signature_bytes, msg_value
            )
            if envelope_error:
                return Result.fail(f"{envelope_error} [{context}]")

            # ERC20 sells: the maker contract pulls funds with transferFrom, so
            # the allowance must be granted to order.maker (not the router and
            # not the legacy MainnetRFQ).  Fail here with a clear message
            # instead of letting the contract revert.
            taker_asset = str(order_tuple[3])
            if taker_asset.lower() != NATIVE_ZERO_ADDRESS:
                needed = int(order_tuple[7])
                allowance = await self._get_erc20_allowance(w3, taker_asset, from_addr, maker)
                if allowance < needed:
                    return Result.fail(
                        f"Insufficient allowance: RFQ maker {maker} may spend {allowance} of "
                        f"{taker_asset}, swap needs {needed}. Call approve_rfq_maker(quote) first "
                        f"(approvals are per maker contract) [{context}]"
                    )

            tx_hex, tx, receipt = await self._send_swap_tx(
                w3, fn_call, value=msg_value, wait_for_receipt=wait_for_receipt
            )
            if wait_for_receipt:
                failure = await self._describe_receipt_failure(w3, tx, receipt, tx_hex)
                if failure:
                    return Result.fail(f"{failure} [{context}]")

            return Result.ok(
                {
                    "tx_hash": tx_hex,
                    "operation": "execute_rfq_swap",
                    "target": target,
                    "maker": maker,
                }
            )

        except Exception as e:
            error_msg = self._sanitize_error(e, "executing swap")
            return Result.fail(f"{error_msg} [{context}]")

    @track_method("swap")
    async def approve_rfq_maker(
        self, quote: dict, amount_wei: int | None = None, wait_for_receipt: bool = True
    ) -> Result[dict]:
        """Grant the quote's maker contract an ERC20 allowance for the taker asset.

        Firm quotes are served by several maker contracts and each one pulls
        the taker asset with ``transferFrom`` itself, so the allowance has to
        be granted to ``order.maker`` — approving the router or the legacy
        ``MainnetRFQ`` address does nothing for a quote from another maker.
        Call this before ``execute_rfq_swap`` when selling an ERC20 token.

        The maker is validated against the router's on-chain allow-list before
        any approval is sent.  Native-asset sells need no allowance and are
        rejected.

        Args:
            quote: Firm quote dict (or ``Result[dict]``) from
                ``get_swap_firm_quote()``.
            amount_wei: Allowance to grant, in the token's base units.
                Defaults to the quote's ``takerAmount``.
            wait_for_receipt: If ``True``, block until the approval is mined.

        Returns:
            ``Result.ok`` with ``approved=False`` and the current ``allowance``
            when nothing had to be sent, or ``approved=True`` with the approval
            ``tx_hash`` otherwise.

        Raises:
            ValueError: If no signer account is configured.
        """
        if not self.account:
            raise ValueError(
                "Account is required for signing transactions. Set signer or PRIVATE_KEY."
            )
        from_addr = cast(str, cast(Any, self.account).address)

        if isinstance(quote, Result):
            if not quote.success:
                return Result.fail(f"Cannot approve failed quote: {quote.error}")
            if quote.data is None:
                return Result.fail("Invalid quote: empty data")
            quote = quote.data

        quote_typed: dict[Any, Any] = self._transform_quote_from_api(quote)
        quote = quote_typed
        order_data = quote.get("order")
        if not order_data:
            return Result.fail("Invalid firm quote: missing 'order' field.")

        w3, contract = await self._get_rfq_contract(quote.get("chain_id"))
        if not w3 or not contract:
            return Result.fail("RFQ Contract not found or W3 not initialized.")

        taker_asset = str(order_data.get("taker_asset") or order_data.get("takerAsset") or "")
        if not taker_asset:
            return Result.fail("Invalid firm quote: missing 'order.takerAsset' field.")
        if taker_asset.lower() == NATIVE_ZERO_ADDRESS:
            return Result.fail(
                "Native taker asset does not need an allowance; "
                "execute_rfq_swap sends it as msg.value."
            )

        target_res = await self._resolve_rfq_execution_target(w3, contract, quote, order_data)
        if not target_res.success or target_res.data is None:
            return Result.fail(target_res.error or "Could not resolve RFQ execution target.")
        maker = Web3.to_checksum_address(order_data["maker"])
        token = Web3.to_checksum_address(taker_asset)
        needed = (
            amount_wei
            if amount_wei is not None
            else self._to_int(order_data.get("taker_amount") or order_data.get("takerAmount"))
        )
        if needed <= 0:
            return Result.fail("Approval amount must be positive.")
        context = self._rfq_error_context(quote, order_data, maker)

        try:
            allowance = await self._get_erc20_allowance(w3, token, from_addr, maker)
            if allowance >= needed:
                return Result.ok(
                    {
                        "approved": False,
                        "allowance": allowance,
                        "spender": maker,
                        "token": token,
                        "operation": "approve_rfq_maker",
                    }
                )

            token_contract = w3.eth.contract(address=token, abi=_ERC20_ALLOWANCE_ABI)
            fn_call = token_contract.functions.approve(maker, needed)
            tx_hex, tx, receipt = await self._send_swap_tx(
                w3, fn_call, value=0, wait_for_receipt=wait_for_receipt
            )
            if wait_for_receipt:
                failure = await self._describe_receipt_failure(w3, tx, receipt, tx_hex)
                if failure:
                    return Result.fail(f"{failure} [{context}]")

            return Result.ok(
                {
                    "approved": True,
                    "tx_hash": tx_hex,
                    "amount": needed,
                    "spender": maker,
                    "token": token,
                    "operation": "approve_rfq_maker",
                }
            )
        except Exception as e:
            error_msg = self._sanitize_error(e, "approving RFQ maker")
            return Result.fail(f"{error_msg} [{context}]")

    # ------------------------------------------------------------------
    # RFQ execution-target discovery and validation
    # ------------------------------------------------------------------

    async def _call_with_rpc_policy(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        """Run an awaitable RPC-backed call under the rate limiter and retry policy."""
        if self._rpc_rate_limiter:
            await self._rpc_rate_limiter.acquire()

        if self.config.retry_enabled:
            from ..utils.retry import async_retry

            retry_func = async_retry(
                max_attempts=self.config.retry_max_attempts,
                initial_delay=self.config.retry_initial_delay,
                max_delay=self.config.retry_max_delay,
                exponential_base=self.config.retry_exponential_base,
                retry_on_status=self.config.retry_on_status,
                retry_on_exceptions=self.config.retry_on_exceptions,
            )(fn)
            return await retry_func()
        return await fn()

    async def _get_rfq_targets(
        self, w3: Any, deployment_contract: Any, chain_id: int | None = None
    ) -> tuple[str | None, frozenset[str]]:
        """Discover the RFQ router and the maker contracts it forwards to.

        The router address comes from the deployments endpoint
        (``DexalotRouter`` for the chain) when the backend publishes it, and
        otherwise from ``trustedForwarder()`` on the deployments (legacy
        MainnetRFQ) contract.  The allowed makers always come from
        ``getAllowedRFQs()`` on that router — the API does not list them and
        the router's own allow-list is what it enforces.  Returns
        ``(router_address, allowed_makers)`` with lowercase maker addresses;
        the deployments address is always part of the allowed set.

        The result is cached in the static tier (1h) per API base URL and
        deployment address.  Lookup failures are *not* cached and degrade to
        ``(None, {deployment address})`` — the legacy behaviour — so a quote
        from another maker is refused rather than sent to the wrong contract.
        """
        deployment_addr = str(deployment_contract.address)
        cache_key = ("rfq_targets", self.api_base_url, deployment_addr.lower())
        if self._cache_enabled:
            cached = _STATIC_CACHE.get(cache_key)
            if cached is not None:
                return cast(tuple[str | None, frozenset[str]], cached)

        router: str | None = None
        allowed: set[str] = {deployment_addr.lower()}
        try:
            router_addr = self._router_address_from_deployments(chain_id)
            if router_addr is None:
                forwarder_contract = w3.eth.contract(
                    address=Web3.to_checksum_address(deployment_addr),
                    abi=_RFQ_TRUSTED_FORWARDER_ABI,
                )
                forwarder = await self._call_with_rpc_policy(
                    lambda: forwarder_contract.functions.trustedForwarder().call()
                )
                if forwarder and str(forwarder).lower() != NATIVE_ZERO_ADDRESS:
                    router_addr = Web3.to_checksum_address(forwarder)
            if router_addr is not None:
                router_contract = w3.eth.contract(address=router_addr, abi=_ROUTER_ALLOWED_RFQS_ABI)
                makers = await self._call_with_rpc_policy(
                    lambda: router_contract.functions.getAllowedRFQs().call()
                )
                allowed.update(str(m).lower() for m in makers)
                router = router_addr
        except Exception as exc:
            self.logger.warning(
                "Could not resolve RFQ router/allowed makers via %s (%s); "
                "only the deployments address will be accepted as maker",
                deployment_addr,
                exc,
            )
            return None, frozenset({deployment_addr.lower()})

        result = (router, frozenset(allowed))
        if self._cache_enabled:
            _STATIC_CACHE.set(cache_key, result)
        return result

    async def _resolve_rfq_execution_target(
        self, w3: Any, deployment_contract: Any, quote: dict, order_data: dict
    ) -> Result[str]:
        """Pick and validate the contract ``simpleSwap`` must be sent to.

        * ``order.maker`` must be on the router's allow-list (or be the
          deployments address).
        * If the quote carries ``tx.to`` it must be the router or the maker;
          it is then used as the target so the call follows the API's own
          routing.  Otherwise the maker is called directly.
        """
        maker_raw = order_data.get("maker")
        if not maker_raw:
            return Result.fail("Invalid firm quote: missing 'order.maker' field.")
        try:
            maker = Web3.to_checksum_address(maker_raw)
        except Exception:
            return Result.fail(f"Invalid firm quote: 'order.maker' is not an address: {maker_raw}")

        router, allowed = await self._get_rfq_targets(
            w3, deployment_contract, quote.get("chain_id")
        )
        if maker.lower() not in allowed:
            return Result.fail(
                f"Firm quote maker {maker} is not an allowed RFQ contract "
                f"(router={router or 'unknown'}); refusing to execute"
            )

        tx_meta = quote.get("tx")
        tx_to = tx_meta.get("to") if isinstance(tx_meta, dict) else None
        if not tx_to:
            return Result.ok(maker)
        try:
            tx_to_cs = Web3.to_checksum_address(tx_to)
        except Exception:
            return Result.fail(f"Invalid firm quote: 'tx.to' is not an address: {tx_to}")
        permitted = {maker.lower()}
        if router:
            permitted.add(router.lower())
        if tx_to_cs.lower() not in permitted:
            return Result.fail(
                f"Firm quote tx.to {tx_to_cs} is neither the RFQ router nor the order maker "
                f"{maker}; refusing to execute"
            )
        return Result.ok(tx_to_cs)

    def _check_tx_envelope(
        self,
        quote: dict,
        target_contract: Any,
        order_tuple: tuple,
        signature_bytes: bytes,
        msg_value: int,
    ) -> str | None:
        """Cross-check the API's ``tx`` envelope against the call the SDK encodes.

        The SDK always builds the calldata itself; the envelope is only used to
        detect disagreement.  Returns an error message when ``tx.data`` or
        ``tx.value`` are present and differ, ``None`` otherwise.
        """
        raw_tx = quote.get("tx")
        tx_meta: dict[str, Any] = raw_tx if isinstance(raw_tx, dict) else {}

        api_data = tx_meta.get("data")
        if api_data:
            expected = self._encode_simple_swap(target_contract, order_tuple, signature_bytes)
            if str(api_data).lower() != expected.lower():
                return (
                    "Firm quote tx.data does not match the SDK-encoded simpleSwap call; "
                    "refusing to execute"
                )

        api_value = tx_meta.get("value")
        if api_value not in (None, "") and self._to_int(api_value) != msg_value:
            return (
                f"Firm quote tx.value {api_value} does not match the computed msg.value "
                f"{msg_value}; refusing to execute"
            )
        return None

    async def _get_erc20_allowance(self, w3: Any, token: str, owner: str, spender: str) -> int:
        """Return ``allowance(owner, spender)`` of an ERC20 token in base units."""
        token_contract = w3.eth.contract(
            address=Web3.to_checksum_address(token), abi=_ERC20_ALLOWANCE_ABI
        )
        raw = await self._call_with_rpc_policy(
            lambda: token_contract.functions.allowance(owner, spender).call()
        )
        return int(raw)

    @staticmethod
    def _encode_simple_swap(contract: Any, order_tuple: tuple, signature_bytes: bytes) -> str:
        """ABI-encode ``simpleSwap(order, signature)`` calldata (web3 v7 / v6)."""
        encoder = getattr(contract, "encode_abi", None) or contract.encodeABI
        return str(encoder("simpleSwap", args=[order_tuple, signature_bytes]))

    @staticmethod
    def _signature_to_bytes(signature: Any) -> bytes:
        """Coerce a hex string (with or without ``0x``) or bytes to bytes."""
        if isinstance(signature, str):
            hex_text = signature[2:] if signature.startswith("0x") else signature
            return bytes.fromhex(hex_text)
        return cast(bytes, signature)

    @staticmethod
    def _rfq_error_context(quote: dict, order_data: dict, target: str) -> str:
        """Compact ``key=value`` trail appended to swap errors for diagnosis."""
        parts = [f"target={target}", f"maker={order_data.get('maker')}"]
        quote_id = quote.get("quote_id")
        if quote_id:
            parts.append(f"quote_id={quote_id}")
        nonce_and_meta = order_data.get("nonce_and_meta") or order_data.get("nonceAndMeta")
        if nonce_and_meta not in (None, ""):
            parts.append(f"nonce_and_meta={nonce_and_meta}")
        expiry = order_data.get("expiry")
        if expiry not in (None, ""):
            parts.append(f"expiry={expiry}")
        return ", ".join(parts)

    async def _send_swap_tx(
        self, w3: Any, fn_call: Any, *, value: int, wait_for_receipt: bool
    ) -> tuple[str, dict, Any]:
        """Estimate gas, build, sign and broadcast a contract call.

        Returns ``(tx_hex, tx, receipt)``; ``receipt`` is ``None`` unless
        ``wait_for_receipt`` is set.
        """
        if not self.account:
            raise ValueError(
                "Account is required for signing transactions. Set signer or PRIVATE_KEY."
            )
        from_addr = cast(str, cast(Any, self.account).address)

        nonce = await self._get_nonce(w3)
        gas_estimate = await self._estimate_function_gas(
            fn_call, {"from": from_addr, "value": value}
        )
        gas_price = await self._rpc_call(w3, "eth.gas_price")

        tx = await fn_call.build_transaction(
            {
                "from": from_addr,
                "nonce": nonce,
                "gas": int(gas_estimate * SWAP_GAS_BUFFER),
                "gasPrice": gas_price,
                "value": value,
            }
        )

        # Use Account instance method - never expose private key
        signed_tx = self.account.sign_transaction(tx)
        tx_hash = await self._rpc_call(w3, "eth.send_raw_transaction", signed_tx.raw_transaction)
        tx_hex = w3.to_hex(tx_hash)

        receipt = None
        if wait_for_receipt:
            receipt = await self._rpc_call(w3, "eth.wait_for_transaction_receipt", tx_hash)
        return tx_hex, tx, receipt

    async def _describe_receipt_failure(
        self, w3: Any, tx: dict, receipt: Any, tx_hex: str
    ) -> str | None:
        """Return a ``Transaction reverted: ...`` message, or ``None`` on success."""
        receipt_status = (
            receipt.status
            if hasattr(receipt, "status")
            else receipt.get("status", 1)
            if receipt
            else 1
        )
        if receipt_status == 1:
            return None

        revert_reason = await self._extract_revert_reason(w3, tx, receipt)
        block_number = (
            receipt.get("blockNumber")
            if isinstance(receipt, dict)
            else getattr(receipt, "blockNumber", None)
        )
        detail_parts = [f"tx={tx_hex}"]
        if block_number is not None:
            detail_parts.append(f"block={block_number}")
        if revert_reason:
            detail_parts.append(f"reason={revert_reason}")
        return f"Transaction reverted: {', '.join(detail_parts)}"

    async def _extract_revert_reason(self, w3: Any, tx: dict, receipt: Any) -> str | None:
        """Best-effort extraction of the on-chain revert reason for a failed tx.

        Re-runs the original transaction as ``eth_call`` against the block in
        which it reverted; the node returns the revert message (e.g.
        ``execution reverted: RF-EXP-01``) which is otherwise dropped from
        the receipt. Returns ``None`` if the call cannot be replayed or the
        node refuses to surface a reason.
        """
        try:
            block_number = (
                receipt.get("blockNumber")
                if isinstance(receipt, dict)
                else getattr(receipt, "blockNumber", None)
            )
            call_tx = {
                "from": tx.get("from"),
                "to": tx.get("to"),
                "data": tx.get("data"),
                "value": tx.get("value", 0),
                "gas": tx.get("gas"),
            }
            call_tx = {k: v for k, v in call_tx.items() if v is not None}
            try:
                await w3.eth.call(call_tx, block_identifier=block_number)
            except Exception as call_exc:
                msg = str(call_exc)
                marker = "execution reverted"
                if marker in msg:
                    after = msg.split(marker, 1)[1].lstrip(" :")
                    after = after.strip().strip("'").strip('"')
                    return after or marker
                return msg[:200] or None
            return None
        except Exception:
            return None

    async def _get_rfq_contract(self, chain_id: int | None = None):
        """Resolve the deployments MainnetRFQ contract and W3 for the target chain.

        RFQ SimpleSwap executes on the connected chain (e.g. Avalanche
        C-Chain for ``chain_id`` 43114), not on Dexalot L1.  The ``chain_id``
        argument selects which connected chain to route to; when ``None``,
        falls back to ``self.chain_id``.

        The returned contract is the legacy MainnetRFQ from the deployments
        endpoint.  It anchors router/maker discovery (``_get_rfq_targets``)
        but is not itself the execution target — see ``execute_rfq_swap``.
        """
        if "MainnetRFQ" not in self.deployments:
            return None, None

        target_chain_id = chain_id if chain_id is not None else self.chain_id
        if target_chain_id is None:
            return None, None

        rfq_deployments = self.deployments["MainnetRFQ"]

        chain_name = self._chain_name_for_id(target_chain_id)

        # Pick the deployment for the resolved chain.  Deployments may be
        # keyed by chain name or by chain_id; check both.
        deployment = None
        if chain_name is not None and chain_name in rfq_deployments:
            deployment = rfq_deployments[chain_name]
        elif target_chain_id in rfq_deployments:
            deployment = rfq_deployments[target_chain_id]
            chain_name = chain_name or str(target_chain_id)
        elif str(target_chain_id) in rfq_deployments:
            deployment = rfq_deployments[str(target_chain_id)]
            chain_name = chain_name or str(target_chain_id)

        if not deployment or "address" not in deployment:
            return None, None

        # chain_name is guaranteed non-None here: each branch above either
        # matches on chain_name or assigns it from target_chain_id.
        w3 = self.connected_chain_providers.get(cast(str, chain_name))
        if w3 is None:
            return None, None

        contract_address = deployment["address"]
        abi = deployment.get("abi", [])
        contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi)
        return w3, contract

    def _chain_name_for_id(self, chain_id: int | None) -> str | None:
        """Reverse-resolve a connected-chain id to its ``chain_config`` name."""
        if chain_id is None:
            return None
        for name, cfg in (self.chain_config or {}).items():
            if cfg.get("chain_id") == chain_id:
                return name
        return None

    def _router_address_from_deployments(self, chain_id: int | None) -> str | None:
        """Return the ``DexalotRouter`` address the deployments endpoint published
        for *chain_id* (checksummed), or ``None`` when the backend did not list it."""
        routers = self.deployments.get("DexalotRouter") or {}
        target_chain_id = chain_id if chain_id is not None else self.chain_id
        chain_name = self._chain_name_for_id(target_chain_id)
        entry = routers.get(chain_name) if chain_name is not None else None
        if entry is None and target_chain_id is not None:
            entry = routers.get(target_chain_id) or routers.get(str(target_chain_id))
        address = (entry or {}).get("address")
        if not address:
            return None
        try:
            return Web3.to_checksum_address(address)
        except Exception:
            self.logger.warning("Ignoring malformed DexalotRouter deployment address %r", address)
            return None

    async def _estimate_swap_gas(self, contract, order_tuple, signature_bytes, msg_value: int = 0):
        """Estimate gas for swap transaction with retry/rate limiting.

        ``msg_value`` mirrors the ``value`` field of the eventual transaction
        so the estimator sees the same call shape MainnetRFQ will validate
        (native sells require ``msg.value == takerAmount``).
        """
        if not self.account:
            raise ValueError("Account is required for gas estimation.")
        from_addr = cast(str, cast(Any, self.account).address)

        fn_call = contract.functions.simpleSwap(order_tuple, signature_bytes)
        return await self._estimate_function_gas(fn_call, {"from": from_addr, "value": msg_value})

    async def _estimate_function_gas(self, fn_call: Any, tx_params: dict) -> int:
        """``estimate_gas`` for a bound contract call under the RPC rate/retry policy."""
        estimate = await self._call_with_rpc_policy(lambda: fn_call.estimate_gas(tx_params))
        return cast(int, estimate)

    @staticmethod
    def _compute_msg_value(order_data: dict) -> int:
        """Return the wei msg.value to attach to a SimpleSwap call.

        The MainnetRFQ contract requires ``msg.value == takerAmount`` when the
        taker is sending the chain's native token (``takerAsset`` is the zero
        address), and ``msg.value == 0`` otherwise.
        """
        taker_asset = order_data.get("taker_asset") or order_data.get("takerAsset") or ""
        if taker_asset.lower() == NATIVE_ZERO_ADDRESS:
            return SwapClient._to_int(
                order_data.get("taker_amount") or order_data.get("takerAmount")
            )
        return 0

    @staticmethod
    def _to_int(value: Any) -> int:
        """Coerce an order field to int, accepting decimal or 0x-hex strings.

        ``nonceAndMeta`` arrives as a 0x-prefixed hex string; ``makerAmount`` /
        ``takerAmount`` as decimal strings; ``expiry`` as a JSON number.
        """
        if value is None or value == "":
            return 0
        if isinstance(value, int):
            return value
        text = str(value)
        return int(text, 16) if text.lower().startswith("0x") else int(text)

    def _construct_rfq_order(self, order_data):
        """Construct the Order tuple from order data dictionary."""
        # ABI: simpleSwap((nonceAndMeta, expiry, makerAsset, takerAsset, maker, taker, makerAmount, takerAmount), signature)
        # Use snake_case field names (transformed from API)
        maker_asset = order_data.get("maker_asset") or order_data.get("makerAsset")
        taker_asset = order_data.get("taker_asset") or order_data.get("takerAsset")
        return (
            self._to_int(order_data.get("nonce_and_meta") or order_data.get("nonceAndMeta")),
            self._to_int(order_data.get("expiry")),
            Web3.to_checksum_address(maker_asset),
            Web3.to_checksum_address(taker_asset),
            Web3.to_checksum_address(order_data["maker"]),
            Web3.to_checksum_address(order_data["taker"]),
            self._to_int(order_data.get("maker_amount") or order_data.get("makerAmount")),
            self._to_int(order_data.get("taker_amount") or order_data.get("takerAmount")),
        )
