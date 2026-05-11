import os
import platform
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)


GITHUB_USERNAME = os.environ.get("JARVIS_GITHUB_USERNAME", "Pawansingh3889")
DEFAULT_LOCATION = os.environ.get("JARVIS_LOCATION", "Hull")


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
    user = GITHUB_USERNAME
    headers = {"Accept": "application/vnd.github+json"}
    pat = os.environ.get("JARVIS_GITHUB_TOKEN")
    if pat:
        headers["Authorization"] = f"Bearer {pat}"

    queries = {
        "Open PRs you authored": f"is:pr is:open author:{user} archived:false",
        "PRs awaiting your review": f"is:pr is:open review-requested:{user} archived:false",
        "Issues assigned to you": f"is:issue is:open assignee:{user} archived:false",
    }

    sections = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for label, q in queries.items():
            r = await client.get(
                "https://api.github.com/search/issues",
                params={"q": q, "per_page": 10},
                headers=headers,
            )
            if r.status_code != 200:
                sections.append(f"{label}: lookup failed ({r.status_code})")
                continue
            data = r.json()
            count = data.get("total_count", 0)
            if count == 0:
                sections.append(f"{label}: 0")
                continue
            lines = [f"{label}: {count}"]
            for item in data.get("items", [])[:5]:
                parts = item["repository_url"].rsplit("/", 2)[-2:]
                repo = "/".join(parts)
                lines.append(f"  - {repo}#{item['number']}: {item['title']}")
            sections.append("\n".join(lines))

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
    "You are Jarvis, a personal voice assistant for Captain PKT. "
    "Always address the user as 'Captain PKT' or 'Captain', never by any other name. "
    "Keep replies short, conversational and direct, suitable for being spoken aloud. "
    "When Captain asks for 'updates', 'briefing', 'status', or similar, call current_time, "
    "weather, news_headlines (count 3 to 5), and github_pending in parallel and then "
    "summarise into one short briefing covering: today's date and time, weather, top news, "
    "and pending GitHub work. "
    "When Captain asks about time, this computer, weather, news, or pending GitHub work, "
    "call the matching tool rather than guessing. "
    "Never narrate what you are about to do, just do it and answer."
)


class Brain:
    def __init__(self, extra_mcp_servers: Optional[dict] = None) -> None:
        local = create_sdk_mcp_server(
            name="jarvis-tools",
            version="0.1.0",
            tools=[current_time, system_info, weather, news_headlines, github_pending],
        )
        servers = {"jarvis": local}
        if extra_mcp_servers:
            servers.update(extra_mcp_servers)

        cli_path = _detect_system_claude_cli()
        self._options = ClaudeAgentOptions(
            mcp_servers=servers,
            allowed_tools=[
                "mcp__jarvis__current_time",
                "mcp__jarvis__system_info",
                "mcp__jarvis__weather",
                "mcp__jarvis__news_headlines",
                "mcp__jarvis__github_pending",
            ],
            system_prompt=SYSTEM_PROMPT,
            cli_path=cli_path,
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

    async def process(self, text: str) -> str:
        if self._client is None:
            raise RuntimeError("Brain must be used as an async context manager")

        await self._client.query(text)
        chunks: list[str] = []
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
        return "".join(chunks).strip()
