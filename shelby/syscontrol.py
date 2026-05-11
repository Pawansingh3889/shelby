"""System control primitives for Shelby's open_application / open_url tools.

Curated allowlist of friendly names → launch commands so Captain can say
"open Chrome" or "open my terminal" without exposing arbitrary shell
execution to the model. Names not in the allowlist fall back to Windows'
`start` shell resolution, which handles installed apps via their
registered protocols / app paths (e.g. `start spotify`).

Keeping this in its own module makes it easy to extend with platform
branches later (macOS `open -a`, Linux `xdg-open`).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from urllib.parse import urlparse


# Friendly-name to spec. Spec can be:
# - a single executable filename ("notepad.exe") -> launched via Popen
# - a list ["start", "thing"] -> dispatched through the shell
# - a URI scheme ("spotify:") -> opened via webbrowser / start
APPS: dict[str, list[str] | str] = {
    "notepad":     "notepad.exe",
    "calculator":  "calc.exe",
    "calc":        "calc.exe",
    "explorer":    "explorer.exe",
    "files":       "explorer.exe",
    "terminal":    "wt.exe",
    "powershell":  "powershell.exe",
    "chrome":      ["start", "chrome"],
    "edge":        ["start", "msedge"],
    "firefox":     ["start", "firefox"],
    "vscode":      "code",
    "code":        "code",
    "spotify":     ["start", "spotify:"],
    "discord":     ["start", "discord:"],
    "slack":       ["start", "slack:"],
    "obsidian":    ["start", "obsidian:"],
    "settings":    ["start", "ms-settings:"],
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
}


def _normalise(name: str) -> str:
    return (name or "").strip().lower()


def known_app(name: str) -> bool:
    return _normalise(name) in APPS


def list_known() -> list[str]:
    return sorted(APPS.keys())


def open_app(name: str) -> tuple[bool, str]:
    """Launch the named application. Returns (ok, message).

    On Windows we try the curated allowlist first, then fall back to
    `start <name>` which resolves against App Paths and protocol handlers.
    """
    raw = (name or "").strip()
    if not raw:
        return False, "no application name given"

    key = _normalise(raw)
    spec = APPS.get(key)
    system = platform.system()

    try:
        if spec is None:
            # Generic fallback: Windows `start`, macOS `open -a`, Linux xdg-open.
            if system == "Windows":
                subprocess.Popen(
                    ["cmd", "/c", "start", "", raw],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            elif system == "Darwin":
                subprocess.Popen(["open", "-a", raw])
            else:
                if shutil.which("xdg-open"):
                    subprocess.Popen(["xdg-open", raw])
                else:
                    return False, f"don't know how to launch '{raw}' on this platform"
        elif isinstance(spec, str):
            # Resolve PATH ourselves to give a clean error if missing.
            resolved = shutil.which(spec) or spec
            subprocess.Popen(
                [resolved],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            # List form: dispatch through the shell so things like `start chrome`
            # resolve via App Paths.
            if system == "Windows":
                subprocess.Popen(
                    ["cmd", "/c", *spec],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                subprocess.Popen(spec)
        return True, f"launching {raw}"
    except FileNotFoundError:
        return False, f"could not find '{raw}' on this machine"
    except Exception as exc:
        return False, f"launch failed: {exc}"


# Schemes we'll allow into open_url. http(s) is the obvious one; mailto
# and tel feel safe too. Everything else is rejected so the model can't
# coax Shelby into firing arbitrary protocol handlers.
_URL_OK_SCHEMES = {"http", "https", "mailto", "tel"}


def open_url(url: str) -> tuple[bool, str]:
    """Open a URL in the default browser. Returns (ok, message)."""
    raw = (url or "").strip()
    if not raw:
        return False, "no url given"
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme:
        # Explicit scheme: must be allowlisted. Catches javascript:, file:,
        # data:, ssh:, etc. before they reach the OS.
        if scheme not in _URL_OK_SCHEMES:
            return False, f"refusing to open '{scheme}' urls"
    else:
        # Bare domain like "github.com" or path-only: prepend https.
        raw = "https://" + raw
        parsed = urlparse(raw)
        if not parsed.netloc:
            return False, f"could not parse '{url}' as a URL"
    try:
        if platform.system() == "Windows":
            os.startfile(raw)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", raw])
        else:
            subprocess.Popen(["xdg-open", raw])
        return True, f"opening {parsed.netloc or raw}"
    except Exception as exc:
        return False, f"open url failed: {exc}"
