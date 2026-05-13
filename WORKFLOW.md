# Shelby Workflow

End-to-end map of how a voice turn flows through the system, what each
module owns, and where to look when something breaks.

## High-level architecture

```
                   ┌──────────────────────────────────────────────┐
                   │                 Browser UI                    │
                   │   index.html  (orb, HUD, particle sphere,     │
                   │   SSE consumer, /stats poller, /wake POSTer)  │
                   └─────────────┬───────────────▲────────────────┘
                                 │ SSE: /events  │ POST /wake
                                 │ GET  /stats   │
                                 ▼               │
   ┌──────────────────────────────────────────────────────────────┐
   │                       web.py (Starlette)                      │
   │  Routes: / /events /wake /stats /health /version /info        │
   │          /manifest.webmanifest /icon.svg                      │
   │  publish(state, text, words, levels, doing, mode, persona)    │
   └─────────────┬─────────────────────────────────────▲──────────┘
                 │                                     │
                 │ run_in_thread (uvicorn)             │ publish()
                 ▼                                     │
   ┌──────────────────────────────────────────────────────────────┐
   │                    web_cli.py  (the conductor)                │
   │  _loop(): wake → record → transcribe → _stream_speak → repeat │
   │  AUDIO_LOCK gates TTS so timer chimes don't overlap            │
   │  pub() helper attaches mode + persona to every publish         │
   └──┬─────────────┬───────────────┬────────────────┬─────────────┘
      │             │               │                │
      │             │               │                │
      ▼             ▼               ▼                ▼
 ┌─────────┐  ┌──────────┐    ┌────────────┐   ┌────────────┐
 │ ambient │  │  voice   │    │   brain_   │   │   timers   │
 │ (wake + │  │ (STT,    │    │   hybrid   │   │  + bridge  │
 │  VAD)   │  │ TTS, RMS)│    │            │   │            │
 └─────────┘  └──────────┘    └──┬──────┬──┘   └────────────┘
                                 │      │
                            online│      │offline
                                 ▼      ▼
                          ┌──────────┐ ┌────────────┐
                          │  Claude  │ │   Ollama   │
                          │  (SHELBY)│ │  (JARVIS)  │
                          └──────────┘ └────────────┘
```

## Module ownership

| File | Responsibility |
|---|---|
| `ambient.py` | Wake-word detection (openWakeWord), VAD recording (webrtcvad), `MANUAL_WAKE` event for click-to-wake |
| `voice.py` | Whisper STT, Edge-TTS / SAPI fallback, RMS amplitude envelope, markdown sanitiser |
| `brain.py` | `ClaudeBrain` (online, Shelby persona, Claude Agent SDK + MCP tools) and all `@tool` definitions |
| `brain_ollama.py` | `OllamaBrain` (offline, Jarvis persona, local Llama via Ollama HTTP API) |
| `brain_hybrid.py` | `HybridBrain` auto-routes per turn based on `net.is_online()` |
| `net.py` | Connectivity probe (Anthropic + Cloudflare HEAD, 30s cache) |
| `memory.py` | Cross-session jsonl memory store, `format_for_prompt()` injects into system prompt |
| `skills.py` | OpenClaw-compatible loader: `~/.shelby/skills/*/skill.md` → MCP tools |
| `timers.py` | In-memory timer store + `watch_loop` coroutine |
| `syscontrol.py` | `open_app` (curated allowlist + shell fallback) + `open_url` (scheme-restricted) |
| `settings.py` | Loads `~/.shelby/settings.json` into env vars at boot (defaults, env wins) |
| `telegram_bridge.py` | Optional Hermes-style text bridge for chatting with Shelby from your phone |
| `web.py` | Starlette app: SSE, stats, health, version, info, manifest, icon |
| `web_cli.py` | Main conductor: wires everything, runs `_loop`, owns `AUDIO_LOCK` |
| `memory_cli.py` | `shelby-memory` CLI (view / search / clear memory file) |
| `static/index.html` | Single-file UI: orb, HUD chrome, particle sphere, mode badge, transcript modal |

## End-to-end turn flow

A turn = "Hey Jarvis, what's the weather" → reply audible.

1. **Browser** has an open SSE connection at `/events` and is polling
   `/stats` every 2s. State badge shows `idle`.
2. **`ambient.wait_for_wake()`** (in a thread via `to_thread`) polls
   the microphone callback queue, running each chunk through
   openWakeWord. Either the model scores ≥0.5 or the user clicked the
   "Wake Shelby" button which set `MANUAL_WAKE`.
3. **`web_cli._loop`** sees wake fire, calls `pub("listening")` →
   browser flips orb cyan + corner brackets light up + ring brightens.
4. **`ambient.record_until_silence()`** records mic until VAD detects
   `SILENCE_HANG_MS` (900ms default) of silence after speech started.
5. **`voice.transcribe()`** runs faster-whisper on the PCM (vad_filter
   off since we already trimmed). Returns "what's the weather".
6. **`pub("thinking", text='"what's the weather"')`** → browser shows
   the transcript and switches state to thinking (purple).
7. **`_stream_speak(brain, "what's the weather")`** starts:
   - **Producer task** calls `brain.process_stream(prompt)`.
   - **`HybridBrain.process_stream`**:
     - Calls `net.is_online()` (cached, usually instant).
     - If verdict changed since last turn, emits `{type:"mode"}` event.
     - Delegates to either `ClaudeBrain` or `OllamaBrain`.
   - **`ClaudeBrain.process_stream`** (online path):
     - Anthropic streaming events arrive via the Agent SDK.
     - `content_block_delta` text_delta events → `{type:"text"}`
     - `content_block_start` tool_use events → `{type:"tool", name}`
     - Tools run via in-process MCP server (current_time, weather,
       news_headlines, github_pending, set_timer, search_memory,
       open_application, OpenClaw skills, etc.) plus any external
       MCP servers from `SHELBY_MCP_SERVERS`.
   - **Producer** routes events:
     - `tool` → `pub("thinking", doing="Checking weather")` → purple
       pill appears under orb
     - `text` → accumulates into a buffer, splits on
       `[.!?,;:]` boundaries, sanitises markdown, queues each
       complete chunk
   - **Consumer task** pulls chunks:
     - Acquires `AUDIO_LOCK` (timers can't chime mid-speech)
     - `voice.speak_async(chunk)` synthesises via Edge-TTS, computes
       RMS envelope, fires `on_start(words, levels)` BEFORE playback
     - `on_start` callback: `pub("speaking", text, words, levels,
       append=not_first)` → browser appends words to body, schedules
       per-word highlight from `offset_ms`, schedules per-bar
       envelope updates, kicks the particle sphere energy
     - `sd.play(pcm, sr)` plays through speakers
8. **After speak loop completes**, `_loop` saves the turn to memory
   via `memory.append_turn(user, assistant)`.
9. **Follow-up window**: `pub("listening", text="follow-up?")` →
   browser starts the 5s countdown ring. If user speaks within the
   window, jump to step 5 without needing wake word again.
10. **Otherwise**: `pub("idle", ...)` → ring fades, badge returns to
    idle, browser ready for next wake.

## Online vs offline routing

```
                  start of turn
                       │
                       ▼
                ┌──────────────┐
                │ SHELBY_FORCE │   ── set?
                │   _MODE      │──────────► use the pinned mode
                └──────┬───────┘
                       │ unset
                       ▼
                ┌──────────────┐
                │ net.is_online│   ── cached 30s
                └──────┬───────┘
                       │
              ┌────────┴────────┐
              │                 │
            True               False
              │                 │
              ▼                 ▼
        ┌──────────┐      ┌──────────┐
        │ SHELBY   │      │  JARVIS  │
        │ ClaudeBn │      │ OllamaBn │
        │  +tools  │      │ text only│
        └──────────┘      └──────────┘
```

Mode swap mid-session: HybridBrain holds the active brain open and
only tears it down when the verdict actually changes. Browser sees
a `{type:"mode"}` event before the first text delta and re-skins the
topbar badge (`SHELBY [ONLINE]` green / `JARVIS [OFFLINE]` amber).

## Where state lives

| Concern | Lives in |
|---|---|
| Conversation memory across sessions | `~/.shelby/memory.jsonl` (or `%APPDATA%\shelby\memory.jsonl`) |
| Skills | `~/.shelby/skills/<slug>/skill.md` |
| Persistent config | `~/.shelby/settings.json` |
| Active timers | In-memory only (don't survive restart, by design) |
| Tool result cache | In-memory, TTL keyed (weather 5m, news 10m, github 2m) |
| Connectivity verdict | In-memory, 30s TTL |
| Three.js particle state | Browser only (frame loop, not persisted) |

## How to add a new tool

1. Add an `@tool("name", "description", schema)` function in `brain.py`
2. Register it in the `create_sdk_mcp_server(tools=[...])` list
3. Add `"mcp__shelby__name"` to `allowed_tools`
4. Add a friendly label in `web_cli._TOOL_LABELS` so the UI pill is
   nice (e.g. `"Checking the oven"` instead of `"oven_check"`)
5. Optionally add a section to the system prompt teaching the
   intent ("when Captain says X, call name(...)")
6. Add a test in `tests/test_<thing>.py`

## How to add a new skill (no code, OpenClaw style)

1. Create `~/.shelby/skills/my-skill/skill.md`
2. Frontmatter: `name`, `description`, `triggers`
3. Body: the instructions Shelby should follow
4. Restart shelby-demo. Skills load on boot.

## Diagnostic commands

```powershell
# What's the server's state and where is everything?
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/version
curl http://127.0.0.1:8765/info
curl http://127.0.0.1:8765/stats

# What does Shelby remember?
shelby-memory
shelby-memory --search oven
shelby-memory --tail 5

# Wipe memory
shelby-memory --clear

# Run smoke tests
pytest tests/ -q

# Force offline mode for testing
$env:SHELBY_FORCE_MODE = "offline"
shelby-demo
```

## File hot-paths to know

| Symptom | Look at |
|---|---|
| Wake word never fires | `ambient.wait_for_wake`, mic permissions, openWakeWord model |
| STT garbled | `voice.transcribe`, `SHELBY_STT_MODEL` (try `base.en`) |
| TTS robotic | Edge-TTS failed and fell back to SAPI; check internet / firewall |
| Tools don't fire | System prompt intent missing, or tool not in `allowed_tools` |
| Tool pill never shows | `_TOOL_LABELS` mapping in web_cli, or `process_stream` event routing |
| UI doesn't update | Browser SSE connection dropped, check `/events` in DevTools |
| Memory not injected | `SHELBY_MEMORY_PATH` not writable; check `shelby-memory --path` |
| Skills not loaded | `~/.shelby/skills/*/skill.md` format, restart server |
| Offline mode hangs | Ollama daemon not running (`ollama serve`), or model not pulled |

## Today's commit log (2026-05-12)

```
b991f7f test: pytest smoke suite (32 tests) for ...
c27ceec feat(cli): shelby-memory command for inspecting / wiping memory
9d90193 feat(settings): persistent ~/.shelby/settings.json loader
d529c68 feat(brain): search_memory + clear_memory MCP tools
9a8397e feat(web): /health, /version, /info status endpoints
0a82078 feat(telegram): Hermes-style remote text bridge (scaffolded)
15ff63c feat(brain): morning_brief MCP tool wraps Pawan's morning-brief CLI
e1a0731 feat(skills): OpenClaw-compatible skill loader
e7ab570 feat(memory): cross-session conversation memory
f17ad0f feat(brain): external MCP server registration via SHELBY_MCP_SERVERS
```
