"""Offline brain backed by a local Ollama model.

Jarvis persona, dry-witted and brief — the offline counterpart to Shelby.
Matches the async-context-manager + process_stream protocol of ClaudeBrain
so HybridBrain can swap them transparently.

For v1 this is text-only: no tool calling. Jarvis responds from the
model's own knowledge plus a few facts injected via the system prompt
(current time, location). Tool support can be layered in later via
Ollama's OpenAI-compatible tool-call API once Llama 3.1+ is the
default offline model.

Setup the user needs:
    ollama pull llama3.2          (or any model they prefer)
    ollama serve                  (usually starts automatically)
Override the model via SHELBY_OLLAMA_MODEL env var.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import AsyncIterator, Optional

import httpx

from . import memory


OLLAMA_HOST = os.environ.get("SHELBY_OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("SHELBY_OLLAMA_MODEL", "llama3.2")
DEFAULT_LOCATION = os.environ.get("SHELBY_LOCATION", "Hull")


JARVIS_PROMPT = (
    "You are Jarvis, Captain PKT's offline tactical assistant. Local model, "
    "no internet, no live tools. Address the user as 'Captain' or 'Sir' with "
    "a dry, formal, slightly clipped British wit. Keep replies short and "
    "spoken (under three sentences when possible). Plain prose only — no "
    "markdown, no asterisks, no bullets, no code fences, no URLs.\n"
    "\n"
    "You do not have live weather, news, GitHub or email. If Captain asks "
    "for any of those, acknowledge that you are offline and offer what you "
    "do know (current time, general advice, conversation). Do not make up "
    "specific facts.\n"
    "\n"
    "When Captain asks for the time or date, use the current_context block "
    "below rather than guessing. If they ask for a 'briefing' or 'updates' "
    "offline, give them: the time, a short situational acknowledgement that "
    "external tools are unavailable, and a proactive question (e.g. 'Shall "
    "I prepare something local for you, Captain?').\n"
)


class OllamaBrain:
    """Local Llama-family brain via Ollama's HTTP API.

    Lifecycle matches ClaudeBrain:
        async with OllamaBrain() as brain:
            async for ev in brain.process_stream("hello"):
                ...

    process_stream yields {"type": "text", "text": "..."} events (no
    "tool" events in v1 since we don't wire tool calls yet).
    """

    name = "Jarvis"
    mode = "offline"

    def __init__(self, model: str = OLLAMA_MODEL, host: str = OLLAMA_HOST) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        # Long-ish read timeout because local models stream slowly on CPU.
        # Connect timeout stays tight so a dead Ollama daemon fails fast.
        self._client = httpx.AsyncClient(
            base_url=self.host,
            timeout=httpx.Timeout(connect=2.0, read=120.0, write=10.0, pool=2.0),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _current_context(self) -> str:
        now = datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")
        return (
            "current_context:\n"
            f"  local_time: {now}\n"
            f"  location: {DEFAULT_LOCATION}\n"
            "  network: OFFLINE (no internet access)\n"
        )

    async def process_stream(self, text: str) -> AsyncIterator[dict]:
        if self._client is None:
            raise RuntimeError("OllamaBrain must be used as an async context manager")

        preamble = memory.format_for_prompt(n=10)  # smaller window for local model
        system_parts = [JARVIS_PROMPT, self._current_context()]
        if preamble:
            system_parts.append(preamble)
        system = "\n".join(system_parts)
        payload = {
            "model": self.model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "options": {
                # Slightly lower temperature for the formal Jarvis tone.
                "temperature": 0.6,
                "num_predict": 512,
            },
        }

        try:
            async with self._client.stream(
                "POST", "/api/chat", json=payload
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise RuntimeError(
                        f"ollama returned {response.status_code}: {body[:200]!r}"
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = chunk.get("message") or {}
                    content = msg.get("content") or ""
                    if content:
                        yield {"type": "text", "text": content}
                    if chunk.get("done"):
                        return
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError, OSError):
            # Ollama daemon not running. Surface a single graceful sentence
            # the consumer can speak so Captain isn't left hanging.
            yield {
                "type": "text",
                "text": (
                    "Apologies Captain, the local model is unreachable. "
                    "Please start Ollama, or come back online."
                ),
            }
        except Exception as exc:
            msg = str(exc).strip() or exc.__class__.__name__
            yield {"type": "text", "text": f"Local model error: {msg}."}
