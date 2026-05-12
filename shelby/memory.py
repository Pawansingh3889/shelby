"""Cross-session memory for Shelby.

Persists a rolling log of recent (you / shelby) turns to disk so Shelby
remembers what Captain was doing last time. Stored at:

    %APPDATA%/shelby/memory.jsonl     (Windows)
    ~/.shelby/memory.jsonl            (macOS / Linux)

Append-only JSONL. On boot the most recent ~20 turns are formatted as a
"PREVIOUS CONVERSATION CONTEXT" preamble that gets injected into the
brain's system prompt, so the model can reference earlier facts ("the
stove timer you mentioned this morning", "the briefing we did yesterday").

Capped at 200 lines on disk; older lines pruned at append time. That's
~20-50 conversations of context, plenty for "remember what we did last
session" without context-window bloat.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


MAX_LINES = 200
RECENT_TURNS_FOR_PROMPT = 20


def _config_dir() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "shelby"
    return Path.home() / ".shelby"


def _memory_path() -> Path:
    override = os.environ.get("SHELBY_MEMORY_PATH")
    if override:
        return Path(override)
    return _config_dir() / "memory.jsonl"


def _ensure_dir() -> Path:
    path = _memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_turn(user: str, assistant: str) -> None:
    """Record one round trip. Trims the file to MAX_LINES on each append."""
    user = (user or "").strip()
    assistant = (assistant or "").strip()
    if not user and not assistant:
        return
    path = _ensure_dir()
    line = json.dumps({
        "ts": int(time.time()),
        "user": user[:800],         # bound a single line
        "assistant": assistant[:1500],
    }, ensure_ascii=False)

    existing: list[str] = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                existing = f.readlines()
        except OSError:
            existing = []

    existing.append(line + "\n")
    if len(existing) > MAX_LINES:
        existing = existing[-MAX_LINES:]
    with path.open("w", encoding="utf-8") as f:
        f.writelines(existing)


def load_recent(n: int = RECENT_TURNS_FOR_PROMPT) -> list[dict]:
    """Return the most recent n turns from disk (oldest first)."""
    path = _memory_path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def format_for_prompt(n: int = RECENT_TURNS_FOR_PROMPT) -> str:
    """Render recent turns as a system-prompt preamble.

    Returns empty string if there's no history yet, so the brain can
    just concatenate without a conditional.
    """
    turns = load_recent(n)
    if not turns:
        return ""

    lines = [
        "PREVIOUS CONVERSATION CONTEXT (last "
        f"{len(turns)} turns from earlier sessions, oldest first):"
    ]
    for t in turns:
        ts = t.get("ts")
        when = (
            datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            if ts else "?"
        )
        u = (t.get("user") or "").strip()
        a = (t.get("assistant") or "").strip()
        if u:
            lines.append(f"[{when}] Captain: {u}")
        if a:
            lines.append(f"[{when}] You: {a}")
    lines.append(
        "---\n"
        "Use this only when Captain references something earlier. Don't "
        "recap unprompted; it's background only.\n"
    )
    return "\n".join(lines)


def clear() -> None:
    """Wipe memory. Useful for testing or 'forget everything' command."""
    path = _memory_path()
    if path.exists():
        path.unlink()
