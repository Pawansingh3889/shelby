"""Smoke tests for shelby.net (connectivity probe)."""
from __future__ import annotations

import asyncio


def test_is_online_sync_wraps_async():
    from shelby import net
    # No assertion on True/False since the test environment varies; we
    # just check the function returns a bool and doesn't blow up.
    result = net.is_online_sync()
    assert isinstance(result, bool)


def test_invalidate_clears_cache():
    from shelby import net
    # Populate cache.
    asyncio.run(net.is_online())
    assert net._CACHE is not None
    net.invalidate()
    assert net._CACHE is None


def test_repeated_call_uses_cache(monkeypatch):
    from shelby import net
    net.invalidate()
    asyncio.run(net.is_online())
    cached = net._CACHE
    asyncio.run(net.is_online())
    # Second call should hit the cache, not re-probe (same tuple).
    assert net._CACHE is cached
