import asyncio

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
    assert "extra_body" not in client.requests[0]
    assert client.requests[1]["extra_body"] == {"provider": {"require_parameters": True}}

    monkeypatch.setattr(llm_module.settings, "llm", env(LLM_PROVIDER="groq", GROQ_API_KEY="k"))
    groq_client = ScriptedGroq([{"json": {"a": 1}}])
    asyncio.run(LLM(ResponseCache(str(tmp_path), 0), groq_client).ask("m", "x", json_mode=True, cache=False))
    assert "extra_body" not in groq_client.requests[0]  # an OpenRouter-only option
