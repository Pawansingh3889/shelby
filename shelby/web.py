from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route


_subscribers: list[asyncio.Queue] = []
_last_payload: dict = {"state": "idle", "text": ""}


def publish(state: str, text: Optional[str] = None, words: Optional[list] = None) -> None:
    payload = {"state": state, "text": text or "", "words": words or []}
    global _last_payload
    _last_payload = payload
    msg = json.dumps(payload)
    for q in _subscribers:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


async def _homepage(request):
    return FileResponse(Path(__file__).parent / "static" / "index.html")


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
    ]
)
