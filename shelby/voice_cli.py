import anyio

from .brain import Brain
from .voice import Recorder, speak_async, transcribe, warmup_stt


async def _loop() -> None:
    print("Shelby voice mode.")
    print("  Enter to start recording, Enter again to stop and send.")
    print("  Type ':q' (then Enter) to exit.\n")

    print("[warming up speech-to-text]", flush=True)
    warmup_stt()
    print("[ready]\n", flush=True)

    rec = Recorder()
    async with Brain() as brain:
        while True:
            cmd = input("[Enter to talk, :q to quit] ").strip()
            if cmd in {":q", ":quit", "exit"}:
                return

            try:
                rec.start()
            except Exception as exc:
                print(f"[mic error: {exc}]")
                return

            print("Recording... Enter to stop.", flush=True)
            input()
            audio = rec.stop()

            if audio.size == 0:
                print("(no audio captured)")
                continue

            print("Transcribing...", flush=True)
            text = transcribe(audio)
            if not text:
                print("(no speech detected)")
                continue

            print(f"you (heard)> {text}")

            try:
                reply = await brain.process(text)
            except Exception as exc:
                print(f"shelby> [error: {exc}]")
                continue

            print(f"shelby> {reply}")
            await speak_async(reply)


def run() -> None:
    anyio.run(_loop)


if __name__ == "__main__":
    run()
