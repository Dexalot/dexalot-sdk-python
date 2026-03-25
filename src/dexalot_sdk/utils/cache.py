import asyncio
import time
from collections.abc import Callable, Hashable
from functools import wraps
from typing import Any


class MemoryCache:
    _CLEANUP_INTERVAL = 50  # run cleanup every N writes

    def __init__(self, ttl_seconds: float, max_size: int = 256):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._store: dict[Hashable, tuple[float, Any]] = {}
        self._write_count: int = 0

    def _cleanup(self):
        """Remove expired entries. Called every _CLEANUP_INTERVAL writes."""
        now = time.time()
        self._store = {k: v for k, v in self._store.items() if now - v[0] < self.ttl}

    def _trim(self):
        """Trim to max_size using FIFO eviction. Called on every write."""
        if len(self._store) > self.max_size:
            num_to_remove = len(self._store) - self.max_size
            # Python 3.7+ dicts preserve insertion order
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
        self._trim()
        self._write_count += 1
        if self._write_count >= self._CLEANUP_INTERVAL:
            self._cleanup()
            self._write_count = 0

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

            instance = args[0] if args else None
            env_key = getattr(instance, "api_base_url", "") or ""
            key = (func.__name__, env_key, args[1:], frozenset(kwargs.items()))
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

    Stampede protection: concurrent callers for the same uncached key are
    coalesced — the underlying function is called exactly once and all
    waiters receive the same result.
    """

    def decorator(func: Callable):
        _pending: dict[Hashable, asyncio.Future[Any]] = {}
        _pending_lock = asyncio.Lock()

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Check if this is an instance method and if caching is disabled
            if args and hasattr(args[0], "_cache_enabled") and not args[0]._cache_enabled:
                # Bypass cache entirely - call function directly
                return await func(*args, **kwargs)

            instance = args[0] if args else None
            env_key = getattr(instance, "api_base_url", "") or ""
            key = (func.__name__, env_key, args[1:], frozenset(kwargs.items()))

            cached = cache.get(key)
            if cached is not None:
                return cached

            do_work = False
            async with _pending_lock:
                # Double-check inside the lock to close the race between
                # the initial cache miss and acquiring _pending_lock.
                cached = cache.get(key)
                if cached is not None:
                    return cached
                if key in _pending:
                    fut: asyncio.Future[Any] = _pending[key]
                else:
                    fut = asyncio.get_running_loop().create_future()
                    _pending[key] = fut
                    do_work = True

            if not do_work:
                return await asyncio.shield(fut)

            try:
                result = await func(*args, **kwargs)
                cache.set(key, result)
                fut.set_result(result)
                return result
            except Exception as e:
                fut.set_exception(e)
                raise
            finally:
                async with _pending_lock:
                    _pending.pop(key, None)

        return wrapper

    return decorator
