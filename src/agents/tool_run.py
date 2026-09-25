"""Runs one agent step with its tools: MCP servers plus in-process tools, a bounded tool
loop, and a prompt-only fallback when the tools can't be used."""
from typing import Any, Optional

from src.agents.llm import LLM, ToolExecutor
from src.agents.mcp_tools import ToolBox

LocalTools = dict[str, tuple[dict, ToolExecutor]]


async def run_with_tools(llm: LLM, model: str, prompt: str, servers: list, local_tools: LocalTools,
                         max_turns: int, cache_extra: Optional[tuple], fallback_cache_on: Any) -> str:
    raw = None
    if servers or local_tools:
        messages = [{"role": "user", "content": prompt}]
        if cache_extra:
            raw = llm.cached_tool_answer(model, messages, cache_extra)
        if raw is None:
            try:
                async with ToolBox(servers) as toolbox:
                    async def call_tool(name: str, args: dict) -> str:
                        if name in local_tools:
                            return await local_tools[name][1](name, args)
                        return await toolbox.call(name, args)

                    tools = toolbox.tools + [spec for spec, _ in local_tools.values()]
                    raw = await llm.ask_with_tools(model, messages, tools, call_tool, max_turns, cache_extra)
            except Exception as e:
                # MCP servers unavailable (e.g. failed to start) - fall back to prompt-only
                # analysis rather than failing the whole pipeline.
                print(f"DEBUG: tools unavailable ({e}), falling back to prompt-only", flush=True)
    if raw is None or raw.startswith("Error:"):
        raw = await llm.ask(model, prompt, json_mode=True, cache_on=fallback_cache_on)
    return raw
