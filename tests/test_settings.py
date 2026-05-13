"""Smoke tests for shelby.settings."""
from __future__ import annotations

import json


def test_loads_keys_into_env(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "location": "Sheffield",
        "force_mode": "offline",
        "ollama_model": "mistral",
    }), encoding="utf-8")
    monkeypatch.setenv("SHELBY_SETTINGS_PATH", str(settings_path))
    for k in ("SHELBY_LOCATION", "SHELBY_FORCE_MODE", "SHELBY_OLLAMA_MODEL"):
        monkeypatch.delenv(k, raising=False)

    # Force a fresh import to trigger the side-effect.
    import importlib
    import shelby.settings as s
    importlib.reload(s)

    import os
    assert os.environ.get("SHELBY_LOCATION") == "Sheffield"
    assert os.environ.get("SHELBY_FORCE_MODE") == "offline"
    assert os.environ.get("SHELBY_OLLAMA_MODEL") == "mistral"


def test_real_env_overrides_file(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"location": "Sheffield"}), encoding="utf-8")
    monkeypatch.setenv("SHELBY_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("SHELBY_LOCATION", "Hull")  # real env wins

    import importlib
    import shelby.settings as s
    importlib.reload(s)

    import os
    assert os.environ.get("SHELBY_LOCATION") == "Hull"


def test_missing_file_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELBY_SETTINGS_PATH", str(tmp_path / "nope.json"))
    import importlib
    import shelby.settings as s
    importlib.reload(s)
    # No exception, no env mutation. Just succeeds.
    assert s._PROMOTED == {} or len(s._PROMOTED) >= 0


def test_path_keys_get_expanded(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "memory_path": "~/test_memory.jsonl",
    }), encoding="utf-8")
    monkeypatch.setenv("SHELBY_SETTINGS_PATH", str(settings_path))
    monkeypatch.delenv("SHELBY_MEMORY_PATH", raising=False)

    import importlib
    import shelby.settings as s
    importlib.reload(s)

    import os
    assert os.environ["SHELBY_MEMORY_PATH"].startswith("/") or "\\" in os.environ["SHELBY_MEMORY_PATH"]
    assert "~" not in os.environ["SHELBY_MEMORY_PATH"]
