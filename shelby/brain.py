import os
import platform
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx

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
    fmt = "%l: %c %t, feels like %f, humidity %h, wind %w"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"https://wttr.in/{location}", params={"format": fmt})
    text = r.text.strip() if r.status_code == 200 else f"weather lookup failed ({r.status_code})"
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
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("https://feeds.bbci.co.uk/news/rss.xml")
    if r.status_code != 200:
        return {"content": [{"type": "text", "text": f"news lookup failed ({r.status_code})"}]}
    root = ET.fromstring(r.text)
    titles = [item.findtext("title", "") for item in root.findall(".//item")][:count]
    text = "\n".join(f"- {t}" for t in titles if t) or "no headlines"
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

    async with httpx.AsyncClient(timeout=15.0) as client:
        sections = await asyncio.gather(*(_one(client, label, q) for label, q in queries))

    return {"content": [{"type": "text", "text": "\n\n".join(sections)}]}


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
    "and just answer. Never narrate what you are about to do for trivial replies, just answer."
)


class Brain:
    def __init__(self, extra_mcp_servers: Optional[dict] = None) -> None:
        # Force Claude Code to use Max OAuth, never the metered API path.
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

        local = create_sdk_mcp_server(
            name="shelby-tools",
            version="0.1.0",
            tools=[current_time, system_info, weather, forecast, news_headlines, github_pending],
        )
        servers = {"shelby": local}
        if extra_mcp_servers:
            servers.update(extra_mcp_servers)

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
                "mcp__claude_ai_Gmail__search_threads",
                "mcp__claude_ai_Gmail__get_thread",
                "mcp__claude_ai_Google_Calendar__list_events",
                "mcp__claude_ai_Google_Calendar__list_calendars",
            ],
            disallowed_tools=["Bash", "Edit", "Write", "Read"],
            system_prompt=SYSTEM_PROMPT,
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

    async def process_stream(self, text: str) -> AsyncIterator[str]:
        """Yield text deltas as Claude streams them back.

        With include_partial_messages enabled on ClaudeAgentOptions, the SDK
        emits StreamEvent objects whose 'event' dict carries Anthropic-style
        streaming events. We only care about content_block_delta events of
        type text_delta — each one carries a small slice of the final reply,
        typically a token or two.

        Callers can consume this incrementally for low-latency TTS, or use
        process() which buffers everything into a single string.
        """
        if self._client is None:
            raise RuntimeError("Brain must be used as an async context manager")

        await self._client.query(text)
        async for msg in self._client.receive_response():
            if StreamEvent is not None and isinstance(msg, StreamEvent):
                event = getattr(msg, "event", None) or {}
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        chunk = delta.get("text", "")
                        if chunk:
                            yield chunk

    async def process(self, text: str) -> str:
        """Backward-compatible: collect the full reply as one string."""
        parts: list[str] = []
        async for chunk in self.process_stream(text):
            parts.append(chunk)
        return "".join(parts).strip()
