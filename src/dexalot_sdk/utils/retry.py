import asyncio
import functools
from collections.abc import Callable
from typing import Any, TypeVar

import aiohttp

T = TypeVar("T")


def async_retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
    exponential_base: float = 2.0,
    retry_on_status: tuple[int, ...] = (429, 500, 502, 503, 504),
    retry_on_exceptions: tuple[type[BaseException], ...] = (
        aiohttp.ClientError,
        asyncio.TimeoutError,
    ),
):
    """
    Async retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3).
        initial_delay: Initial delay in seconds before first retry (default: 1.0).
        max_delay: Maximum delay in seconds between retries (default: 10.0).
        exponential_base: Base for exponential backoff calculation (default: 2.0).
        retry_on_status: Tuple of HTTP status codes that should trigger a retry.
        retry_on_exceptions: Tuple of exception types that should trigger a retry.

    Returns:
        Decorated async function that will retry on specified errors.

    Example:
        @async_retry(max_attempts=3, initial_delay=1.0)
        async def fetch_data():
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.json()
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception: BaseException | None = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except aiohttp.ClientResponseError as e:
                    # Handle HTTP status codes first (more specific than ClientError)
                    last_exception = e
                    if e.status in retry_on_status and attempt < max_attempts - 1:
                        # Calculate delay with exponential backoff
                        delay = min(
                            initial_delay * (exponential_base**attempt),
                            max_delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    # Status code not retryable or last attempt, re-raise
                    raise
                except retry_on_exceptions as e:
                    last_exception = e
                    # Check if we should retry
                    if attempt < max_attempts - 1:
                        # Calculate delay with exponential backoff
                        delay = min(
                            initial_delay * (exponential_base**attempt),
                            max_delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    # Last attempt failed, re-raise
                    raise
                except Exception:
                    # For any other exception, don't retry
                    raise

            # This should never be reached, but just in case
            # This code is defensive and cannot be reached through normal execution
            # as the loop always either returns successfully or raises an exception
            if last_exception:  # pragma: no cover
                raise last_exception  # pragma: no cover

        return wrapper

    return decorator
