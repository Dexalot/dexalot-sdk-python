import asyncio

from web3 import AsyncWeb3


class AsyncNonceManager:
    """
    Thread-safe nonce manager for tracking transaction nonces per (chain_id, address) combination.
    Prevents race conditions when multiple transactions are sent concurrently.
    """

    def __init__(self):
        """Initialize the nonce manager."""
        # Maps "{chain_id}:{address}" to current nonce value
        self._nonces: dict[str, int] = {}
        # Per-key locks for thread safety
        self._locks: dict[str, asyncio.Lock] = {}
        # Track if nonce has been fetched from chain
        self._initialized: dict[str, bool] = {}
        # Lock for managing the locks dictionary itself
        self._dict_lock = asyncio.Lock()

    def _get_key(self, address: str, chain_id: int) -> str:
        """Generate a cache key for (chain_id, address) combination."""
        return f"{chain_id}:{address.lower()}"

    async def _get_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for the given key."""
        async with self._dict_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def get_nonce(self, w3: AsyncWeb3, address: str, chain_id: int | None = None) -> int:
        """
        Get the next nonce for the given address on the given chain.

        Args:
            w3: AsyncWeb3 instance
            address: Ethereum address
            chain_id: Chain ID (if None, will be fetched from w3)

        Returns:
            Next nonce to use for the transaction
        """
        # Resolve chain_id if not provided
        if chain_id is None:
            chain_id = await w3.eth.chain_id

        key = self._get_key(address, chain_id)
        lock = await self._get_lock(key)

        async with lock:
            # If not initialized, fetch from chain
            if not self._initialized.get(key, False):
                # Convert address to checksum format for type safety
                checksum_address = w3.to_checksum_address(address)
                nonce = await w3.eth.get_transaction_count(checksum_address, "pending")
                self._nonces[key] = nonce
                self._initialized[key] = True
                return nonce

            # Increment and return
            current_nonce = self._nonces.get(key, 0)
            self._nonces[key] = current_nonce + 1
            return current_nonce + 1

    async def reset_nonce(self, w3: AsyncWeb3, address: str, chain_id: int | None = None) -> None:
        """
        Reset the nonce for the given address on the given chain by fetching from chain.

        Args:
            w3: AsyncWeb3 instance
            address: Ethereum address
            chain_id: Chain ID (if None, will be fetched from w3)
        """
        # Resolve chain_id if not provided
        if chain_id is None:
            chain_id = await w3.eth.chain_id

        key = self._get_key(address, chain_id)
        lock = await self._get_lock(key)

        async with lock:
            # Convert address to checksum format for type safety
            checksum_address = w3.to_checksum_address(address)
            nonce = await w3.eth.get_transaction_count(checksum_address, "pending")
            self._nonces[key] = nonce
            self._initialized[key] = True

    def clear_nonce(self, address: str, chain_id: int) -> None:
        """
        Clear the cached nonce for the given address on the given chain.
        Next call to get_nonce() will fetch from chain.

        Args:
            address: Ethereum address
            chain_id: Chain ID
        """
        key = self._get_key(address, chain_id)
        if key in self._nonces:
            del self._nonces[key]
        if key in self._initialized:
            del self._initialized[key]
        # Note: We keep the lock to avoid recreating it
