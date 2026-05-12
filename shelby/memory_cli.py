"""Tiny CLI for inspecting / managing Shelby's cross-session memory.

Usage:
    shelby-memory               # print recent turns, oldest first
    shelby-memory --tail 5      # only the most recent 5 turns
    shelby-memory --search oven # substring search across all turns
    shelby-memory --path        # print the memory file path
    shelby-memory --clear       # wipe everything (asks for confirmation)
    shelby-memory --clear --yes # wipe everything, no prompt

Reuses the same memory.py module the brain uses, so the CLI sees
exactly what Shelby would inject into the system prompt.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import memory


def _fmt_turn(t: dict) -> str:
    ts = t.get("ts", 0)
    when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"
    u = (t.get("user") or "").strip()
    a = (t.get("assistant") or "").strip()
    lines = [f"[{when}]"]
    if u:
        lines.append(f"  you:    {u}")
    if a:
        lines.append(f"  shelby: {a}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="shelby-memory",
        description="Inspect or wipe Shelby's cross-session conversation memory.",
    )
    parser.add_argument("--tail", type=int, default=None,
                        help="Only show the most recent N turns")
    parser.add_argument("--search", type=str, default=None,
                        help="Filter to turns containing this substring")
    parser.add_argument("--path", action="store_true",
                        help="Print the memory file path and exit")
    parser.add_argument("--clear", action="store_true",
                        help="Delete the memory file")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt on --clear")
    args = parser.parse_args(argv)

    if args.path:
        print(memory._memory_path())
        return 0

    if args.clear:
        path = memory._memory_path()
        if not path.exists():
            print("memory already empty")
            return 0
        if not args.yes:
            try:
                resp = input(f"wipe {path}? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\naborted")
                return 1
            if resp not in ("y", "yes"):
                print("aborted")
                return 1
        memory.clear()
        print("memory cleared")
        return 0

    turns = memory.load_recent(n=memory.MAX_LINES)
    if args.search:
        needle = args.search.lower()
        turns = [
            t for t in turns
            if needle in (t.get("user", "") + " " + t.get("assistant", "")).lower()
        ]
    if args.tail is not None:
        turns = turns[-max(0, args.tail):]

    if not turns:
        print("no memory")
        return 0

    for t in turns:
        print(_fmt_turn(t))
        print()
    print(f"---\n{len(turns)} turn(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
