import asyncio
import time


class AsyncRateLimiter:
    """Token bucket rate limiter for async operations.

    Ensures that operations are spaced out by at least min_interval seconds.
    Uses a simple token bucket algorithm where tokens are consumed immediately
    and regenerated at a fixed rate.
    """

    def __init__(self, calls_per_second: float):
        """
        Initialize the rate limiter.

        Args:
            calls_per_second: Maximum number of calls allowed per second.
                              Must be positive.
        """
        if calls_per_second <= 0:
            raise ValueError("calls_per_second must be positive")

        self.min_interval = 1.0 / calls_per_second
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        """
        Acquire a token, waiting if necessary to maintain the rate limit.

        Computes the required sleep time under the lock and speculatively
        advances _last_call to reserve the slot, then releases the lock before
        sleeping. This allows concurrent callers to each sleep independently
        rather than queuing behind the lock for the full sleep duration.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
            else:
                sleep_time = 0.0
            self._last_call = now + sleep_time

        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

    def reset(self):
        """Reset the rate limiter (useful for testing or manual control)."""
        self._last_call = 0.0
