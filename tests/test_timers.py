"""Smoke tests for shelby.timers."""
from __future__ import annotations

import asyncio


def setup_function():
    # Module-level singleton; reset between tests.
    from shelby import timers
    for t in list(timers._timers.values()):
        timers.cancel(t.id)


def test_schedule_and_list():
    from shelby import timers
    t = timers.schedule(120, "check oven")
    assert t.id > 0
    assert t.seconds == 120
    assert t.message == "check oven"
    assert timers.all_active() == [t]


def test_schedule_clamps_to_one_second():
    from shelby import timers
    t = timers.schedule(0, "x")
    assert t.seconds == 1


def test_cancel_by_id():
    from shelby import timers
    t = timers.schedule(60, "x")
    gone = timers.cancel(t.id)
    assert gone is t
    assert timers.cancel(t.id) is None
    assert timers.all_active() == []


def test_cancel_by_match():
    from shelby import timers
    timers.schedule(60, "check the oven")
    timers.schedule(60, "leave for the train")
    matched = timers.cancel_by_match("oven")
    assert len(matched) == 1
    assert matched[0].message == "check the oven"
    assert len(timers.all_active()) == 1


def test_pop_due():
    from shelby import timers
    # schedule already-due timer by setting fire_at in the past
    t = timers.schedule(1, "fire")
    t.fire_at -= 100
    due = timers.pop_due()
    assert len(due) == 1
    assert due[0].id == t.id
    assert timers.all_active() == []


def test_fmt_remaining_buckets():
    from shelby import timers
    t1 = timers.schedule(30, "x")
    t2 = timers.schedule(600, "x")
    t3 = timers.schedule(7200, "x")
    s1 = timers.fmt_remaining(t1)
    s2 = timers.fmt_remaining(t2)
    s3 = timers.fmt_remaining(t3)
    assert s1.endswith("s")
    assert "m" in s2
    assert "h" in s3


def test_watch_loop_fires_callbacks():
    from shelby import timers
    fired: list[timers.Timer] = []

    async def on_fire(t):
        fired.append(t)

    async def run_briefly():
        task = asyncio.create_task(timers.watch_loop(on_fire, poll_interval_s=0.05))
        t = timers.schedule(1, "test")
        t.fire_at -= 100
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())
    assert len(fired) == 1
    assert fired[0].message == "test"
