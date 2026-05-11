"""Connectivity detection for the hybrid brain.

We probe two endpoints in priority order:
1. Anthropic API (the actual dependency for online mode) — head request
2. Cloudflare DNS over HTTPS (1.1.1.1) — generic fallback for "is internet
   even up", in case Anthropic's status is degraded but the user could
   still be reachable for tools

Result is cached for 30 seconds so we don't probe on every turn. A
manual `invalidate()` resets the cache for when the user wants to
force a re-probe (e.g. after they reconnect their VPN).
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx


# Cache: (timestamp, online_bool). 30s TTL feels right — long enough to
# avoid hammering on rapid-fire turns, short enough that pulling the
# ethernet cable is reflected by the next briefing.
_CACHE: Optional[tuple[float, bool]] = None
_CACHE_TTL_S = 30.0
_PROBE_TIMEOUT_S = 2.5

_PROBES = [
    "https://api.anthropic.com/",
    "https://1.1.1.1/",
]


def invalidate() -> None:
    global _CACHE
    _CACHE = None


async def is_online(force: bool = False) -> bool:
    """Return True if we have working internet.

    Cached for ~30s. Pass force=True to bypass cache.
    """
    global _CACHE
    now = time.monotonic()
    if not force and _CACHE is not None and (now - _CACHE[0]) < _CACHE_TTL_S:
        return _CACHE[1]

    online = False
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
        for url in _PROBES:
            try:
                # HEAD is cheaper than GET; we don't care about the body.
                r = await client.head(url)
                # Any 2xx / 3xx / 4xx means we reached a server. 4xx is fine
                # here — e.g. Anthropic returns 401 for unauth HEAD, but
                # that proves connectivity.
                if r.status_code < 500:
                    online = True
                    break
            except (httpx.HTTPError, asyncio.TimeoutError, OSError):
                continue

    _CACHE = (now, online)
    return online


def is_online_sync(force: bool = False) -> bool:
    """Synchronous wrapper for callers that aren't already in an event loop.

    Spins up a tiny loop just for the probe. Don't call this from inside
    an active event loop — use is_online() directly there.
    """
    try:
        return asyncio.run(is_online(force=force))
    except RuntimeError:
        # Already inside a loop. The caller should have used is_online() async.
        return False
