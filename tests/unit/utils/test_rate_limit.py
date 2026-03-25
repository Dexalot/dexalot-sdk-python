import asyncio
import time

import pytest

from dexalot_sdk.utils.rate_limit import AsyncRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_basic():
    """Test basic rate limiting - ensures minimum interval between calls."""
    limiter = AsyncRateLimiter(calls_per_second=10.0)  # 100ms between calls

    start = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - start

    # Should have waited at least 100ms between calls
    assert elapsed >= 0.1
    assert elapsed < 0.2  # But not too much more


@pytest.mark.asyncio
async def test_rate_limiter_respects_rate():
    """Test that rate limiter enforces the specified rate."""
    limiter = AsyncRateLimiter(calls_per_second=2.0)  # 500ms between calls

    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    elapsed = time.monotonic() - start

    # 3 calls with 2 intervals = at least 1 second
    assert elapsed >= 1.0
    assert elapsed < 1.5  # Allow some tolerance


@pytest.mark.asyncio
async def test_rate_limiter_concurrent_calls():
    """Test that concurrent calls are properly rate limited.

    With the lock-before-sleep fix, callers sleep independently in parallel
    rather than queueing behind the lock. Total elapsed time is still
    determined by the rate, but individual completions may overlap.
    """
    limiter = AsyncRateLimiter(calls_per_second=10.0)  # 100ms between calls

    # Make 5 concurrent calls
    start = time.monotonic()
    await asyncio.gather(*[limiter.acquire() for _ in range(5)])
    elapsed = time.monotonic() - start

    # Should have taken at least 400ms (4 intervals of 100ms) but not much more
    assert elapsed >= 0.4
    assert elapsed < 0.7


@pytest.mark.asyncio
async def test_rate_limiter_concurrent_throughput():
    """Benchmark: 10 concurrent calls at 5 rps must complete in ~2s, not ~10s.

    This is the acceptance criterion for P-1. Before the fix, holding the lock
    during sleep caused O(N) serialization; after the fix callers sleep in
    parallel windows and total time is O(N/rate).
    """
    limiter = AsyncRateLimiter(calls_per_second=5.0)  # 200ms between calls

    start = time.monotonic()
    await asyncio.gather(*[limiter.acquire() for _ in range(10)])
    elapsed = time.monotonic() - start

    # 10 calls at 5 rps = 9 intervals = 1.8s; allow generous upper bound
    assert elapsed >= 1.8
    assert elapsed < 3.0  # would be ~10s with the old serialized implementation


@pytest.mark.asyncio
async def test_rate_limiter_no_wait_if_enough_time_passed():
    """Test that limiter doesn't wait if enough time has passed."""
    limiter = AsyncRateLimiter(calls_per_second=10.0)  # 100ms between calls

    # First call
    await limiter.acquire()

    # Wait longer than the interval
    await asyncio.sleep(0.15)

    # Second call should not wait
    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start

    # Should be very fast (no waiting)
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_rate_limiter_reset():
    """Test that reset clears the internal state."""
    limiter = AsyncRateLimiter(calls_per_second=10.0)

    # Make a call
    await limiter.acquire()

    # Reset
    limiter.reset()

    # Next call should not wait (reset cleared last_call)
    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 0.05


def test_rate_limiter_invalid_rate():
    """Test that invalid rate raises ValueError."""
    with pytest.raises(ValueError, match="calls_per_second must be positive"):
        AsyncRateLimiter(calls_per_second=0)

    with pytest.raises(ValueError, match="calls_per_second must be positive"):
        AsyncRateLimiter(calls_per_second=-1)


@pytest.mark.asyncio
async def test_rate_limiter_high_rate():
    """Test rate limiter with high rate (many calls per second)."""
    limiter = AsyncRateLimiter(calls_per_second=100.0)  # 10ms between calls

    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - start

    # 5 calls with 4 intervals of 10ms = at least 40ms
    assert elapsed >= 0.04
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_rate_limiter_low_rate():
    """Test rate limiter with low rate (few calls per second)."""
    limiter = AsyncRateLimiter(calls_per_second=1.0)  # 1 second between calls

    start = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - start

    # Should have waited at least 1 second
    assert elapsed >= 1.0
    assert elapsed < 1.2
