"""Spawns MCP servers over stdio and exposes their tools to one Groq tool-calling loop."""
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

_SRC = Path(__file__).resolve().parent.parent
REPO_SERVER = str(_SRC / "repo_tools" / "repo_server.py")
KB_SERVER = str(_SRC / "kb" / "kb_server.py")


class ToolBox:
    """Runs the given MCP servers for the life of the `async with` block and routes each
    tool call to the server that owns the tool. `servers` is [(script_path, env), ...]."""

    def __init__(self, servers: list[tuple[str, dict[str, str]]]):
        self._servers = servers
        self._stack = AsyncExitStack()
        self._owner: dict[str, ClientSession] = {}
        self.tools: list[dict] = []

    async def __aenter__(self) -> "ToolBox":
        try:
            for script, env in self._servers:
                params = StdioServerParameters(command=sys.executable, args=[script], env=env)
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                for tool in (await session.list_tools()).tools:
                    self._owner[tool.name] = session
                    self.tools.append({
                        "type": "function",
                        "function": {"name": tool.name, "description": tool.description or "",
                                     "parameters": tool.input_schema},
                    })
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc_info):
        await self._stack.aclose()

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        session = self._owner.get(name)
        if session is None:
            return f"Error: unknown tool '{name}'"
        result = await session.call_tool(name, arguments)
        parts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
        return "\n".join(parts)
