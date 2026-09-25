"""Client wrapper that spawns repo_server.py (an MCP server sandboxed to one repo root)
and exposes its tools in Groq's function-calling schema.
"""
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

_SERVER_SCRIPT = str(Path(__file__).parent / "repo_server.py")


class RepoToolsClient:
    """Spawns the repo-tools MCP server, sandboxed to `repo_root`, for the life of the `async with` block."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root
        self._stack = AsyncExitStack()
        self._session: Optional[ClientSession] = None

    async def __aenter__(self) -> "RepoToolsClient":
        params = StdioServerParameters(
            command=sys.executable,
            args=[_SERVER_SCRIPT],
            env={"MCP_REPO_ROOT": self.repo_root},
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc_info):
        await self._stack.aclose()

    async def get_groq_tools(self) -> list[dict]:
        """Returns this server's tools in Groq/OpenAI function-calling schema."""
        result = await self._session.list_tools()
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                },
            }
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Calls a tool by name and returns its text content."""
        result = await self._session.call_tool(name, arguments)
        parts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
        return "\n".join(parts) if parts else ""
