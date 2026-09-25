"""Chat calls shared by the agents: one-shot and tool-calling, both backed by the
response cache, with a call counter so the cost of a run is visible.

Uses the OpenAI SDK against whichever OpenAI-compatible provider is configured
(OpenRouter by default, Groq, or a custom server such as Ollama) - see src/config.py."""
import asyncio
import hashlib
import json
from typing import Any, Awaitable, Callable, Optional

from openai import OpenAI

from src.config import settings
from src.kb.cache import ResponseCache

ToolExecutor = Callable[[str, dict], Awaitable[str]]


def tool_content(result: str) -> str:
    """A tool result as a JSON object. Some providers (Gemini, through OpenRouter) need a
    function response to be an object and try to parse a bare string as JSON - a file that
    happens to contain `[{"price": 50}]` then reaches the model as that list, not as the file."""
    return json.dumps({"output": result}, ensure_ascii=False)


def _is_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except (json.JSONDecodeError, TypeError):
        return False


class LLM:
    def __init__(self, cache: ResponseCache, client: Any = None):
        self.cache = cache
        if client is None and (settings.llm.api_key or settings.llm.provider == "custom"):
            client = OpenAI(
                base_url=settings.llm.base_url,
                api_key=settings.llm.api_key or "not-needed",
                # OpenRouter's optional app attribution header.
                default_headers={"X-Title": "CodeReview Agent"} if settings.llm.provider == "openrouter" else None,
            )
        self.client = client
        self.calls = 0

    async def _create(self, **kwargs):
        if settings.llm.provider == "openrouter" and ("tools" in kwargs or "response_format" in kwargs):
            # Route only to providers that actually support tool calls / JSON mode; by default
            # OpenRouter may pick one that silently ignores them.
            kwargs["extra_body"] = {"provider": {"require_parameters": True}}
        self.calls += 1
        print(f"DEBUG: LLM call #{self.calls} -> {kwargs['model']}", flush=True)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.client.chat.completions.create(**kwargs))

    def _key(self, model: str, messages: Any, extra: tuple) -> str:
        # Screenshots are large data URLs; key on their hash rather than the raw bytes.
        serialized = json.dumps(messages, sort_keys=True, default=str)
        return self.cache.key(model, hashlib.sha256(serialized.encode()).hexdigest(), *extra)

    async def ask(self, model: str, prompt: str, image_data_url: Optional[str] = None,
                  json_mode: bool = False, cache: bool = True, cache_on: Any = None) -> str:
        """One-shot call. `cache_on` keys the cache on something other than the full prompt -
        e.g. the prompt without recalled knowledge, which changes as the knowledge base grows."""
        if not self.client:
            return f"Error: no LLM client - set the API key for provider '{settings.llm.provider}'."
        content: Any = prompt
        if image_data_url:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
        messages = [{"role": "user", "content": content}]
        key = None
        if cache:
            key = self._key(model, (cache_on, image_data_url) if cache_on is not None else messages, ("ask", json_mode))
        if key and (hit := self.cache.get(key)) is not None:
            print(f"DEBUG: LLM cache hit ({model})", flush=True)
            return hit

        kwargs = {"messages": messages, "model": model}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            completion = await self._create(**kwargs)
        except Exception as e:
            print(f"DEBUG: LLM {model} failed: {e}", flush=True)
            return f"Error: {e}"
        answer = completion.choices[0].message.content or ""
        if key:
            self.cache.set(key, answer)
        return answer

    def _tool_key(self, model: str, messages: list, cache_extra: tuple, cache_on: Any) -> str:
        return self._key(model, cache_on if cache_on is not None else messages, ("tools", *cache_extra))

    def cached_tool_answer(self, model: str, messages: list, cache_extra: tuple, cache_on: Any = None) -> Optional[str]:
        """Cached final answer for a tool loop, so callers can skip setting up tools entirely."""
        hit = self.cache.get(self._tool_key(model, messages, cache_extra, cache_on))
        if hit is not None:
            print(f"DEBUG: LLM cache hit ({model}, tool loop skipped)", flush=True)
        return hit

    async def ask_with_tools(self, model: str, messages: list, tools: list, tool_executor: ToolExecutor,
                             max_iterations: int, cache_extra: Optional[tuple] = None, cache_on: Any = None) -> str:
        """Bounded tool-calling loop. The model may call tools for up to `max_iterations` turns;
        after that one tool-less JSON call forces a final answer. Pass `cache_extra` only when
        it captures everything the tools can observe (e.g. the repo's git fingerprint)."""
        if not self.client:
            return f"Error: no LLM client - set the API key for provider '{settings.llm.provider}'."
        if cache_extra is not None and (hit := self.cached_tool_answer(model, messages, cache_extra, cache_on)) is not None:
            return hit
        key = self._tool_key(model, messages, cache_extra, cache_on) if cache_extra is not None else None

        answer = None
        try:
            for _ in range(max_iterations):
                completion = await self._create(messages=messages, model=model, tools=tools, tool_choice="auto")
                message = completion.choices[0].message
                tool_calls = getattr(message, "tool_calls", None)
                if not tool_calls:
                    answer = message.content or ""
                    break
                messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [tc.model_dump() for tc in tool_calls],
                })
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    print(f"DEBUG: tool call {tc.function.name} {json.dumps(args)[:200]}", flush=True)
                    try:
                        result = await tool_executor(tc.function.name, args)
                    except Exception as e:
                        result = f"Error calling {tc.function.name}: {e}"
                    messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.function.name,
                                     "content": tool_content(result)})
            if answer is not None and not _is_json_object(answer):
                # The model stopped calling tools but answered in prose; ask once more, in JSON mode.
                messages.append({"role": "assistant", "content": answer})
                messages.append({"role": "user", "content": "Now respond with ONLY the JSON object in the "
                                                            "shape specified at the start - no other text."})
                answer = None
            if answer is None:
                completion = await self._create(
                    messages=messages, model=model, response_format={"type": "json_object"}
                )
                answer = completion.choices[0].message.content or ""
        except Exception as e:
            print(f"DEBUG: LLM {model} tool loop failed: {e}", flush=True)
            return f"Error: {e}"

        if key:
            self.cache.set(key, answer)
        return answer

    @staticmethod
    def parse_json(raw: str) -> dict:
        """Parses an agent's JSON reply, tolerating a model that ignores json_mode or errors out."""
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("summary", "")
                data.setdefault("findings", [])
                data.setdefault("confidence", "low")
                data.setdefault("open_questions", [])
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return {"summary": raw, "findings": [], "confidence": "low", "open_questions": [], "parse_error": True}
