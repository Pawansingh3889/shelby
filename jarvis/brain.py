from typing import Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
    create_sdk_mcp_server,
)


class Brain:
    def __init__(self, extra_mcp_servers: Optional[dict] = None) -> None:
        servers: dict = {}
        if extra_mcp_servers:
            servers.update(extra_mcp_servers)

        self._options = ClaudeAgentOptions(mcp_servers=servers)
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
