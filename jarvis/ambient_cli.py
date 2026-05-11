import anyio

from .ambient import get_wake_model, record_until_silence, wait_for_wake
from .brain import Brain
from .voice import speak_async, transcribe, warmup_stt


async def _loop() -> None:
    print("Jarvis ambient mode.")
    print("  Say 'Hey Jarvis' to wake. Speak. Pause. Reply will be spoken.")
    print("  Ctrl-C to exit.\n")

    print("[warming up speech-to-text]", flush=True)
    warmup_stt()
    print("[loading wake-word model]", flush=True)
    get_wake_model()
    print("[ready, listening for 'hey jarvis']\n", flush=True)

    async with Brain() as brain:
        try:
            while True:
                wait_for_wake()
                print("> wake detected, listening...", flush=True)
                audio = record_until_silence()

                if audio.size == 0:
                    print("(no audio)", flush=True)
                elif not (text := transcribe(audio)):
                    print("(no speech detected)", flush=True)
                else:
                    print(f"you> {text}", flush=True)
                    try:
                        reply = await brain.process(text)
                        print(f"jarvis> {reply}", flush=True)
                        await speak_async(reply)
                    except Exception as exc:
                        print(f"jarvis> [error: {exc}]", flush=True)

                print("[listening for 'hey jarvis']\n", flush=True)
        except KeyboardInterrupt:
            print("\n[shutting down]")


def run() -> None:
    anyio.run(_loop)


if __name__ == "__main__":
    run()
