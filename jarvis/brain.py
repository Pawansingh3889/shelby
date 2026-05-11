import os
import platform
from datetime import datetime
from pathlib import Path
from typing import Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)


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


class Brain:
    def __init__(self, extra_mcp_servers: Optional[dict] = None) -> None:
        local = create_sdk_mcp_server(
            name="jarvis-tools",
            version="0.1.0",
            tools=[current_time, system_info],
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
            ],
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
