import os
import platform
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import httpx


# Tiny in-memory TTL cache so a follow-up briefing within ~60s reuses
# the weather / news / github results from the previous one instead of
# re-fetching. Keyed on (tool_name, frozenset of args items).
_TOOL_CACHE: dict[tuple[str, frozenset], tuple[float, Any]] = {}


async def _cached(
    name: str,
    args: dict,
    ttl_s: float,
    fetch: Callable[[], Awaitable[Any]],
) -> Any:
    key = (name, frozenset((k, str(v)) for k, v in (args or {}).items()))
    now = time.monotonic()
    hit = _TOOL_CACHE.get(key)
    if hit is not None and now - hit[0] < ttl_s:
        return hit[1]
    value = await fetch()
    _TOOL_CACHE[key] = (now, value)
    return value

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)

try:
    from claude_agent_sdk import StreamEvent  # type: ignore
except ImportError:  # pragma: no cover
    StreamEvent = None  # type: ignore

from . import memory, syscontrol, timers


GITHUB_USERNAME = os.environ.get("SHELBY_GITHUB_USERNAME", "Pawansingh3889")
DEFAULT_LOCATION = os.environ.get("SHELBY_LOCATION", "Hull")


@tool("current_time", "Get the current local time on this computer", {})
async def current_time(args):
    now = datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M:%S %Z").strip()
    return {"content": [{"type": "text", "text": now}]}


@tool("system_info", "Get OS, machine and Python info from this computer", {})
async def system_info(args):
    info = {
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "python": platform.python_version(),
        "node": platform.node(),
    }
    text = "\n".join(f"{k}: {v}" for k, v in info.items())
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "weather",
    "Get current weather for a location. Pass location as a string, or omit for the user's home town.",
    {"location": str},
)
async def weather(args):
    location = (args.get("location") or DEFAULT_LOCATION).strip()

    async def _fetch() -> str:
        fmt = "%l: %c %t, feels like %f, humidity %h, wind %w"
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"https://wttr.in/{location}", params={"format": fmt})
        return r.text.strip() if r.status_code == 200 else f"weather lookup failed ({r.status_code})"

    text = await _cached("weather", {"location": location}, ttl_s=300.0, fetch=_fetch)
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "forecast",
    "Hourly weather forecast plus sunrise and sunset for a location, today and tomorrow. "
    "Use this when Captain is going somewhere outdoors and needs to plan: hill walking, "
    "climbing, cycling, cricket, beach, anything weather-sensitive. Pass the destination as "
    "a string, e.g. 'Hathersage', 'Stanage Edge', 'Snowdon', 'London'.",
    {"location": str, "hours": int},
)
async def forecast(args):
    location = (args.get("location") or DEFAULT_LOCATION).strip()
    hours = max(6, min(int(args.get("hours") or 14), 36))

    async with httpx.AsyncClient(timeout=15.0) as client:
        geo = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "language": "en", "format": "json"},
        )
        gdata = geo.json()
        if not gdata.get("results"):
            return {"content": [{"type": "text", "text": f"location '{location}' not found"}]}

        loc = gdata["results"][0]
        lat, lon = loc["latitude"], loc["longitude"]
        name = loc.get("name", location)
        admin = loc.get("admin1", "")
        country = loc.get("country", "")

        fc = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,weather_code,precipitation_probability,wind_speed_10m,wind_gusts_10m",
                "daily": "sunrise,sunset",
                "timezone": "auto",
                "forecast_days": 2,
            },
        )
        fd = fc.json()

    times = fd["hourly"]["time"][:hours]
    temps = fd["hourly"]["temperature_2m"][:hours]
    rain = fd["hourly"]["precipitation_probability"][:hours]
    wind = fd["hourly"]["wind_speed_10m"][:hours]
    gusts = fd["hourly"]["wind_gusts_10m"][:hours]

    sunrise = fd["daily"]["sunrise"][0].split("T")[1]
    sunset = fd["daily"]["sunset"][0].split("T")[1]

    place = ", ".join(p for p in [name, admin, country] if p)
    lines = [f"{place}", f"Sunrise {sunrise}, sunset {sunset}", "Hourly:"]
    for t, te, r, w, g in zip(times, temps, rain, wind, gusts):
        time_str = t.split("T")[1][:5]
        lines.append(f"  {time_str}  {te}°C  rain {r}%  wind {w} km/h (gust {g})")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "news_headlines",
    "Get the top N general news headlines from BBC News. Pass count to limit, default 5.",
    {"count": int},
)
async def news_headlines(args):
    count = max(1, min(int(args.get("count") or 5), 15))

    async def _fetch() -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://feeds.bbci.co.uk/news/rss.xml")
        if r.status_code != 200:
            return f"news lookup failed ({r.status_code})"
        root = ET.fromstring(r.text)
        titles = [item.findtext("title", "") for item in root.findall(".//item")][:15]
        return "\n".join(f"- {t}" for t in titles if t) or "no headlines"

    # Cache the full top-15 list; slice to requested count after.
    full = await _cached("news_headlines", {}, ttl_s=600.0, fetch=_fetch)
    if full.startswith("news lookup failed") or full == "no headlines":
        text = full
    else:
        text = "\n".join(full.splitlines()[:count])
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "github_pending",
    "Get the user's open GitHub PRs (authored), PRs awaiting their review, and assigned issues.",
    {},
)
async def github_pending(args):
    import asyncio

    user = GITHUB_USERNAME
    headers = {"Accept": "application/vnd.github+json"}
    pat = os.environ.get("SHELBY_GITHUB_TOKEN")
    if pat:
        headers["Authorization"] = f"Bearer {pat}"

    queries = [
        ("Open PRs you authored", f"is:pr is:open author:{user} archived:false"),
        ("PRs awaiting your review", f"is:pr is:open review-requested:{user} archived:false"),
        ("Issues assigned to you", f"is:issue is:open assignee:{user} archived:false"),
    ]

    async def _one(client: httpx.AsyncClient, label: str, q: str) -> str:
        r = await client.get(
            "https://api.github.com/search/issues",
            params={"q": q, "per_page": 10},
            headers=headers,
        )
        if r.status_code != 200:
            return f"{label}: lookup failed ({r.status_code})"
        data = r.json()
        count = data.get("total_count", 0)
        if count == 0:
            return f"{label}: 0"
        lines = [f"{label}: {count}"]
        for item in data.get("items", [])[:5]:
            parts = item["repository_url"].rsplit("/", 2)[-2:]
            repo = "/".join(parts)
            lines.append(f"  - {repo}#{item['number']}: {item['title']}")
        return "\n".join(lines)

    async def _fetch() -> str:
        async with httpx.AsyncClient(timeout=15.0) as client:
            sections = await asyncio.gather(*(_one(client, label, q) for label, q in queries))
        return "\n\n".join(sections)

    text = await _cached("github_pending", {"user": user}, ttl_s=120.0, fetch=_fetch)
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "set_timer",
    "Set a timer / reminder. Pass duration in seconds and a short spoken "
    "message for Captain. Examples: seconds=1200 message='check the oven', "
    "seconds=300 message='leave for the train'. Convert spoken durations to "
    "seconds before calling: '20 minutes' -> 1200, 'two and a half hours' "
    "-> 9000, '90 seconds' -> 90.",
    {"seconds": int, "message": str},
)
async def set_timer(args):
    seconds = int(args.get("seconds") or 0)
    if seconds < 1:
        return {"content": [{"type": "text", "text": "duration must be at least 1 second"}]}
    message = (args.get("message") or "").strip() or "timer"
    t = timers.schedule(seconds, message)
    pretty = timers.fmt_remaining(t)
    text = f"timer set for {pretty}: '{message}' (id {t.id})"
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "list_timers",
    "List all active timers Captain has running, with the time remaining on "
    "each. Use this when Captain asks 'what timers do I have', 'what's "
    "pending', or to know what to cancel.",
    {},
)
async def list_timers(args):
    active = timers.all_active()
    if not active:
        return {"content": [{"type": "text", "text": "no active timers"}]}
    lines = [
        f"- {t.message} (id {t.id}, {timers.fmt_remaining(t)} remaining)"
        for t in active
    ]
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "cancel_timer",
    "Cancel a running timer by id or by a substring of its message. "
    "Pass timer_id (preferred) or match (e.g. 'oven' to cancel the "
    "'check the oven' timer).",
    {"timer_id": int, "match": str},
)
async def cancel_timer(args):
    tid = args.get("timer_id")
    match = (args.get("match") or "").strip()
    if tid:
        t = timers.cancel(int(tid))
        if t is None:
            return {"content": [{"type": "text", "text": f"no timer with id {tid}"}]}
        return {"content": [{"type": "text", "text": f"cancelled timer {t.id} ('{t.message}')"}]}
    if match:
        gone = timers.cancel_by_match(match)
        if not gone:
            return {"content": [{"type": "text", "text": f"no timer matched '{match}'"}]}
        names = ", ".join(t.message for t in gone)
        return {"content": [{"type": "text", "text": f"cancelled {len(gone)} timer(s): {names}"}]}
    return {"content": [{"type": "text", "text": "pass timer_id or match"}]}


@tool(
    "open_application",
    "Launch a desktop application on this computer. Use the friendly app "
    "name Captain said. Known shortcuts include notepad, calculator, "
    "explorer, terminal, powershell, chrome, edge, firefox, vscode, "
    "spotify, discord, slack, obsidian, settings, task manager. Unknown "
    "names are dispatched via the OS shell, which usually still works.",
    {"name": str},
)
async def open_application(args):
    name = (args.get("name") or "").strip()
    ok, msg = syscontrol.open_app(name)
    return {"content": [{"type": "text", "text": msg}]}


@tool(
    "open_url",
    "Open a URL in the default browser. Accepts full URLs or bare domains "
    "like 'github.com'. Only http, https, mailto and tel schemes are "
    "allowed. Use this when Captain says 'open my GitHub', 'pull up the "
    "BBC news site', or similar.",
    {"url": str},
)
async def open_url_tool(args):
    url = (args.get("url") or "").strip()
    ok, msg = syscontrol.open_url(url)
    return {"content": [{"type": "text", "text": msg}]}


def _detect_system_claude_cli() -> Optional[Path]:
    explicit = os.environ.get("CLAUDE_AGENT_CLI_PATH")
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None

    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            base = Path(appdata) / "Claude" / "claude-code"
            if base.is_dir():
                versions = sorted(
                    (d for d in base.iterdir() if d.is_dir()),
                    key=lambda d: d.name,
                    reverse=True,
                )
                for v in versions:
                    candidate = v / "claude.exe"
                    if candidate.exists():
                        return candidate
    return None


SYSTEM_PROMPT = (
    "You are Shelby, a personal voice assistant for Captain PKT. "
    "Always address the user as 'Captain PKT' or 'Captain', never by any other name. "
    "Keep replies short, conversational and direct, suitable for being spoken aloud. "
    "\n\n"
    "OUTPUT FORMAT (critical, your reply goes through text-to-speech):\n"
    "- Never use markdown. No asterisks for bold or emphasis, no backticks for code, "
    "no hash headings, no hyphen or asterisk bullet lines, no markdown link syntax. "
    "TTS reads these characters out loud as literal noise.\n"
    "- Plain prose only. If you need to enumerate, do it conversationally ('First X, then "
    "Y, and finally Z') rather than as a bulleted list.\n"
    "- No emoji, no URLs, no code fences. If you must name a URL, say something like "
    "'BBC News dot com' rather than reading the raw address.\n"
    "\n"
    "LATENCY RULES (critical, voice is real-time):\n"
    "- Whenever you need more than one tool to answer, emit ALL of those tool calls in a "
    "single assistant turn so the runtime can run them in parallel. Never call one, wait "
    "for the result, then call the next, unless tool B genuinely depends on tool A's output.\n"
    "- For any reply that needs tools and takes more than ~1 second, your FIRST tokens "
    "should be a brief spoken acknowledgement (e.g. 'One moment Captain.' or 'Checking "
    "now.') BEFORE the tool calls. This lets the speech start playing while the tools run. "
    "Keep the acknowledgement to a single short sentence, then call the tools.\n"
    "\n"
    "BRIEFING INTENT — when Captain says 'updates', 'briefing', 'status' or similar:\n"
    "1. Speak a one-sentence acknowledgement first, e.g. 'On it Captain, pulling your "
    "briefing now.' Vary the wording.\n"
    "2. In a SINGLE turn after that acknowledgement, call IN PARALLEL: current_time, "
    "weather, news_headlines (count 3 to 5), github_pending, and Gmail search_threads "
    "(query 'is:unread in:inbox category:primary newer_than:2d', pageSize 10). Skip "
    "Google_Calendar for now. All five tool_use blocks belong in the same assistant message.\n"
    "3. Once results return, open the briefing with a time-of-day greeting, then summarise: "
    "today's date and time, weather, top news, pending GitHub work, pending email count plus "
    "1-2 most important subjects. Skip Gmail messages that are obviously marketing or "
    "automation.\n"
    "4. Close the briefing with a proactive question, varying each time, picking the most "
    "relevant: 'Captain, what's the plan today?' / 'Anywhere I should prepare you for?' / "
    "'Anything you need teed up for the morning?' / 'Where are we headed today, Captain?' "
    "Always end the briefing with a question, never just trail off.\n"
    "\n"
    "PREPARE INTENT — when Captain says 'I'm going to X', 'prepare me for X', 'heading to X', "
    "or similar:\n"
    "1. Speak a one-sentence acknowledgement first, e.g. 'Prepping you for X now, Captain.'\n"
    "2. In a SINGLE turn, call forecast(location=X, hours=14) AND current_time IN PARALLEL. "
    "If X is a feature like 'Stanage Edge' rather than a settlement, pick the nearest town "
    "that geocodes (e.g. 'Hathersage' for Stanage Edge, 'Pen-y-Pass' for Snowdon).\n"
    "3. Produce a short tactical brief covering: weather window during the visit, "
    "temperature range and what that means to wear (layers, waterproofs if rain >30%, sun "
    "if hot), wind on exposed ground (>30 km/h gust changes the day), sunset time and "
    "whether a head torch is sensible, anything activity-specific based on the destination "
    "(climbing crag, hill, beach, city) using your general knowledge.\n"
    "4. End the brief with one practical question: 'Want me to surface anything else "
    "before you leave?' or 'Anything I should pull up for the journey?'\n"
    "\n"
    "When Captain asks about time, this computer, weather, news, GitHub or email, call the "
    "matching tool rather than guessing. For single-tool questions, skip the acknowledgement "
    "and just answer. Never narrate what you are about to do for trivial replies, just answer.\n"
    "\n"
    "SQL TOOLS (if registered via SHELBY_MCP_SERVERS) — when Captain asks about "
    "data, queries, tables, rows or to 'lint this SQL', use the sql-explorer or "
    "sql-sop tools. Read-only queries only via sql-explorer. Always summarise "
    "results conversationally (e.g. 'Captain, we shipped 247 orders yesterday, "
    "up 12 from the day before') rather than dumping raw rows. Cap any "
    "list-read-out at 3 items.\n"
    "\n"
    "SYSTEM CONTROL — when Captain says 'open X', 'launch X', 'pull up X':\n"
    "1. If X is an application (Chrome, notepad, terminal, Spotify, VS Code, "
    "task manager, etc.), call open_application(name=X).\n"
    "2. If X is a website or URL (GitHub, BBC, gmail.com, etc.), call "
    "open_url(url=X). Bare domains work: open_url(url='github.com').\n"
    "3. Confirm in one short sentence: 'On it Captain.' or 'Opening Chrome.' "
    "Don't repeat the URL or app name back verbatim.\n"
    "\n"
    "TIMER INTENT — when Captain says 'remind me in X to Y', 'set a timer for X', "
    "'wake me in X minutes' or similar:\n"
    "1. Convert the spoken duration to seconds. '20 minutes' -> 1200, 'an hour' -> 3600, "
    "'90 seconds' -> 90, 'two and a half hours' -> 9000. Strip filler words from the "
    "message ('to check the oven' -> 'check the oven').\n"
    "2. Call set_timer(seconds=N, message='short reminder'). Confirm in one short "
    "sentence: 'Timer set for 20 minutes Captain' (no need to repeat the reminder text "
    "verbatim, Shelby will say it when the timer fires).\n"
    "3. If Captain asks what timers are pending, call list_timers and read them out "
    "conversationally. If Captain says 'cancel the oven timer', call "
    "cancel_timer(match='oven').\n"
    "\n"
    "WEB SEARCH — when Captain asks about restaurants, places, businesses, events, prices, "
    "live facts, anything time-sensitive that isn't covered by the named tools, use the "
    "WebSearch tool. Default location context is Hull, UK unless Captain specifies otherwise. "
    "Pick a couple of the best results and summarise them in one or two short sentences "
    "suitable for being spoken aloud. Avoid reading long lists; offer the top option plus "
    "one alternative and ask if Captain wants more detail."
)


class ClaudeBrain:
    """Online brain backed by Claude via the Agent SDK and Max OAuth.

    The original `Brain` name remains as a module-level alias for
    backward compatibility with anything still importing it directly.
    For new code, prefer ClaudeBrain (explicit) or HybridBrain
    (auto-routing with Ollama fallback) from brain_hybrid.
    """

    # Exposed for HybridBrain so it can announce mode + persona.
    name = "Shelby"
    mode = "online"

    def __init__(self, extra_mcp_servers: Optional[dict] = None) -> None:
        # Force Claude Code to use Max OAuth, never the metered API path.
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

        local = create_sdk_mcp_server(
            name="shelby-tools",
            version="0.1.0",
            tools=[
                current_time, system_info, weather, forecast,
                news_headlines, github_pending,
                set_timer, list_timers, cancel_timer,
                open_application, open_url_tool,
            ],
        )
        servers = {"shelby": local}

        # External MCP servers via SHELBY_MCP_SERVERS env var:
        # comma-separated "name=command [arg ...]" entries. Examples:
        #   SHELBY_MCP_SERVERS=sql-sop=sql-sop-mcp,sql-explorer=sql-explorer-mcp
        # The SDK runs each as a stdio child process. setting_sources=["user"]
        # also pulls in anything already configured in claude.ai settings, so
        # this env var is the second registration path for servers Captain
        # wants attached to Shelby specifically.
        external_spec = os.environ.get("SHELBY_MCP_SERVERS", "").strip()
        external_allowed: list[str] = []
        if external_spec:
            for entry in external_spec.split(","):
                entry = entry.strip()
                if not entry or "=" not in entry:
                    continue
                name, command_line = entry.split("=", 1)
                name = name.strip()
                parts = command_line.strip().split()
                if not parts:
                    continue
                servers[name] = {
                    "type": "stdio",
                    "command": parts[0],
                    "args": parts[1:],
                }
                # Allow all tools from this server. Specific names land at
                # mcp__<name>__<tool> but we don't know them in advance, so
                # we authorise the wildcard prefix.
                external_allowed.append(f"mcp__{name}__*")
                print(f"[mcp] registering external server '{name}' -> {parts[0]}", flush=True)

        if extra_mcp_servers:
            servers.update(extra_mcp_servers)

        # Prepend rolling cross-session memory so the model has continuity.
        # Empty string if there's no history yet (first run).
        memory_preamble = memory.format_for_prompt()
        system_prompt = (
            (memory_preamble + "\n\n" + SYSTEM_PROMPT)
            if memory_preamble
            else SYSTEM_PROMPT
        )

        cli_path = _detect_system_claude_cli()
        self._options = ClaudeAgentOptions(
            mcp_servers=servers,
            allowed_tools=[
                "mcp__shelby__current_time",
                "mcp__shelby__system_info",
                "mcp__shelby__weather",
                "mcp__shelby__forecast",
                "mcp__shelby__news_headlines",
                "mcp__shelby__github_pending",
                "mcp__shelby__set_timer",
                "mcp__shelby__list_timers",
                "mcp__shelby__cancel_timer",
                "mcp__shelby__open_application",
                "mcp__shelby__open_url",
                "mcp__claude_ai_Gmail__search_threads",
                "mcp__claude_ai_Gmail__get_thread",
                "mcp__claude_ai_Google_Calendar__list_events",
                "mcp__claude_ai_Google_Calendar__list_calendars",
                "WebSearch",
                "WebFetch",
                *external_allowed,
            ],
            disallowed_tools=["Bash", "Edit", "Write", "Read"],
            system_prompt=system_prompt,
            cli_path=cli_path,
            setting_sources=["user"],
            include_partial_messages=True,
            model=os.environ.get("SHELBY_MODEL", "sonnet"),
        )
        self._client: Optional[ClaudeSDKClient] = None

    async def __aenter__(self):
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, tb)
            self._client = None

    async def process_stream(self, text: str) -> AsyncIterator[dict]:
        """Yield typed events as Claude streams them back.

        Each event is a dict with a 'type' field:
          - {"type": "text", "text": "..."}      a text token/delta
          - {"type": "tool", "name": "..."}      a tool_use block starting

        With include_partial_messages enabled, the SDK emits StreamEvent
        objects whose 'event' dict carries Anthropic-style streaming events.
        We surface text_delta and tool_use block starts so callers can drive
        both TTS and a live "what's Shelby doing" status pill.
        """
        if self._client is None:
            raise RuntimeError("Brain must be used as an async context manager")

        await self._client.query(text)
        async for msg in self._client.receive_response():
            if StreamEvent is None or not isinstance(msg, StreamEvent):
                continue
            event = getattr(msg, "event", None) or {}
            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    chunk = delta.get("text", "")
                    if chunk:
                        yield {"type": "text", "text": chunk}
            elif etype == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    name = block.get("name") or ""
                    if name:
                        yield {"type": "tool", "name": name}

    async def process(self, text: str) -> str:
        """Backward-compatible: collect the full text reply as one string."""
        parts: list[str] = []
        async for event in self.process_stream(text):
            if event.get("type") == "text":
                parts.append(event.get("text", ""))
        return "".join(parts).strip()


# Backward-compat alias. Old code does .
Brain = ClaudeBrain
