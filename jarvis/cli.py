import anyio

from .brain import Brain


async def _loop() -> None:
    print("Shelby brain (text mode). Type :q or Ctrl-D to exit.")
    async with Brain() as brain:
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if text in {":q", ":quit", "exit"}:
                return
            if not text:
                continue
            try:
                reply = await brain.process(text)
            except Exception as exc:
                print(f"shelby> [error: {exc}]")
                continue
            print(f"shelby> {reply}")


def run() -> None:
    anyio.run(_loop)


if __name__ == "__main__":
    run()
