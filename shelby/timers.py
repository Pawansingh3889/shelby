"""In-memory timer + reminder system for Shelby.

Captain says "remind me in 20 minutes to check the oven" → the brain calls
set_timer(seconds=1200, message="check the oven") → this module stores the
fire time → a background watcher coroutine in web_cli polls due timers
every second and pushes a callback to the audio loop.

State is intentionally in-memory: timers don't survive a process restart.
That matches a voice assistant's natural lifecycle (start shelby, talk to
it, eventually close it). Persistence would mean alarms going off after a
machine reboot, which is rarely what you want.
"""
from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional


@dataclass
class Timer:
    id: int
    fire_at: float        # time.monotonic() target
    seconds: int          # original duration, for display
    message: str
    created_at: float = field(default_factory=time.monotonic)


_timers: dict[int, Timer] = {}
_next_id = itertools.count(1)


def schedule(seconds: int, message: str) -> Timer:
    """Schedule a new timer to fire in `seconds` from now."""
    seconds = max(1, int(seconds))
    message = (message or "").strip() or "your timer is up"
    now = time.monotonic()
    t = Timer(
        id=next(_next_id),
        fire_at=now + seconds,
        seconds=seconds,
        message=message,
    )
    _timers[t.id] = t
    return t


def cancel(timer_id: int) -> Optional[Timer]:
    return _timers.pop(timer_id, None)


def cancel_by_match(needle: str) -> list[Timer]:
    """Cancel timers whose message contains `needle` (case-insensitive)."""
    needle = (needle or "").strip().lower()
    if not needle:
        return []
    matched = [t for t in _timers.values() if needle in t.message.lower()]
    for t in matched:
        _timers.pop(t.id, None)
    return matched


def all_active() -> list[Timer]:
    return sorted(_timers.values(), key=lambda t: t.fire_at)


def pop_due(now: Optional[float] = None) -> list[Timer]:
    """Return and remove all timers whose fire time has passed."""
    now = now if now is not None else time.monotonic()
    due = [t for t in _timers.values() if t.fire_at <= now]
    for t in due:
        _timers.pop(t.id, None)
    return due


def remaining(t: Timer, now: Optional[float] = None) -> int:
    now = now if now is not None else time.monotonic()
    return max(0, int(t.fire_at - now))


def fmt_remaining(t: Timer, now: Optional[float] = None) -> str:
    r = remaining(t, now)
    if r >= 3600:
        return f"{r // 3600}h {(r % 3600) // 60}m"
    if r >= 60:
        return f"{r // 60}m {r % 60}s"
    return f"{r}s"


async def watch_loop(
    on_fire: Callable[[Timer], Awaitable[None]],
    poll_interval_s: float = 1.0,
) -> None:
    """Background coroutine: poll for due timers and call on_fire for each.

    Runs forever until the surrounding task is cancelled. Sleeps briefly
    between polls so the event loop stays responsive.
    """
    while True:
        try:
            for t in pop_due():
                try:
                    await on_fire(t)
                except Exception as exc:
                    print(f"[timer fire error: {exc}]", flush=True)
            await asyncio.sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[timer watcher error: {exc}]", flush=True)
            await asyncio.sleep(poll_interval_s)
