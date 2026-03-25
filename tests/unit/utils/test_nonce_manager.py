import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from dexalot_sdk.utils.nonce_manager import AsyncNonceManager


class TestAsyncNonceManager:
    @pytest.fixture
    def manager(self):
        return AsyncNonceManager()

    @pytest.fixture
    def mock_w3(self):
        w3 = MagicMock()

        # chain_id is a property that returns an awaitable - use a function that creates new coroutine each time
        def make_chain_id(value=43114):
            async def chain_id():
                return value

            return chain_id()

        # Make chain_id a property that returns a new coroutine each time
        type(w3.eth).chain_id = property(lambda self: make_chain_id(43114))
        w3.eth.get_transaction_count = AsyncMock(return_value=5)
        w3.to_checksum_address = lambda addr: addr  # Return address as-is for tests
        return w3

    async def test_get_nonce_initializes_from_chain(self, manager, mock_w3):
        """Test that nonce is fetched from chain on first use."""
        address = "0x1234567890123456789012345678901234567890"
        nonce = await manager.get_nonce(mock_w3, address)

        assert nonce == 5
        mock_w3.eth.get_transaction_count.assert_called_once_with(address, "pending")

    async def test_get_nonce_increments_locally(self, manager, mock_w3):
        """Test that nonce increments locally after initialization."""
        address = "0x1234567890123456789012345678901234567890"

        # First call - fetches from chain
        nonce1 = await manager.get_nonce(mock_w3, address)
        assert nonce1 == 5

        # Reset mock to verify it's not called again
        mock_w3.eth.get_transaction_count.reset_mock()

        # Second call - increments locally
        nonce2 = await manager.get_nonce(mock_w3, address)
        assert nonce2 == 6

        # Third call - increments again
        nonce3 = await manager.get_nonce(mock_w3, address)
        assert nonce3 == 7

        # Verify get_transaction_count was not called again
        mock_w3.eth.get_transaction_count.assert_not_called()

    async def test_get_nonce_per_chain_isolation(self, manager):
        """Test that nonces are tracked separately per chain."""
        address = "0x1234567890123456789012345678901234567890"

        def make_chain_id(value):
            async def chain_id():
                return value

            return chain_id()

        w3_chain1 = MagicMock()
        type(w3_chain1.eth).chain_id = property(lambda self: make_chain_id(43114))
        w3_chain1.eth.get_transaction_count = AsyncMock(return_value=10)
        w3_chain1.to_checksum_address = lambda addr: addr

        w3_chain2 = MagicMock()
        type(w3_chain2.eth).chain_id = property(lambda self: make_chain_id(43113))
        w3_chain2.eth.get_transaction_count = AsyncMock(return_value=20)
        w3_chain2.to_checksum_address = lambda addr: addr

        # Get nonce on chain 1
        nonce1_chain1 = await manager.get_nonce(w3_chain1, address)
        assert nonce1_chain1 == 10

        # Get nonce on chain 2 (different chain, should start from 20)
        nonce1_chain2 = await manager.get_nonce(w3_chain2, address)
        assert nonce1_chain2 == 20

        # Increment on chain 1
        nonce2_chain1 = await manager.get_nonce(w3_chain1, address)
        assert nonce2_chain1 == 11

        # Increment on chain 2 (should be independent)
        nonce2_chain2 = await manager.get_nonce(w3_chain2, address)
        assert nonce2_chain2 == 21

    async def test_get_nonce_per_address_isolation(self, manager):
        """Test that nonces are tracked separately per address."""
        address1 = "0x1111111111111111111111111111111111111111"
        address2 = "0x2222222222222222222222222222222222222222"

        def make_chain_id(value):
            async def chain_id():
                return value

            return chain_id()

        mock_w3 = MagicMock()
        type(mock_w3.eth).chain_id = property(lambda self: make_chain_id(43114))
        mock_w3.eth.get_transaction_count = AsyncMock(side_effect=[10, 20])
        mock_w3.to_checksum_address = lambda addr: addr

        # Get nonce for address1
        nonce1_addr1 = await manager.get_nonce(mock_w3, address1)
        assert nonce1_addr1 == 10

        # Get nonce for address2 (should fetch from chain)
        nonce1_addr2 = await manager.get_nonce(mock_w3, address2)
        assert nonce1_addr2 == 20

        # Increment address1
        nonce2_addr1 = await manager.get_nonce(mock_w3, address1)
        assert nonce2_addr1 == 11

        # Increment address2 (should be independent)
        nonce2_addr2 = await manager.get_nonce(mock_w3, address2)
        assert nonce2_addr2 == 21

    async def test_get_nonce_concurrent_access(self, manager):
        """Test that concurrent access to get_nonce is thread-safe."""
        address = "0x1234567890123456789012345678901234567890"

        def make_chain_id(value):
            async def chain_id():
                return value

            return chain_id()

        mock_w3 = MagicMock()
        type(mock_w3.eth).chain_id = property(lambda self: make_chain_id(43114))
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=5)
        mock_w3.to_checksum_address = lambda addr: addr

        # Initialize nonce
        await manager.get_nonce(mock_w3, address)
        mock_w3.eth.get_transaction_count.reset_mock()

        # Create multiple concurrent requests
        async def get_nonce_task():
            return await manager.get_nonce(mock_w3, address)

        # Run 10 concurrent tasks
        tasks = [get_nonce_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All results should be unique and sequential
        assert len(results) == 10
        assert sorted(results) == list(range(6, 16))  # 6 to 15 (after initial 5)

        # Verify get_transaction_count was not called (all used cached nonce)
        mock_w3.eth.get_transaction_count.assert_not_called()

    async def test_reset_nonce(self, manager):
        """Test that reset_nonce fetches fresh value from chain."""
        address = "0x1234567890123456789012345678901234567890"

        def make_chain_id(value):
            async def chain_id():
                return value

            return chain_id()

        mock_w3 = MagicMock()
        type(mock_w3.eth).chain_id = property(lambda self: make_chain_id(43114))
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=5)
        mock_w3.to_checksum_address = lambda addr: addr

        # Initialize and increment
        await manager.get_nonce(mock_w3, address)
        await manager.get_nonce(mock_w3, address)
        mock_w3.eth.get_transaction_count.reset_mock()

        # Update mock to return new value
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=15)

        # Reset nonce
        await manager.reset_nonce(mock_w3, address)

        # Next call should use the reset value
        nonce = await manager.get_nonce(mock_w3, address)
        assert nonce == 16  # 15 + 1 (incremented)

        mock_w3.eth.get_transaction_count.assert_called_once_with(address, "pending")

    async def test_reset_nonce_with_chain_id(self, manager):
        """Test reset_nonce with explicit chain_id."""
        address = "0x1234567890123456789012345678901234567890"
        chain_id = 43114

        w3 = MagicMock()
        w3.eth.get_transaction_count = AsyncMock(return_value=25)
        w3.to_checksum_address = lambda addr: addr

        await manager.reset_nonce(w3, address, chain_id)

        # Verify chain_id was not fetched (since we provided it)
        assert not hasattr(w3.eth, "chain_id") or not w3.eth.chain_id.called

    async def test_clear_nonce(self, manager):
        """Test that clear_nonce removes cached nonce."""
        address = "0x1234567890123456789012345678901234567890"
        chain_id = 43114

        def make_chain_id(value):
            async def chain_id():
                return value

            return chain_id()

        mock_w3 = MagicMock()
        type(mock_w3.eth).chain_id = property(lambda self: make_chain_id(43114))
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=5)
        mock_w3.to_checksum_address = lambda addr: addr

        # Initialize nonce
        await manager.get_nonce(mock_w3, address)
        mock_w3.eth.get_transaction_count.reset_mock()

        # Clear nonce
        manager.clear_nonce(address, chain_id)

        # Next call should fetch from chain again
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=30)
        nonce = await manager.get_nonce(mock_w3, address)
        assert nonce == 30

        mock_w3.eth.get_transaction_count.assert_called_once_with(address, "pending")

    async def test_get_nonce_with_explicit_chain_id(self, manager):
        """Test get_nonce with explicit chain_id parameter."""
        address = "0x1234567890123456789012345678901234567890"
        chain_id = 43114

        w3 = MagicMock()
        w3.eth.get_transaction_count = AsyncMock(return_value=5)
        w3.to_checksum_address = lambda addr: addr

        nonce = await manager.get_nonce(w3, address, chain_id)
        assert nonce == 5

        # Verify chain_id was not fetched (since we provided it)
        assert not hasattr(w3.eth, "chain_id") or not w3.eth.chain_id.called
