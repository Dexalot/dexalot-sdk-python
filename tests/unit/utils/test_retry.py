import asyncio
import time

import aiohttp
import pytest

from dexalot_sdk.utils.retry import async_retry


@pytest.mark.asyncio
async def test_retry_success_on_first_attempt():
    """Test that successful function doesn't retry."""
    call_count = 0

    @async_retry(max_attempts=3)
    async def successful_func():
        nonlocal call_count
        call_count += 1
        return "success"

    result = await successful_func()
    assert result == "success"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_exponential_backoff():
    """Test that retry uses exponential backoff."""
    call_count = 0
    call_times = []

    @async_retry(
        max_attempts=4,
        initial_delay=0.1,
        max_delay=1.0,
        exponential_base=2.0,
    )
    async def failing_func():
        nonlocal call_count
        call_times.append(time.monotonic())
        call_count += 1
        if call_count < 4:
            raise aiohttp.ClientError("Transient error")
        return "success"

    start = time.monotonic()
    result = await failing_func()
    total_elapsed = time.monotonic() - start

    assert result == "success"
    assert call_count == 4

    # Check exponential backoff delays
    # Delay 1: 0.1 * 2^0 = 0.1s
    # Delay 2: 0.1 * 2^1 = 0.2s
    # Delay 3: 0.1 * 2^2 = 0.4s
    # Total: ~0.7s
    assert total_elapsed >= 0.7
    assert total_elapsed < 1.0

    # Verify delays between calls
    if len(call_times) >= 2:
        delay1 = call_times[1] - call_times[0]
        assert delay1 >= 0.09  # ~0.1s
        assert delay1 < 0.15

    if len(call_times) >= 3:
        delay2 = call_times[2] - call_times[1]
        assert delay2 >= 0.19  # ~0.2s
        assert delay2 < 0.25

    if len(call_times) >= 4:
        delay3 = call_times[3] - call_times[2]
        assert delay3 >= 0.39  # ~0.4s
        assert delay3 < 0.45


@pytest.mark.asyncio
async def test_retry_max_delay_cap():
    """Test that max_delay caps the exponential backoff."""
    call_count = 0

    @async_retry(
        max_attempts=5,
        initial_delay=0.1,
        max_delay=0.3,  # Cap at 0.3s
        exponential_base=2.0,
    )
    async def failing_func():
        nonlocal call_count
        call_count += 1
        if call_count < 5:
            raise aiohttp.ClientError("Transient error")
        return "success"

    start = time.monotonic()
    result = await failing_func()
    total_elapsed = time.monotonic() - start

    assert result == "success"
    assert call_count == 5

    # Delays should be capped at 0.3s
    # Delay 1: 0.1s, Delay 2: 0.2s, Delay 3: 0.3s (capped), Delay 4: 0.3s (capped)
    # Total: ~0.9s
    assert total_elapsed >= 0.9
    assert total_elapsed < 1.2


@pytest.mark.asyncio
async def test_retry_max_attempts():
    """Test that retry stops after max_attempts."""
    call_count = 0

    @async_retry(max_attempts=3, initial_delay=0.01)
    async def always_failing_func():
        nonlocal call_count
        call_count += 1
        raise aiohttp.ClientError("Always fails")

    with pytest.raises(aiohttp.ClientError, match="Always fails"):
        await always_failing_func()

    assert call_count == 3  # Should have tried 3 times


@pytest.mark.asyncio
async def test_retry_on_specific_exceptions():
    """Test that retry only retries on specified exceptions."""
    call_count = 0

    @async_retry(
        max_attempts=3,
        initial_delay=0.01,
        retry_on_exceptions=(ValueError,),
    )
    async def func_with_value_error():
        nonlocal call_count
        call_count += 1
        raise ValueError("Value error")

    with pytest.raises(ValueError, match="Value error"):
        await func_with_value_error()

    assert call_count == 3  # Should retry ValueError

    # Reset
    call_count = 0

    @async_retry(
        max_attempts=3,
        initial_delay=0.01,
        retry_on_exceptions=(ValueError,),
    )
    async def func_with_other_error():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Runtime error")

    with pytest.raises(RuntimeError, match="Runtime error"):
        await func_with_other_error()

    assert call_count == 1  # Should NOT retry RuntimeError


@pytest.mark.asyncio
async def test_retry_on_http_status_codes():
    """Test that retry retries on specific HTTP status codes."""
    call_count = 0

    @async_retry(
        max_attempts=3,
        initial_delay=0.01,
        retry_on_status=(500, 502, 503),
    )
    async def func_with_500_error():
        nonlocal call_count
        call_count += 1
        # Simulate aiohttp.ClientResponseError
        error = aiohttp.ClientResponseError(
            request_info=None,
            history=(),
            status=500,
            message="Internal Server Error",
        )
        raise error

    with pytest.raises(aiohttp.ClientResponseError):
        await func_with_500_error()

    assert call_count == 3  # Should retry on 500

    # Reset
    call_count = 0

    @async_retry(
        max_attempts=3,
        initial_delay=0.01,
        retry_on_status=(500, 502, 503),
    )
    async def func_with_404_error():
        nonlocal call_count
        call_count += 1
        # 404 should not be retried
        error = aiohttp.ClientResponseError(
            request_info=None,
            history=(),
            status=404,
            message="Not Found",
        )
        raise error

    with pytest.raises(aiohttp.ClientResponseError):
        await func_with_404_error()

    assert call_count == 1  # Should NOT retry on 404


@pytest.mark.asyncio
async def test_retry_client_response_error_priority():
    """Test that ClientResponseError is checked before ClientError."""
    call_count = 0

    @async_retry(
        max_attempts=3,
        initial_delay=0.01,
        retry_on_status=(429,),  # Only retry 429
        retry_on_exceptions=(aiohttp.ClientError,),
    )
    async def func_with_429_error():
        nonlocal call_count
        call_count += 1
        # 429 should be retried
        error = aiohttp.ClientResponseError(
            request_info=None,
            history=(),
            status=429,
            message="Too Many Requests",
        )
        raise error

    with pytest.raises(aiohttp.ClientResponseError):
        await func_with_429_error()

    assert call_count == 3  # Should retry on 429

    # Reset
    call_count = 0

    @async_retry(
        max_attempts=3,
        initial_delay=0.01,
        retry_on_status=(500,),  # Only retry 500, not 400
        retry_on_exceptions=(aiohttp.ClientError,),
    )
    async def func_with_400_error():
        nonlocal call_count
        call_count += 1
        # 400 should not be retried (not in retry_on_status)
        error = aiohttp.ClientResponseError(
            request_info=None,
            history=(),
            status=400,
            message="Bad Request",
        )
        raise error

    with pytest.raises(aiohttp.ClientResponseError):
        await func_with_400_error()

    assert call_count == 1  # Should NOT retry on 400


@pytest.mark.asyncio
async def test_retry_timeout_error():
    """Test that TimeoutError is retried."""
    call_count = 0

    @async_retry(
        max_attempts=3,
        initial_delay=0.01,
        retry_on_exceptions=(asyncio.TimeoutError,),
    )
    async def func_with_timeout():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TimeoutError("Timeout")
        return "success"

    result = await func_with_timeout()
    assert result == "success"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_non_retryable_exception():
    """Test that non-retryable exceptions are not retried."""
    call_count = 0

    @async_retry(
        max_attempts=3,
        initial_delay=0.01,
        retry_on_exceptions=(aiohttp.ClientError,),
    )
    async def func_with_key_error():
        nonlocal call_count
        call_count += 1
        raise KeyError("Key not found")

    with pytest.raises(KeyError, match="Key not found"):
        await func_with_key_error()

    assert call_count == 1  # Should NOT retry KeyError


@pytest.mark.asyncio
async def test_retry_succeeds_after_retries():
    """Test that function succeeds after some retries."""
    call_count = 0

    @async_retry(max_attempts=5, initial_delay=0.01)
    async def eventually_succeeds():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise aiohttp.ClientError("Transient error")
        return f"success on attempt {call_count}"

    result = await eventually_succeeds()
    assert result == "success on attempt 3"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_default_parameters():
    """Test retry with default parameters."""
    call_count = 0

    @async_retry()  # Use all defaults
    async def failing_func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise aiohttp.ClientError("Error")
        return "success"

    result = await failing_func()
    assert result == "success"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_fallback_exception():
    """Test retry fallback exception handling."""
    call_count = 0

    class SuppressibleError(aiohttp.ClientError):
        pass

    @async_retry(
        max_attempts=3,
        initial_delay=0.01,
        retry_on_exceptions=(SuppressibleError,),
    )
    async def edge_case_func():
        nonlocal call_count
        call_count += 1
        raise SuppressibleError("Test error")

    with pytest.raises(SuppressibleError, match="Test error"):
        await edge_case_func()

    assert call_count == 3
