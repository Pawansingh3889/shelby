# Shelby

A personal voice assistant built on the Claude Agent SDK with a local
Ollama fallback for offline turns. Wake-word activated, browser UI,
cross-session memory, optional Telegram bridge.

Two personas:

- **Shelby** — online path, powered by Claude (Anthropic API + Agent
  SDK + MCP tools). Calm British naval-officer voice.
- **Jarvis** — offline path, powered by Ollama (default `llama3.2`).
  Takes over automatically when the network drops.

Architecture, module map, and end-to-end turn flow are in
[`WORKFLOW.md`](WORKFLOW.md). This README covers install, run, and
the four ways to talk to it.

---

## Quick start (PowerShell)

Assumes Windows + Python 3.10+. macOS/Linux: same commands minus the
PowerShell-isms.

```powershell
# 1. Clone
git clone https://github.com/Pawansingh3889/shelby.git
cd shelby

# 2. Create venv (Python 3.10+)
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install editable (registers the shelby-* console scripts)
pip install -e .

# 4. Set your Anthropic API key for the online path
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# 5. Pick a CLI (see "Running" below)
shelby-demo
```

If PowerShell blocks `.ps1` activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## Running

Five entry points, registered as console scripts by `pyproject.toml`.

| Command | Mode | What you get |
|---|---|---|
| `shelby` | text REPL | Type at a prompt, get text back. Smallest moving parts — good for debugging the brain. |
| `shelby-voice` | push-to-talk | Press Enter to start recording, Enter again to stop. Whisper transcribes, brain replies, Edge-TTS speaks. No wake word. |
| `shelby-ambient` | always-on voice | "Hey Jarvis" wake word → record until silence → reply spoken. Terminal-only, no UI. |
| `shelby-demo` | full assistant | Ambient mode **plus** a browser UI on `http://127.0.0.1:8765` (orb, transcript, tool pills, particle sphere). The default daily-driver. |
| `shelby-memory` | memory CLI | Inspect / search / wipe the cross-session memory file. See "Memory" below. |

### `shelby` — text REPL

```powershell
shelby
# you> what time is it
# shelby> It's 4:42 PM, Captain.
# you> :q
```

### `shelby-voice` — push-to-talk

```powershell
shelby-voice
# [warming up speech-to-text]
# [ready]
# [Enter to talk, :q to quit]   <Enter>
# Recording... Enter to stop.   <speak> <Enter>
# Transcribing...
# you (heard)> what's the weather in hull
# shelby> [reply spoken aloud]
```

### `shelby-ambient` — terminal ambient mode

```powershell
shelby-ambient
# [warming up speech-to-text]
# [loading wake-word model]
# [ready, listening for 'hey jarvis']
# > wake detected, listening...
# you> set a timer for ten minutes for the kettle
# shelby> Done, Captain. Ten-minute timer for the kettle.
# > listening for follow-up (no wake needed, 5s)...
```

### `shelby-demo` — full web UI + ambient

```powershell
shelby-demo
# [serving UI at http://127.0.0.1:8765]
# [warming up speech-to-text]
# [warming up text-to-speech]
# [loading wake-word model]
# [ready, listening for 'hey jarvis']
```

The browser opens automatically. Click the orb or say "Hey Jarvis"
to wake. SSE feed updates the orb state, transcript, and tool pills
in real time.

### `shelby-memory` — inspect memory

```powershell
shelby-memory                  # print recent turns, oldest first
shelby-memory --tail 5         # last 5 turns
shelby-memory --search oven    # substring filter
shelby-memory --path           # where the memory file lives
shelby-memory --clear          # wipe (asks for confirm)
shelby-memory --clear --yes    # wipe, no prompt
```

---

## Configuration

Two layers, env vars win over the file.

### `settings.json` (persistent defaults)

Lives at `%APPDATA%\shelby\settings.json` on Windows,
`~/.shelby/settings.json` elsewhere. Auto-loaded at boot; missing
keys are fine. Example:

```json
{
  "location": "Hull",
  "github_username": "Pawansingh3889",
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
  "telegram_token": "...",
  "telegram_chat_id": "123456"
}
```

Every key becomes a `SHELBY_<UPPER>` env var. Paths get `~` expanded.

### Environment variables (override the file)

```powershell
# Required for the online (Claude) path
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# Pin a mode (skip network probe)
$env:SHELBY_FORCE_MODE = "offline"   # or "online"

# Web demo binding
$env:SHELBY_WEB_HOST = "127.0.0.1"
$env:SHELBY_WEB_PORT = "8765"

# Conversation timing
$env:SHELBY_FOLLOWUP_MS = "5000"     # follow-up window after a reply
$env:SHELBY_SILENCE_HANG_MS = "900"  # silence threshold to end a turn

# STT / TTS overrides
$env:SHELBY_STT_MODEL = "base.en"    # default tiny.en; base.en is better, slower
$env:SHELBY_VOICE = "en-GB-RyanNeural"

# Optional Telegram bridge (both must be set)
$env:SHELBY_TELEGRAM_TOKEN   = "..."
$env:SHELBY_TELEGRAM_CHAT_ID = "..."

# Extra MCP servers (name=command, comma-separated)
$env:SHELBY_MCP_SERVERS = "sql-sop=sql-sop-mcp,sql-explorer=sql-explorer-mcp"
```

---

## Offline mode (Ollama)

The Jarvis persona runs against a local Ollama daemon. Set it up
once:

```powershell
# Install + start Ollama
winget install Ollama.Ollama
ollama serve                          # leave this terminal running
ollama pull llama3.2                  # in a second terminal
```

Force offline mode to test without unplugging:

```powershell
$env:SHELBY_FORCE_MODE = "offline"
shelby-demo
```

The UI badge flips from green `SHELBY [ONLINE]` to amber
`JARVIS [OFFLINE]`. Offline tools are limited to ones that don't
need the internet (timers, memory, system control).

---

## Skills (no-code tools)

Drop a markdown file at `~/.shelby/skills/<slug>/skill.md`:

```markdown
---
name: oven-timer
description: Sets a timer for the oven with a friendly reminder
triggers: ["oven", "bake"]
---

When Captain mentions baking or the oven, offer to set a timer.
Default to 15 minutes if no duration is given. Use the set_timer tool.
```

Restart `shelby-demo`. Skills load on boot, exposed to the brain as
MCP tools. See `examples/skills/briefing-deep-dive/` for a worked
example.

---

## Tests

```powershell
pytest tests/ -q
```

32 smoke tests covering memory, net, settings, skills, syscontrol,
timers. Audio/STT/TTS code paths are not exercised in the suite —
they need a real microphone — so a green test run is necessary but
not sufficient for "it works."

---

## Diagnostic endpoints (web demo)

While `shelby-demo` is running:

```powershell
curl http://127.0.0.1:8765/health    # liveness
curl http://127.0.0.1:8765/version   # version info
curl http://127.0.0.1:8765/info      # build + persona + mode
curl http://127.0.0.1:8765/stats     # active timers, memory count, mode
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ANTHROPIC_API_KEY not set` | Online path needs the key | `$env:ANTHROPIC_API_KEY = "..."` |
| Wake word never fires | Mic permission, or openWakeWord model not loaded | Windows Settings → Privacy → Microphone → allow Python |
| `address already in use` on `:8765` | Old `shelby-demo` still running | `$env:SHELBY_WEB_PORT = "8766"` or kill the old process |
| STT garbled | `tiny.en` model is too small for your voice | `$env:SHELBY_STT_MODEL = "base.en"` |
| TTS robotic / silent | Edge-TTS hit a network error and fell back to SAPI | Check firewall lets Python reach `speech.platform.bing.com` |
| Offline mode hangs | Ollama daemon not running | Open a terminal, `ollama serve`, retry |
| `pyttsx3` install fails on Windows | Build dep missing | `pip install pyttsx3 --no-build-isolation` |
| Memory not persisted | Settings file pointing to unwritable path | `shelby-memory --path` to see, fix in `settings.json` |
| `webrtcvad-wheels` install error on macOS | Compile path needs Xcode CLT | `xcode-select --install` |
| PowerShell can't run `.venv\Scripts\Activate.ps1` | Default execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Web UI blank | SSE connection dropped | DevTools → Network → look at `/events`, refresh the page |

---

## Project layout

```
shelby/
├── pyproject.toml          # console scripts + deps
├── README.md               # this file
├── WORKFLOW.md             # architecture, turn flow, module map
├── shelby/                 # the package
│   ├── cli.py              # `shelby`        text REPL
│   ├── voice_cli.py        # `shelby-voice`  push-to-talk
│   ├── ambient_cli.py      # `shelby-ambient` terminal ambient
│   ├── web_cli.py          # `shelby-demo`   web UI + ambient
│   ├── memory_cli.py       # `shelby-memory` memory inspector
│   ├── brain.py            # ClaudeBrain (online) + @tool defs
│   ├── brain_ollama.py     # OllamaBrain (offline)
│   ├── brain_hybrid.py     # HybridBrain auto-router
│   ├── ambient.py          # wake-word + VAD recording
│   ├── voice.py            # Whisper STT + Edge-TTS/SAPI
│   ├── memory.py           # cross-session jsonl store
│   ├── settings.py         # settings.json → env var promotion
│   ├── skills.py           # OpenClaw skill loader
│   ├── jobs.py             # job-digest pipeline bridge
│   ├── timers.py           # in-memory timer store
│   ├── syscontrol.py       # open_app / open_url tools
│   ├── telegram_bridge.py  # optional Telegram bot
│   ├── net.py              # connectivity probe (30s cache)
│   ├── web.py              # Starlette app: SSE, /stats, /health
│   └── static/index.html   # single-file UI
├── examples/
│   └── skills/             # worked skill examples
└── tests/                  # pytest smoke suite
```

---

## Where state lives

| Concern | Path |
|---|---|
| Conversation memory | `%APPDATA%\shelby\memory.jsonl` (Win) / `~/.shelby/memory.jsonl` |
| Skills | `~/.shelby/skills/<slug>/skill.md` |
| Persistent config | `%APPDATA%\shelby\settings.json` (Win) / `~/.shelby/settings.json` |
| Active timers | In-memory only (don't survive restart, by design) |
| Tool result cache | In-memory, TTL-keyed |

---

## License

No license file yet. Treat as all-rights-reserved until one lands.
