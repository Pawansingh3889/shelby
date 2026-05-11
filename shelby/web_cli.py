from __future__ import annotations

import asyncio
import os
import re
import threading
import time
import webbrowser

import anyio
import uvicorn

from .ambient import get_wake_model, record_until_silence, wait_for_wake
from .brain import Brain
from . import timers
from .voice import speak_async, strip_markdown_for_speech, transcribe, warmup_stt, warmup_tts
from .web import app, publish


# Single audio lock shared between the conversation TTS path and the
# background timer announcer. Timers wait for the current turn to finish
# before chiming so we never overlap two audio streams.
AUDIO_LOCK = asyncio.Lock()


HOST = os.environ.get("SHELBY_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHELBY_WEB_PORT", "8765"))

# Punctuation that signals the end of a speakable chunk. Comma included so
# long mid-sentence phrases get spoken without waiting for the full stop.
_SENTENCE_RE = re.compile(r"[.!?](?:\s|$)|[,;:](?=\s)")


# Friendly verb-form labels for each tool, shown as a pill under the orb
# while Shelby is fetching. Keys are matched against the trailing segment
# of the tool name after the last "__" (mcp__shelby__weather -> weather).
_TOOL_LABELS = {
    "current_time": "Checking the time",
    "system_info": "Checking system",
    "weather": "Checking weather",
    "forecast": "Pulling forecast",
    "news_headlines": "Reading the news",
    "github_pending": "Sweeping GitHub",
    "set_timer": "Setting timer",
    "list_timers": "Checking timers",
    "cancel_timer": "Cancelling timer",
    "open_application": "Launching app",
    "open_url": "Opening link",
    "open_url_tool": "Opening link",
    "search_threads": "Sweeping inbox",
    "get_thread": "Reading email",
    "list_events": "Checking calendar",
    "list_calendars": "Listing calendars",
    "WebSearch": "Searching the web",
    "WebFetch": "Reading page",
}


def _pretty_tool(full_name: str) -> str:
    """Turn an MCP tool name into a short user-facing label."""
    if not full_name:
        return ""
    short = full_name.split("__")[-1]
    return _TOOL_LABELS.get(short) or _TOOL_LABELS.get(full_name) or short.replace("_", " ")


def _serve() -> None:
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.run()


def _next_chunk_boundary(buffer: str) -> int:
    """Find the index of the next speakable chunk boundary in buffer, or -1.

    Returns the slice end position (after the punctuation + whitespace), so
    buffer[:end] is the chunk text including its closing punctuation.
    """
    m = _SENTENCE_RE.search(buffer)
    if m is None:
        return -1
    end = m.end()
    # Don't fire on very short fragments (e.g. 'Hi.' inside 'Hi. Captain'); we
    # want enough text per chunk for natural prosody.
    if end < 12 and len(buffer) > end:
        nxt = _SENTENCE_RE.search(buffer, end)
        if nxt is not None:
            return nxt.end()
    return end


async def _stream_speak(brain: Brain, prompt: str) -> str:
    """Stream brain.process_stream into TTS+playback by sentence.

    Producer task reads text deltas from the brain stream, accumulates a
    buffer, and pushes complete sentences onto a queue. Consumer task pulls
    sentences and calls speak_async, which TTS-encodes and plays each one
    sequentially while publishing chunk-by-chunk updates to the web UI.

    Returns the full assembled reply string for logging.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    full_reply: list[str] = []
    # Match the quoted display the main loop publishes before calling us,
    # so the user transcript stays visible while we update `doing` pills.
    display_prompt = f"“{prompt}”"

    async def producer() -> None:
        buffer = ""
        try:
            async for event in brain.process_stream(prompt):
                etype = event.get("type") if isinstance(event, dict) else None
                if etype == "tool":
                    label = _pretty_tool(event.get("name", ""))
                    if label:
                        # Keep the user's transcript visible in `text`; the
                        # frontend shows `doing` as a pill underneath.
                        publish("thinking", text=display_prompt, doing=label)
                    continue
                if etype != "text":
                    continue
                buffer += event.get("text", "")
                while True:
                    end = _next_chunk_boundary(buffer)
                    if end < 0:
                        break
                    chunk = strip_markdown_for_speech(buffer[:end].strip())
                    buffer = buffer[end:]
                    if chunk:
                        await queue.put(chunk)
            tail = strip_markdown_for_speech(buffer.strip())
            if tail:
                await queue.put(tail)
        finally:
            await queue.put(None)

    async def consumer() -> None:
        first = True
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            full_reply.append(chunk)
            chunk_text = chunk

            async def _on_start(words, levels):
                publish(
                    "speaking",
                    text=chunk_text,
                    words=words,
                    levels=levels,
                    append=not first,
                )

            try:
                async with AUDIO_LOCK:
                    await speak_async(chunk_text, on_start=_on_start)
            except Exception as exc:
                print(f"[tts error: {exc}]", flush=True)
            first = False

    await asyncio.gather(producer(), consumer())
    return " ".join(full_reply).strip()


async def _announce_timer(t: timers.Timer) -> None:
    """Speak a fired timer's message. Waits for the audio lock so it never
    overlaps with the current conversation turn."""
    line = f"Captain, your timer is up. {t.message}."
    print(f"[timer {t.id} fired]> {line}", flush=True)
    async with AUDIO_LOCK:
        publish("speaking", text=line, doing="")
        try:
            await speak_async(strip_markdown_for_speech(line))
        except Exception as exc:
            print(f"[timer tts error: {exc}]", flush=True)


async def _loop() -> None:
    follow_up_window_ms = int(os.environ.get("SHELBY_FOLLOWUP_MS", "5000"))

    print(f"[serving UI at http://{HOST}:{PORT}]", flush=True)
    print("[warming up speech-to-text]", flush=True)
    warmup_stt()
    print("[warming up text-to-speech]", flush=True)
    await warmup_tts()
    print("[loading wake-word model]", flush=True)
    get_wake_model()
    print("[ready, listening for 'hey jarvis']\n", flush=True)
    publish("idle", text="Say 'hey jarvis' to start.")

    # Background timer watcher: polls every second, fires reminders via
    # _announce_timer which serialises on AUDIO_LOCK against active TTS.
    timer_task = asyncio.create_task(timers.watch_loop(_announce_timer))

    async with Brain() as brain:
        try:
            while True:
                # Run the blocking sync wake/record calls in a thread so the
                # event loop stays free for the timer watcher.
                await asyncio.to_thread(wait_for_wake)
                publish("listening")
                print("> wake detected, listening...", flush=True)
                audio = await asyncio.to_thread(record_until_silence)

                while True:
                    if audio.size == 0:
                        print("(no audio)", flush=True)
                        break

                    text = await asyncio.to_thread(transcribe, audio)
                    if not text:
                        print("(no speech detected)", flush=True)
                        break

                    print(f"you> {text}", flush=True)
                    publish("thinking", text=f"“{text}”")

                    try:
                        reply = await _stream_speak(brain, text)
                    except Exception as exc:
                        print(f"shelby> [error: {exc}]", flush=True)
                        publish("idle", text=f"error: {exc}")
                        break

                    print(f"shelby> {reply}", flush=True)

                    publish("listening", text="follow-up?")
                    audio = await asyncio.to_thread(
                        record_until_silence, follow_up_window_ms
                    )
                    if audio.size == 0:
                        print("(no follow-up)", flush=True)
                        break

                publish("idle", text="Say 'hey jarvis' to start.")
                print("[listening for 'hey jarvis']\n", flush=True)
        except KeyboardInterrupt:
            publish("idle", text="shutting down")
            print("\n[shutting down]")
        finally:
            timer_task.cancel()


def run() -> None:
    threading.Thread(target=_serve, daemon=True).start()
    time.sleep(0.6)
    try:
        webbrowser.open(f"http://{HOST}:{PORT}")
    except Exception:
        pass
    anyio.run(_loop)


if __name__ == "__main__":
    run()
