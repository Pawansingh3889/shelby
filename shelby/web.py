from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

import psutil
from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route


# Boot timestamp for live uptime reporting via /stats. Set at module import,
# which lines up with shelby-demo's process start.
_BOOT_AT = time.monotonic()
# Prime the per-process cpu_percent baseline so the first /stats response
# isn't 0.0%.
psutil.cpu_percent(interval=None)


_subscribers: list[asyncio.Queue] = []
_last_payload: dict = {"state": "idle", "text": ""}


def publish(
    state: str,
    text: Optional[str] = None,
    words: Optional[list] = None,
    append: bool = False,
    doing: Optional[str] = None,
    levels: Optional[list] = None,
) -> None:
    payload = {
        "state": state,
        "text": text or "",
        "words": words or [],
        "append": bool(append),
        "doing": doing or "",
        "levels": levels or [],
    }
    global _last_payload
    # Late-joiners shouldn't see append payloads in isolation, so we never
    # store an append=True payload as the 'last' state. The fully-assembled
    # text gets sent by the speak loop when the chunk completes anyway.
    if not append:
        _last_payload = payload
    msg = json.dumps(payload)
    for q in _subscribers:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


async def _homepage(request):
    return FileResponse(Path(__file__).parent / "static" / "index.html")


async def _wake(request):
    from .ambient import MANUAL_WAKE
    MANUAL_WAKE.set()
    return Response(status_code=204)


async def _stats(request):
    """Live system stats for the HUD right panel.

    Polled every couple of seconds from the frontend. Cheap to compute and
    avoids spamming SSE with telemetry that the conversation stream doesn't
    care about.
    """
    vm = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    uptime_s = int(time.monotonic() - _BOOT_AT)
    try:
        battery = psutil.sensors_battery()
        batt_pct = round(battery.percent, 1) if battery else None
        batt_plugged = bool(battery.power_plugged) if battery else None
    except Exception:
        batt_pct = None
        batt_plugged = None
    return JSONResponse({
        "cpu_pct": round(cpu, 1),
        "mem_pct": round(vm.percent, 1),
        "mem_used_gb": round(vm.used / (1024**3), 1),
        "mem_total_gb": round(vm.total / (1024**3), 1),
        "uptime_s": uptime_s,
        "battery_pct": batt_pct,
        "battery_plugged": batt_plugged,
    })


async def _events(request):
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
    _subscribers.append(q)

    async def gen():
        try:
            yield {"data": json.dumps(_last_payload)}
            while True:
                msg = await q.get()
                yield {"data": msg}
        finally:
            try:
                _subscribers.remove(q)
            except ValueError:
                pass

    return EventSourceResponse(gen())


app = Starlette(
    routes=[
        Route("/", _homepage),
        Route("/events", _events),
        Route("/wake", _wake, methods=["POST"]),
        Route("/stats", _stats),
    ]
)
