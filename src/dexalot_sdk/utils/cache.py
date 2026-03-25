import time
from collections.abc import Callable, Hashable
from functools import wraps
from typing import Any


class MemoryCache:
    def __init__(self, ttl_seconds: float, max_size: int = 256):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._store: dict[Hashable, tuple[float, Any]] = {}

    def _cleanup(self):
        now = time.time()
        # remove expired
        self._store = {k: v for k, v in self._store.items() if now - v[0] < self.ttl}
        # trim to max_size (simple FIFO-ish)
        # trim to max_size (simple FIFO-ish based on insertion order if dict is ordered)
        # Python 3.7+ dicts preserve insertion order.
        if len(self._store) > self.max_size:
            # Calculate how many to remove
            num_to_remove = len(self._store) - self.max_size
            # Create a list of keys to remove to avoid runtime error during iteration
            keys_to_remove = list(self._store.keys())[:num_to_remove]
            for k in keys_to_remove:
                self._store.pop(k, None)

    def get(self, key: Hashable) -> Any | None:
        now = time.time()
        value = self._store.get(key)
        if not value:
            return None
        ts, payload = value
        if now - ts > self.ttl:
            # expired
            self._store.pop(key, None)
            return None
        return payload

    def set(self, key: Hashable, value: Any):
        self._store[key] = (time.time(), value)
        self._cleanup()

    def clear(self):
        self._store.clear()


def ttl_cached(cache: MemoryCache):
    """Decorator for sync functions.

    If the decorated method is an instance method and the instance has
    a _cache_enabled attribute set to False, caching is bypassed.
    """

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Check if this is an instance method and if caching is disabled
            if args and hasattr(args[0], "_cache_enabled") and not args[0]._cache_enabled:
                # Bypass cache entirely - call function directly
                return func(*args, **kwargs)

            # crude key: function name + args
            key = (func.__name__, args, frozenset(kwargs.items()))
            cached = cache.get(key)
            if cached is not None:
                return cached
            result = func(*args, **kwargs)
            cache.set(key, result)
            return result

        return wrapper

    return decorator


def async_ttl_cached(cache: MemoryCache):
    """Decorator for async functions.

    If the decorated method is an instance method and the instance has
    a _cache_enabled attribute set to False, caching is bypassed.
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Check if this is an instance method and if caching is disabled
            if args and hasattr(args[0], "_cache_enabled") and not args[0]._cache_enabled:
                # Bypass cache entirely - call function directly
                return await func(*args, **kwargs)

            key = (func.__name__, args, frozenset(kwargs.items()))
            cached = cache.get(key)
            if cached is not None:
                return cached
            result = await func(*args, **kwargs)
            cache.set(key, result)
            return result

        return wrapper

    return decorator
