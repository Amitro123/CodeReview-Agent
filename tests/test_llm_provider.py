import asyncio
import json

import pytest

from src import config
from src.agents import llm as llm_module
from src.agents.llm import LLM
from src.kb.cache import ResponseCache
from tests.fakes import ScriptedGroq

LLM_VARS = ["LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "OPENROUTER_API_KEY", "GROQ_API_KEY",
            "VISION_MODEL", "CODE_MODEL", "TEXT_MODEL", "GROQ_VISION_MODEL"]


@pytest.fixture
def env(monkeypatch):
    for name in LLM_VARS:
        monkeypatch.delenv(name, raising=False)

    def set_env(**values):
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        return config.load_llm_settings()
    return set_env


def test_openrouter_is_the_default(env):
    s = env(OPENROUTER_API_KEY="sk-or-1")
    assert (s.provider, s.base_url, s.api_key) == ("openrouter", "https://openrouter.ai/api/v1", "sk-or-1")
    assert s.vision_model == s.code_model == s.text_model == "google/gemini-2.5-flash"
    assert env().provider == "openrouter"  # no key at all: still OpenRouter (and a startup warning)


def test_groq_only_setups_keep_working(env):
    s = env(GROQ_API_KEY="gsk-1", GROQ_VISION_MODEL="some/vision")
    assert (s.provider, s.base_url, s.api_key) == ("groq", "https://api.groq.com/openai/v1", "gsk-1")
    assert (s.vision_model, s.code_model, s.text_model) == ("some/vision", "openai/gpt-oss-20b", "openai/gpt-oss-120b")
    # With both keys, OpenRouter wins unless LLM_PROVIDER says otherwise.
    assert env(OPENROUTER_API_KEY="sk-or-1").provider == "openrouter"
    assert env(LLM_PROVIDER="groq").provider == "groq"


def test_model_overrides(env):
    s = env(OPENROUTER_API_KEY="k", LLM_MODEL="anthropic/claude-sonnet-4.5", CODE_MODEL="qwen/qwen3-coder",
            GROQ_VISION_MODEL="ignored/outside-groq")
    assert (s.vision_model, s.code_model, s.text_model) == (
        "anthropic/claude-sonnet-4.5", "qwen/qwen3-coder", "anthropic/claude-sonnet-4.5")


def test_custom_openai_compatible_server(env):
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        env(LLM_PROVIDER="custom")
    with pytest.raises(ValueError, match="LLM_MODEL"):
        env(LLM_PROVIDER="custom", LLM_BASE_URL="http://localhost:11434/v1")
    s = env(LLM_PROVIDER="custom", LLM_BASE_URL="http://localhost:11434/v1", LLM_MODEL="qwen3:8b")
    assert (s.base_url, s.api_key, s.code_model) == ("http://localhost:11434/v1", "", "qwen3:8b")
    with pytest.raises(ValueError, match="LLM_PROVIDER must be one of"):
        env(LLM_PROVIDER="someone-else")


def test_client_points_at_the_configured_provider(env, monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module.settings, "llm", env(OPENROUTER_API_KEY="sk-or-1"))
    client = LLM(ResponseCache(str(tmp_path), 1)).client
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
    assert client.api_key == "sk-or-1"

    monkeypatch.setattr(llm_module.settings, "llm", env(OPENROUTER_API_KEY=""))
    assert LLM(ResponseCache(str(tmp_path), 1)).client is None


def test_openrouter_requires_providers_that_support_tools_and_json(env, monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module.settings, "llm", env(OPENROUTER_API_KEY="k"))
    client = ScriptedGroq([{"json": {"a": 1}}, {"json": {"a": 2}}])
    llm = LLM(ResponseCache(str(tmp_path), 0), client)
    asyncio.run(llm.ask("m", "plain", cache=False))
    asyncio.run(llm.ask("m", "as json", json_mode=True, cache=False))
    assert client.requests[0]["extra_body"] == {"usage": {"include": True}}
    assert client.requests[1]["extra_body"] == {"usage": {"include": True}, "provider": {"require_parameters": True}}

    monkeypatch.setattr(llm_module.settings, "llm", env(LLM_PROVIDER="groq", GROQ_API_KEY="k"))
    groq_client = ScriptedGroq([{"json": {"a": 1}}])
    asyncio.run(LLM(ResponseCache(str(tmp_path), 0), groq_client).ask("m", "x", json_mode=True, cache=False))
    assert "extra_body" not in groq_client.requests[0]  # OpenRouter-only options


def test_tool_results_are_json_objects_and_prose_answers_get_a_json_retry(tmp_path):
    client = ScriptedGroq([
        {"tool_calls": [("read_file", {"path": "tests/test_totals.py"})]},
        {"text": "The test fails because the discount is applied twice."},  # prose, not the JSON asked for
        {"json": {"root_cause": "discount applied twice"}},
    ])
    llm = LLM(ResponseCache(str(tmp_path), 0), client)
    file_text = 'assert order_total([{"price": 50, "qty": 2}], discount_percent=10) == 90'

    async def read_file(name, args):
        return file_text
    answer = asyncio.run(llm.ask_with_tools("m", [{"role": "user", "content": "find it"}], [], read_file, 4))

    assert json.loads(answer) == {"root_cause": "discount applied twice"}
    tool_message = next(m for m in client.requests[1]["messages"] if m["role"] == "tool")
    assert tool_message["name"] == "read_file"
    assert json.loads(tool_message["content"]) == {"output": file_text}  # the file, not the list inside it
    assert client.requests[2]["response_format"] == {"type": "json_object"}
    assert "ONLY the JSON object" in client.requests[2]["messages"][-1]["content"]


def test_usage_and_openrouter_cost_are_added_up(tmp_path):
    class Usage:
        def __init__(self, prompt, completion, cost):
            self.prompt_tokens, self.completion_tokens, self.model_extra = prompt, completion, {"cost": cost}

    client = ScriptedGroq([{"json": {"a": 1}}, {"json": {"a": 2}}])
    original = client.create
    usages = iter([Usage(1000, 200, 0.0012), Usage(500, 100, 0.0006)])

    def create(**kwargs):
        completion = original(**kwargs)
        completion.usage = next(usages)
        return completion
    client.create = create
    llm = LLM(ResponseCache(str(tmp_path), 0), client)
    asyncio.run(llm.ask("m", "one", cache=False))
    asyncio.run(llm.ask("m", "two", cache=False))
    assert llm.usage["input_tokens"] == 1500 and llm.usage["output_tokens"] == 300
    assert round(llm.usage["cost"], 6) == 0.0018
