"""HybridBrain: routes each turn to ClaudeBrain (online) or OllamaBrain
(offline) based on a live connectivity probe.

Connection check runs at the start of every process_stream call, cached
30s in shelby.net. The active brain is held open between turns and only
swapped when the connectivity verdict actually changes — so a user with
stable internet stays on Claude the whole session without churn.

If SHELBY_FORCE_MODE is set to "online" or "offline" the probe is skipped
and that mode is pinned for the session (useful for testing without
actually pulling the network cable).
"""
from __future__ import annotations

import os
from typing import AsyncIterator, Optional

from . import net
from .brain import ClaudeBrain
from .brain_ollama import OllamaBrain


FORCE_MODE = os.environ.get("SHELBY_FORCE_MODE", "").strip().lower() or None


class HybridBrain:
    """Top-level brain that auto-routes between Shelby (online, Claude) and
    Jarvis (offline, Ollama).

    Lifecycle is symmetric with the two backing brains:
        async with HybridBrain() as brain:
            async for ev in brain.process_stream("hello"):
                ...

    Exposes `current.name` / `current.mode` so the caller can announce
    persona changes to the UI.
    """

    def __init__(self) -> None:
        self._claude: Optional[ClaudeBrain] = None
        self._ollama: Optional[OllamaBrain] = None
        self.current = None  # type: Optional[ClaudeBrain | OllamaBrain]
        self.mode: str = "unknown"  # "online" | "offline"

    async def __aenter__(self):
        # Decide initial mode up front. Subsequent turns refresh via probe.
        await self._select_mode()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._claude is not None:
            await self._claude.__aexit__(exc_type, exc, tb)
            self._claude = None
        if self._ollama is not None:
            await self._ollama.__aexit__(exc_type, exc, tb)
            self._ollama = None
        self.current = None

    async def _select_mode(self) -> str:
        """Probe connectivity (or honour the force override) and bring up
        the appropriate brain. Tears down the other if it was open.
        Returns the new mode string."""
        if FORCE_MODE in ("online", "offline"):
            online = FORCE_MODE == "online"
        else:
            online = await net.is_online()

        new_mode = "online" if online else "offline"
        if new_mode == self.mode and self.current is not None:
            return new_mode

        # Bring up the chosen brain.
        if online:
            if self._claude is None:
                self._claude = ClaudeBrain()
                await self._claude.__aenter__()
            self.current = self._claude
            # Tear down the offline brain to free its httpx client.
            if self._ollama is not None:
                await self._ollama.__aexit__(None, None, None)
                self._ollama = None
        else:
            if self._ollama is None:
                self._ollama = OllamaBrain()
                await self._ollama.__aenter__()
            self.current = self._ollama
            if self._claude is not None:
                await self._claude.__aexit__(None, None, None)
                self._claude = None

        self.mode = new_mode
        return new_mode

    async def process_stream(self, text: str) -> AsyncIterator[dict]:
        prev_mode = self.mode
        new_mode = await self._select_mode()
        if new_mode != prev_mode and prev_mode != "unknown":
            # Surface a soft transition note as the first text event so the
            # UI can re-skin and the user knows the persona changed.
            yield {
                "type": "mode",
                "mode": new_mode,
                "name": self.current.name if self.current else "",
            }
        if self.current is None:
            yield {"type": "text", "text": "No brain available, Captain."}
            return
        async for ev in self.current.process_stream(text):
            yield ev
