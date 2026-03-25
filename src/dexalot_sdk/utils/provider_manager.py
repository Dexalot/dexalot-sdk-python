import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from web3 import AsyncHTTPProvider, AsyncWeb3

if TYPE_CHECKING:
    from ..core.config import DexalotConfig


@dataclass
class ProviderHealth:
    """Tracks health status of a single RPC provider."""

    failure_count: int = 0
    last_failure_time: float | None = None
    is_healthy: bool = True

    def mark_failure(self, cooldown_seconds: int) -> None:
        """Mark this provider as failed."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        # Mark unhealthy if failures exceed threshold (handled by manager)

    def mark_success(self) -> None:
        """Mark this provider as successful (reset failure count)."""
        self.failure_count = 0
        self.last_failure_time = None
        self.is_healthy = True

    def can_retry(self, cooldown_seconds: int, max_failures: int) -> bool:
        """Check if this provider can be retried after cooldown period."""
        if self.failure_count == 0:
            return True
        if self.failure_count >= max_failures:
            return False
        if self.last_failure_time is None:
            return True
        elapsed = time.monotonic() - self.last_failure_time
        return elapsed >= cooldown_seconds


class ProviderManager:
    """
    Manages multiple RPC providers per chain with automatic failover.

    Features:
    - Multiple providers per chain (primary + fallbacks)
    - Fail-fast failover strategy
    - Health tracking (failure counts, last failure time)
    - Automatic provider recovery after cooldown period
    - Thread-safe with asyncio.Lock per chain
    """

    def __init__(self, config: "DexalotConfig"):
        """
        Initialize ProviderManager.

        Args:
            config: DexalotConfig instance with failover settings
        """
        self.config = config
        self._providers: dict[
            str, list[AsyncWeb3]
        ] = {}  # chain_name -> [w3_primary, w3_fallback1, ...]
        self._provider_urls: dict[str, list[str]] = {}  # chain_name -> [url1, url2, ...]
        self._health: dict[str, list[ProviderHealth]] = {}  # chain_name -> [health1, health2, ...]
        self._locks: dict[str, asyncio.Lock] = {}  # Per-chain locks for thread safety
        self._current_provider_index: dict[str, int] = {}  # chain_name -> current provider index

    async def add_providers(self, chain_name: str, rpc_urls: list[str]) -> None:
        """
        Add multiple providers for a chain (primary first, fallbacks after).

        Args:
            chain_name: Name of the chain (e.g., "Avalanche", "DEXALOT_L1")
            rpc_urls: List of RPC endpoint URLs (primary first)
        """
        if not rpc_urls:
            return

        # Get or create lock for this chain
        if chain_name not in self._locks:
            self._locks[chain_name] = asyncio.Lock()

        async with self._locks[chain_name]:
            # Create AsyncWeb3 instances for each URL
            providers = []
            health_statuses = []

            for url in rpc_urls:
                try:
                    provider = AsyncWeb3(AsyncHTTPProvider(url))
                    providers.append(provider)
                    health_statuses.append(ProviderHealth())
                except Exception:
                    # If provider creation fails, skip it but log
                    continue

            if providers:
                self._providers[chain_name] = providers
                self._provider_urls[chain_name] = rpc_urls[: len(providers)]
                self._health[chain_name] = health_statuses
                self._current_provider_index[chain_name] = 0

    async def get_provider(self, chain_name: str) -> AsyncWeb3 | None:
        """
        Get the best available provider for a chain.

        Strategy: Fail-fast - returns the first healthy provider starting from current index.
        If current provider is unhealthy, tries next providers in order.

        Fast path: when the current provider is healthy, return it without acquiring the lock.
        Slow path: acquire the per-chain lock to perform failover selection.

        Args:
            chain_name: Name of the chain

        Returns:
            AsyncWeb3 instance if available, None if all providers are exhausted
        """
        providers = self._providers.get(chain_name)
        if not providers:
            return None

        # Fast path: no lock needed when the current provider is healthy.
        # asyncio is single-threaded; no await between the reads below, so
        # there is no yield point where state could change under us.
        current_index = self._current_provider_index.get(chain_name, 0)
        health = self._health[chain_name][current_index]
        if health.is_healthy and health.can_retry(
            self.config.provider_failover_cooldown,
            self.config.provider_failover_max_failures,
        ):
            return providers[current_index]

        # Slow path: need failover — acquire the lock.
        if chain_name not in self._locks:
            self._locks[chain_name] = asyncio.Lock()

        async with self._locks[chain_name]:
            health_statuses = self._health[chain_name]
            current_index = self._current_provider_index.get(chain_name, 0)

            # Try providers starting from current index
            for i in range(len(providers)):
                index = (current_index + i) % len(providers)
                health = health_statuses[index]

                if health.can_retry(
                    self.config.provider_failover_cooldown,
                    self.config.provider_failover_max_failures,
                ):
                    # Restore healthy flag if the provider has recovered
                    if (
                        not health.is_healthy
                        and health.failure_count < self.config.provider_failover_max_failures
                    ):
                        health.is_healthy = True

                    self._current_provider_index[chain_name] = index
                    return providers[index]

            # All providers exhausted
            return None

    async def mark_failure(self, chain_name: str, provider_index: int) -> None:
        """
        Mark a provider as failed and potentially switch to next provider.

        Args:
            chain_name: Name of the chain
            provider_index: Index of the failed provider
        """
        if chain_name not in self._health:
            return

        # Get or create lock for this chain
        if chain_name not in self._locks:
            self._locks[chain_name] = asyncio.Lock()

        async with self._locks[chain_name]:
            if provider_index < len(self._health[chain_name]):
                health = self._health[chain_name][provider_index]
                health.mark_failure(self.config.provider_failover_cooldown)

                # Mark unhealthy if failures exceed threshold
                if health.failure_count >= self.config.provider_failover_max_failures:
                    health.is_healthy = False

                # If this was the current provider, try to switch to next
                if self._current_provider_index.get(chain_name) == provider_index:
                    # Try to find next healthy provider
                    providers = self._providers.get(chain_name, [])
                    for i in range(len(providers)):
                        next_index = (provider_index + i + 1) % len(providers)
                        next_health = self._health[chain_name][next_index]
                        if next_health.can_retry(
                            self.config.provider_failover_cooldown,
                            self.config.provider_failover_max_failures,
                        ):
                            self._current_provider_index[chain_name] = next_index
                            break

    async def mark_success(self, chain_name: str, provider_index: int) -> None:
        """
        Mark a provider as successful (reset failure count).

        Args:
            chain_name: Name of the chain
            provider_index: Index of the successful provider
        """
        if chain_name not in self._health:
            return

        # Get or create lock for this chain
        if chain_name not in self._locks:
            self._locks[chain_name] = asyncio.Lock()

        async with self._locks[chain_name]:
            if provider_index < len(self._health[chain_name]):
                health = self._health[chain_name][provider_index]
                health.mark_success()

    def get_provider_index(self, chain_name: str, w3: AsyncWeb3) -> int | None:
        """
        Get the index of a provider instance for a chain.

        Args:
            chain_name: Name of the chain
            w3: AsyncWeb3 instance to find

        Returns:
            Provider index if found, None otherwise
        """
        if chain_name not in self._providers:
            return None

        providers = self._providers[chain_name]
        for i, provider in enumerate(providers):
            if provider == w3:
                return i
        return None

    def get_provider_count(self, chain_name: str) -> int:
        """
        Get the number of providers for a chain.

        Args:
            chain_name: Name of the chain

        Returns:
            Number of providers, or 0 if chain not found
        """
        return len(self._providers.get(chain_name, []))
