import asyncio
import time

import pytest
from web3 import AsyncHTTPProvider, AsyncWeb3

from dexalot_sdk.core.config import DexalotConfig
from dexalot_sdk.utils.provider_manager import ProviderHealth, ProviderManager


class TestProviderHealth:
    """Test ProviderHealth dataclass."""

    def test_initial_state(self):
        """Test initial health state."""
        health = ProviderHealth()
        assert health.failure_count == 0
        assert health.last_failure_time is None
        assert health.is_healthy is True

    def test_mark_failure(self):
        """Test marking a provider as failed."""
        health = ProviderHealth()
        health.mark_failure(cooldown_seconds=60)
        assert health.failure_count == 1
        assert health.last_failure_time is not None
        assert health.is_healthy is True  # Still healthy until max failures

    def test_mark_success(self):
        """Test marking a provider as successful."""
        health = ProviderHealth()
        health.failure_count = 2
        health.last_failure_time = time.monotonic()
        health.is_healthy = False

        health.mark_success()
        assert health.failure_count == 0
        assert health.last_failure_time is None
        assert health.is_healthy is True

    def test_can_retry_no_failures(self):
        """Test can_retry when no failures."""
        health = ProviderHealth()
        assert health.can_retry(cooldown_seconds=60, max_failures=3) is True

    def test_can_retry_below_max(self):
        """Test can_retry when failures below max."""
        health = ProviderHealth()
        health.failure_count = 2
        health.last_failure_time = time.monotonic() - 70  # 70 seconds ago
        assert health.can_retry(cooldown_seconds=60, max_failures=3) is True

    def test_can_retry_above_max(self):
        """Test can_retry when failures exceed max."""
        health = ProviderHealth()
        health.failure_count = 3
        health.last_failure_time = time.monotonic() - 70  # 70 seconds ago
        assert health.can_retry(cooldown_seconds=60, max_failures=3) is False

    def test_can_retry_before_cooldown(self):
        """Test can_retry before cooldown period."""
        health = ProviderHealth()
        health.failure_count = 1
        health.last_failure_time = time.monotonic() - 30  # 30 seconds ago
        assert health.can_retry(cooldown_seconds=60, max_failures=3) is False


class TestProviderManager:
    """Test ProviderManager class."""

    @pytest.fixture
    def config(self):
        """Create a test config."""
        return DexalotConfig(
            provider_failover_enabled=True,
            provider_failover_cooldown=1,  # 1 second for faster tests
            provider_failover_max_failures=3,
        )

    @pytest.fixture
    def manager(self, config):
        """Create a ProviderManager instance."""
        return ProviderManager(config)

    @pytest.mark.asyncio
    async def test_add_providers(self, manager):
        """Test adding providers for a chain."""
        rpc_urls = ["https://rpc1.example.com", "https://rpc2.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        assert "Avalanche" in manager._providers
        assert len(manager._providers["Avalanche"]) == 2
        assert len(manager._provider_urls["Avalanche"]) == 2
        assert len(manager._health["Avalanche"]) == 2

    @pytest.mark.asyncio
    async def test_add_providers_empty_list(self, manager):
        """Test adding empty provider list."""
        await manager.add_providers("Avalanche", [])
        assert "Avalanche" not in manager._providers

    @pytest.mark.asyncio
    async def test_get_provider_primary(self, manager):
        """Test getting primary provider."""
        rpc_urls = ["https://rpc1.example.com", "https://rpc2.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        provider = await manager.get_provider("Avalanche")
        assert provider is not None
        assert isinstance(provider, AsyncWeb3)

    @pytest.mark.asyncio
    async def test_get_provider_not_found(self, manager):
        """Test getting provider for non-existent chain."""
        provider = await manager.get_provider("NonExistent")
        assert provider is None

    @pytest.mark.asyncio
    async def test_mark_failure_and_failover(self, manager):
        """Test marking failure and automatic failover."""
        rpc_urls = ["https://rpc1.example.com", "https://rpc2.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        # Get primary provider
        provider1 = await manager.get_provider("Avalanche")
        provider_index1 = manager.get_provider_index("Avalanche", provider1)

        # Mark failure
        await manager.mark_failure("Avalanche", provider_index1)

        # Get provider again - should get next one
        provider2 = await manager.get_provider("Avalanche")
        provider_index2 = manager.get_provider_index("Avalanche", provider2)

        # Should be different provider
        assert provider_index2 != provider_index1

        # Health should reflect failure
        health1 = manager._health["Avalanche"][provider_index1]
        assert health1.failure_count == 1
        assert health1.last_failure_time is not None

    @pytest.mark.asyncio
    async def test_mark_success_resets_failures(self, manager):
        """Test marking success resets failure count."""
        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        provider = await manager.get_provider("Avalanche")
        provider_index = manager.get_provider_index("Avalanche", provider)

        # Mark failure
        await manager.mark_failure("Avalanche", provider_index)
        health = manager._health["Avalanche"][provider_index]
        assert health.failure_count == 1

        # Mark success
        await manager.mark_success("Avalanche", provider_index)
        assert health.failure_count == 0
        assert health.last_failure_time is None
        assert health.is_healthy is True

    @pytest.mark.asyncio
    async def test_max_failures_marks_unhealthy(self, manager):
        """Test that max failures marks provider as unhealthy."""
        rpc_urls = ["https://rpc1.example.com", "https://rpc2.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        provider = await manager.get_provider("Avalanche")
        provider_index = manager.get_provider_index("Avalanche", provider)

        # Mark failure multiple times
        for _ in range(manager.config.provider_failover_max_failures):
            await manager.mark_failure("Avalanche", provider_index)

        health = manager._health["Avalanche"][provider_index]
        assert health.failure_count == manager.config.provider_failover_max_failures
        assert health.is_healthy is False

    @pytest.mark.asyncio
    async def test_provider_recovery_after_cooldown(self, manager):
        """Test provider recovery after cooldown period."""
        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        provider = await manager.get_provider("Avalanche")
        provider_index = manager.get_provider_index("Avalanche", provider)

        # Mark failure
        await manager.mark_failure("Avalanche", provider_index)
        health = manager._health["Avalanche"][provider_index]
        assert health.failure_count == 1

        # Wait for cooldown
        await asyncio.sleep(1.1)  # Slightly more than cooldown

        # Provider should be available again
        recovered_provider = await manager.get_provider("Avalanche")
        assert recovered_provider is not None
        # Health should still show failure count but provider is retryable
        assert health.can_retry(
            manager.config.provider_failover_cooldown,
            manager.config.provider_failover_max_failures,
        )

    @pytest.mark.asyncio
    async def test_all_providers_exhausted(self, manager):
        """Test when all providers are exhausted."""
        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        provider = await manager.get_provider("Avalanche")
        provider_index = manager.get_provider_index("Avalanche", provider)

        # Mark failure beyond max
        for _ in range(manager.config.provider_failover_max_failures):
            await manager.mark_failure("Avalanche", provider_index)

        # Provider should not be available
        exhausted_provider = await manager.get_provider("Avalanche")
        assert exhausted_provider is None

    @pytest.mark.asyncio
    async def test_thread_safety(self, manager):
        """Test thread safety with concurrent operations."""
        rpc_urls = ["https://rpc1.example.com", "https://rpc2.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        async def concurrent_operation():
            provider = await manager.get_provider("Avalanche")
            if provider:
                index = manager.get_provider_index("Avalanche", provider)
                await manager.mark_success("Avalanche", index)

        # Run multiple concurrent operations
        await asyncio.gather(*[concurrent_operation() for _ in range(10)])

        # Should complete without errors
        provider = await manager.get_provider("Avalanche")
        assert provider is not None

    @pytest.mark.asyncio
    async def test_get_provider_index(self, manager):
        """Test getting provider index."""
        rpc_urls = ["https://rpc1.example.com", "https://rpc2.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        provider = await manager.get_provider("Avalanche")
        index = manager.get_provider_index("Avalanche", provider)
        assert index is not None
        assert 0 <= index < len(rpc_urls)

    @pytest.mark.asyncio
    async def test_get_provider_index_not_found(self, manager):
        """Test getting provider index for non-existent provider."""
        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("Avalanche", rpc_urls)

        fake_provider = AsyncWeb3(AsyncHTTPProvider("https://fake.example.com"))
        index = manager.get_provider_index("Avalanche", fake_provider)
        assert index is None

    @pytest.mark.asyncio
    async def test_get_provider_count(self, manager):
        """Test getting provider count."""
        rpc_urls = [
            "https://rpc1.example.com",
            "https://rpc2.example.com",
            "https://rpc3.example.com",
        ]
        await manager.add_providers("Avalanche", rpc_urls)

        count = manager.get_provider_count("Avalanche")
        assert count == 3

        count_nonexistent = manager.get_provider_count("NonExistent")
        assert count_nonexistent == 0

    def test_provider_health_can_retry_last_failure_none(self):
        """Test ProviderHealth.can_retry when last_failure_time is None."""
        health = ProviderHealth()
        health.failure_count = 1
        health.last_failure_time = None
        assert health.can_retry(cooldown_seconds=60, max_failures=3) is True

    @pytest.mark.asyncio
    async def test_get_provider_creates_lock_on_slow_path(self, manager):
        """Test get_provider creates lock when the slow path (failover) is needed."""
        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("TestChain", rpc_urls)

        # Force the slow path by marking the provider unhealthy
        manager._health["TestChain"][0].is_healthy = False
        if "TestChain" in manager._locks:
            del manager._locks["TestChain"]

        # Slow path runs, lock should be created
        await manager.get_provider("TestChain")
        assert "TestChain" in manager._locks

    @pytest.mark.asyncio
    async def test_get_provider_fast_path_skips_lock(self, manager):
        """Test that get_provider skips lock acquisition when current provider is healthy."""
        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("TestChain", rpc_urls)

        # Remove the lock entirely; a healthy fast-path call must not touch _locks
        if "TestChain" in manager._locks:
            del manager._locks["TestChain"]

        provider = await manager.get_provider("TestChain")
        assert provider is not None
        # Lock should NOT have been created — fast path never acquires it
        assert "TestChain" not in manager._locks

    @pytest.mark.asyncio
    async def test_get_provider_fast_path_concurrent(self, manager):
        """100 concurrent get_provider calls with a healthy provider complete without contention."""
        import time

        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("TestChain", rpc_urls)

        start = time.perf_counter()
        results = await asyncio.gather(*[manager.get_provider("TestChain") for _ in range(100)])
        elapsed = time.perf_counter() - start

        assert all(r is not None for r in results)
        # With fast path, 100 calls should be extremely fast (well under 1 s)
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_get_provider_marks_unhealthy_healthy(self, manager):
        """Test get_provider marks unhealthy provider as healthy when retryable."""
        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("TestChain", rpc_urls)

        # Mark provider as unhealthy but below max failures
        health = manager._health["TestChain"][0]
        health.failure_count = 2  # Below max_failures (3)
        health.is_healthy = False

        provider = await manager.get_provider("TestChain")
        assert provider is not None
        assert health.is_healthy is True

    @pytest.mark.asyncio
    async def test_mark_failure_chain_not_in_health(self, manager):
        """Test mark_failure when chain not in health."""
        # Don't add providers, so chain won't be in _health
        await manager.mark_failure("NonExistent", 0)
        # Should not raise, just return early

    @pytest.mark.asyncio
    async def test_mark_failure_creates_lock(self, manager):
        """Test mark_failure creates lock if it doesn't exist."""
        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("TestChain", rpc_urls)

        # Remove lock to test creation
        if "TestChain" in manager._locks:
            del manager._locks["TestChain"]

        await manager.mark_failure("TestChain", 0)
        assert "TestChain" in manager._locks

    @pytest.mark.asyncio
    async def test_mark_success_chain_not_in_health(self, manager):
        """Test mark_success when chain not in health."""
        # Don't add providers, so chain won't be in _health
        await manager.mark_success("NonExistent", 0)
        # Should not raise, just return early

    @pytest.mark.asyncio
    async def test_mark_success_creates_lock(self, manager):
        """Test mark_success creates lock if it doesn't exist."""
        rpc_urls = ["https://rpc1.example.com"]
        await manager.add_providers("TestChain", rpc_urls)

        # Remove lock to test creation
        if "TestChain" in manager._locks:
            del manager._locks["TestChain"]

        await manager.mark_success("TestChain", 0)
        assert "TestChain" in manager._locks

    def test_get_provider_index_chain_not_found(self, manager):
        """Test get_provider_index when chain not in providers."""
        from web3 import AsyncHTTPProvider, AsyncWeb3

        fake_provider = AsyncWeb3(AsyncHTTPProvider("https://fake.example.com"))
        index = manager.get_provider_index("NonExistent", fake_provider)
        assert index is None
