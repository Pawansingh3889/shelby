from __future__ import annotations

import os
import threading
import time
import webbrowser

import anyio
import uvicorn

from .ambient import get_wake_model, record_until_silence, wait_for_wake
from .brain import Brain
from .voice import speak_async, transcribe, warmup_stt
from .web import app, publish


HOST = os.environ.get("SHELBY_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHELBY_WEB_PORT", "8765"))


def _serve() -> None:
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.run()


async def _loop() -> None:
    follow_up_window_ms = int(os.environ.get("SHELBY_FOLLOWUP_MS", "5000"))

    print(f"[serving UI at http://{HOST}:{PORT}]", flush=True)
    print("[warming up speech-to-text]", flush=True)
    warmup_stt()
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
                        reply = await brain.process(text)
                    except Exception as exc:
                        print(f"shelby> [error: {exc}]", flush=True)
                        publish("idle", text=f"error: {exc}")
                        break

                    print(f"shelby> {reply}", flush=True)

                    async def _publish_with_words(words):
                        publish("speaking", text=reply, words=words)

                    await speak_async(reply, on_start=_publish_with_words)

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
