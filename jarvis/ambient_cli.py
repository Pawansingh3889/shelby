import os

import anyio

from .ambient import get_wake_model, record_until_silence, wait_for_wake
from .brain import Brain
from .voice import speak_async, transcribe, warmup_stt


async def _loop() -> None:
    print("Shelby ambient mode.")
    print("  Say 'Computer' to wake. Speak. Pause. Reply will be spoken.")
    print("  Ctrl-C to exit.\n")

    print("[warming up speech-to-text]", flush=True)
    warmup_stt()
    print("[loading wake-word model]", flush=True)
    get_wake_model()
    print("[ready, listening for 'computer']\n", flush=True)

    follow_up_window_ms = int(os.environ.get("JARVIS_FOLLOWUP_MS", "5000"))

    async with Brain() as brain:
        try:
            while True:
                wait_for_wake()
                print("> wake detected, listening...", flush=True)
                audio = record_until_silence()

                while True:
                    if audio.size == 0:
                        if audio is not None:
                            print("(no audio)", flush=True)
                        break

                    text = transcribe(audio)
                    if not text:
                        print("(no speech detected)", flush=True)
                        break

                    print(f"you> {text}", flush=True)
                    try:
                        reply = await brain.process(text)
                    except Exception as exc:
                        print(f"shelby> [error: {exc}]", flush=True)
                        break

                    print(f"shelby> {reply}", flush=True)
                    await speak_async(reply)

                    print(f"> listening for follow-up (no wake needed, {follow_up_window_ms // 1000}s)...", flush=True)
                    audio = record_until_silence(max_pre_speech_ms=follow_up_window_ms)
                    if audio.size == 0:
                        print("(no follow-up)", flush=True)
                        break

                print("[listening for 'computer']\n", flush=True)
        except KeyboardInterrupt:
            print("\n[shutting down]")


def run() -> None:
    anyio.run(_loop)


if __name__ == "__main__":
    run()
