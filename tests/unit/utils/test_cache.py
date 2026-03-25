import gc
import time
import weakref

import pytest

from dexalot_sdk.utils.cache import MemoryCache, async_ttl_cached, ttl_cached


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
async def test_async_ttl_cached_env_isolation():
    """Cache keys are namespaced by api_base_url; two envs never share results."""

    cache = MemoryCache(ttl_seconds=60)
    call_count = 0

    class MockClient:
        def __init__(self, url, result):
            self.api_base_url = url
            self._cache_enabled = True
            self._result = result

        @async_ttl_cached(cache)
        async def fetch(self):
            nonlocal call_count
            call_count += 1
            return self._result

    testnet = MockClient("https://api-dev.dexalot.com", "testnet-data")
    mainnet = MockClient("https://api.dexalot.com", "mainnet-data")

    assert await testnet.fetch() == "testnet-data"
    assert await mainnet.fetch() == "mainnet-data"
    assert call_count == 2  # each env caused a cache miss

    # Second calls should be served from cache
    assert await testnet.fetch() == "testnet-data"
    assert await mainnet.fetch() == "mainnet-data"
    assert call_count == 2  # no additional calls


@pytest.mark.asyncio
async def test_async_ttl_cached_no_strong_ref():
    """Cache key must not hold a strong reference to self, allowing GC."""

    cache = MemoryCache(ttl_seconds=60)

    class MockClient:
        api_base_url = "https://api.dexalot.com"
        _cache_enabled = True

        @async_ttl_cached(cache)
        async def fetch(self):
            return 42

    client = MockClient()
    ref = weakref.ref(client)
    await client.fetch()

    del client
    gc.collect()
    assert ref() is None, "Client instance should be garbage-collected after del"


@pytest.mark.asyncio
async def test_async_ttl_cached_disabled_cache():
    """Test async_ttl_cached when cache is disabled."""

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
