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
from .voice import speak_async, transcribe, warmup_stt, warmup_tts
from .web import app, publish


HOST = os.environ.get("SHELBY_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHELBY_WEB_PORT", "8765"))

# Punctuation that signals the end of a speakable chunk. Comma included so
# long mid-sentence phrases get spoken without waiting for the full stop.
_SENTENCE_RE = re.compile(r"[.!?](?:\s|$)|[,;:](?=\s)")


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

    async def producer() -> None:
        buffer = ""
        try:
            async for delta in brain.process_stream(prompt):
                buffer += delta
                while True:
                    end = _next_chunk_boundary(buffer)
                    if end < 0:
                        break
                    chunk = buffer[:end].strip()
                    buffer = buffer[end:]
                    if chunk:
                        await queue.put(chunk)
            if buffer.strip():
                await queue.put(buffer.strip())
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

            async def _on_start(words):
                publish("speaking", text=chunk_text, words=words, append=not first)

            try:
                await speak_async(chunk_text, on_start=_on_start)
            except Exception as exc:
                print(f"[tts error: {exc}]", flush=True)
            first = False

    await asyncio.gather(producer(), consumer())
    return " ".join(full_reply).strip()


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

    async with Brain() as brain:
        try:
            while True:
                wait_for_wake()
                publish("listening")
                print("> wake detected, listening...", flush=True)
                audio = record_until_silence()

                while True:
                    if audio.size == 0:
                        print("(no audio)", flush=True)
                        break

                    text = transcribe(audio)
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
                    audio = record_until_silence(max_pre_speech_ms=follow_up_window_ms)
                    if audio.size == 0:
                        print("(no follow-up)", flush=True)
                        break

                publish("idle", text="Say 'hey jarvis' to start.")
                print("[listening for 'hey jarvis']\n", flush=True)
        except KeyboardInterrupt:
            publish("idle", text="shutting down")
            print("\n[shutting down]")


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
