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
    """Test that concurrent calls are properly rate limited."""
    limiter = AsyncRateLimiter(calls_per_second=10.0)  # 100ms between calls

    async def make_call():
        await limiter.acquire()
        return time.monotonic()

    # Make 5 concurrent calls
    start = time.monotonic()
    timestamps = await asyncio.gather(*[make_call() for _ in range(5)])
    elapsed = time.monotonic() - start

    # Should have taken at least 400ms (4 intervals of 100ms)
    assert elapsed >= 0.4
    assert elapsed < 0.6

    # Verify calls were serialized (timestamps should be spaced)
    for i in range(1, len(timestamps)):
        interval = timestamps[i] - timestamps[i - 1]
        assert interval >= 0.09  # Allow small tolerance


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
