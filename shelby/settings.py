"""Persistent settings loader.

Reads ~/.shelby/settings.json on import and promotes its keys into
os.environ so anything in the codebase that reads SHELBY_* env vars
picks them up without code changes. Existing real env vars always
win over the file (env vars override settings.json), so the file is
a default rather than a force.

Layout (all keys optional):

    {
      "location": "Hull",
      "github_username": "Pawansingh3889",
      "force_mode": "online",
      "model": "sonnet",
      "ollama_model": "llama3.2",
      "ollama_host": "http://127.0.0.1:11434",
      "voice": "en-GB-SoniaNeural",
      "tts_rate": "+10%",
      "stt_model": "tiny.en",
      "silence_hang_ms": 900,
      "followup_ms": 5000,
      "mcp_servers": "sql-sop=sql-sop-mcp,sql-explorer=sql-explorer-mcp",
      "skills_dir": "~/.shelby/skills",
      "memory_path": "~/.shelby/memory.jsonl",
      "github_token": "ghp_...",
      "telegram_token": "...",
      "telegram_chat_id": "123456"
    }

Keys map to SHELBY_<UPPER> env vars. Path-shaped values get ~ expanded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _settings_path() -> Path:
    override = os.environ.get("SHELBY_SETTINGS_PATH")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "shelby" / "settings.json"
    return Path.home() / ".shelby" / "settings.json"


# Keys that get path expansion (~ -> home).
_PATH_KEYS = {"skills_dir", "memory_path", "settings_path"}


def _coerce(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def load_into_env() -> dict[str, str]:
    """Read the settings file (if present) and set any missing SHELBY_*
    env vars. Returns the dict of keys that were promoted (for logging).
    """
    path = _settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    promoted: dict[str, str] = {}
    for key, value in data.items():
        if value is None or value == "":
            continue
        env_key = "SHELBY_" + key.upper()
        # Real environment wins; settings.json is a default only.
        if env_key in os.environ and os.environ[env_key]:
            continue
        coerced = _coerce(value)
        if key in _PATH_KEYS:
            coerced = str(Path(coerced).expanduser())
        os.environ[env_key] = coerced
        promoted[env_key] = coerced
    return promoted


# Run at import. This is the cheapest place to ensure every module that
# reads os.environ.get("SHELBY_*") sees the file's values.
_PROMOTED = load_into_env()
if _PROMOTED:
    print(
        f"[settings] loaded {len(_PROMOTED)} key(s) from "
        f"{_settings_path()}: {', '.join(sorted(_PROMOTED.keys()))}",
        flush=True,
    )
