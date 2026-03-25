import time

import pytest

from dexalot_sdk.utils.cache import MemoryCache, ttl_cached


def test_memory_cache_set_get():
    cache = MemoryCache(ttl_seconds=1)
    cache.set("key", "value")
    assert cache.get("key") == "value"


def test_memory_cache_ttl():
    cache = MemoryCache(ttl_seconds=0.1)
    cache.set("key", "value")
    assert cache.get("key") == "value"
    time.sleep(0.2)
    assert cache.get("key") is None


def test_memory_cache_max_size():
    cache = MemoryCache(ttl_seconds=60, max_size=2)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    cache.set("k3", "v3")

    # Should have evicted k1 (FIFO-ish)
    assert cache.get("k1") is None
    assert cache.get("k2") == "v2"
    assert cache.get("k3") == "v3"


def test_ttl_cached_decorator():
    cache = MemoryCache(ttl_seconds=0.1)

    call_count = 0

    @ttl_cached(cache)
    def expensive_func(x):
        nonlocal call_count
        call_count += 1
        return x * 2

    # First call
    assert expensive_func(2) == 4
    assert call_count == 1

    # Second call (cached)
    assert expensive_func(2) == 4
    assert call_count == 1

    # Wait for expiry
    time.sleep(0.2)

    # Third call (re-computed)
    assert expensive_func(2) == 4
    assert call_count == 2


def test_ttl_cached_disabled_cache():
    """Test ttl_cached when cache is disabled."""
    cache = MemoryCache(ttl_seconds=60)

    call_count = 0

    class MockClient:
        def __init__(self):
            self._cache_enabled = False

        @ttl_cached(cache)
        def expensive_func(self, x):
            nonlocal call_count
            call_count += 1
            return x * 2

    client = MockClient()

    # First call - should bypass cache
    assert client.expensive_func(2) == 4
    assert call_count == 1

    # Second call - should still call function (cache disabled)
    assert client.expensive_func(2) == 4
    assert call_count == 2  # Called again, not cached


@pytest.mark.asyncio
async def test_async_ttl_cached_decorator():
    from dexalot_sdk.utils.cache import async_ttl_cached

    cache = MemoryCache(ttl_seconds=0.1)

    call_count = 0

    @async_ttl_cached(cache)
    async def expensive_func(x):
        nonlocal call_count
        call_count += 1
        return x * 2

    # First call
    assert await expensive_func(2) == 4
    assert call_count == 1

    # Second call (cached)
    assert await expensive_func(2) == 4
    assert call_count == 1

    # Wait for expiry
    time.sleep(0.2)

    # Third call (re-computed)
    assert await expensive_func(2) == 4
    assert call_count == 2


@pytest.mark.asyncio
async def test_async_ttl_cached_disabled_cache():
    """Test async_ttl_cached when cache is disabled."""
    from dexalot_sdk.utils.cache import async_ttl_cached

    cache = MemoryCache(ttl_seconds=60)

    call_count = 0

    class MockClient:
        def __init__(self):
            self._cache_enabled = False

        @async_ttl_cached(cache)
        async def expensive_func(self, x):
            nonlocal call_count
            call_count += 1
            return x * 2

    client = MockClient()

    # First call - should bypass cache
    assert await client.expensive_func(2) == 4
    assert call_count == 1

    # Second call - should still call function (cache disabled)
    assert await client.expensive_func(2) == 4
    assert call_count == 2  # Called again, not cached
