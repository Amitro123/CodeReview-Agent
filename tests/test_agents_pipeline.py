import asyncio
import json
from pathlib import Path

from src import config
from src.agents.multi_agent import MultiAgentAnalyzer
from src.agents.universal_agent import UniversalAgent, project_key
from src.memory.brain import Brain

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, args):
        self.id = call_id
        self.type = "function"
        self.function = _Function(name, json.dumps(args))

    def model_dump(self):
        return {"id": self.id, "type": self.type,
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class _Message:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Completion:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class ScriptedGroq:
    """Stands in for the Groq client: replays scripted replies and records every request."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if "tool_calls" in reply:
            calls = [_ToolCall(f"call_{i}", name, args) for i, (name, args) in enumerate(reply["tool_calls"])]
            return _Completion(_Message(tool_calls=calls))
        return _Completion(_Message(content=json.dumps(reply["json"])))


VISUAL_ANSWER = {"summary": "Login button is covered", "findings": ["#overlay covers #login-btn"],
                 "confidence": "medium", "open_questions": ["which component renders the overlay?"]}
CODE_ANSWER = {"summary": "Overlay never unmounts", "findings": ["found config"], "confidence": "high",
               "open_questions": [], "root_cause": "Modal overlay stays mounted after close",
               "fix_checklist": ["Unmount the overlay on close"], "ide_instructions": "Edit the modal",
               "files": ["src/config.py"]}


def _run_universal(agent, browser_tool):
    async def run():
        ui = await agent.visual_agent("data:image/png;base64,abc", "login button does nothing",
                                      {"url": "http://localhost:3000/login"}, [], [], browser_tool=browser_tool)
        code = await agent.code_agent(REPO_ROOT, ui, {"tagName": "BUTTON"}, query="login button does nothing")
        plan = await agent.integrator(ui, code)
        return ui, code, plan
    return asyncio.run(run())


def test_universal_pipeline_uses_tools_then_serves_repeat_from_cache():
    brain = Brain(":memory:", cache_ttl_hours=1)
    client = ScriptedGroq([
        {"tool_calls": [("inspect_element", {"selector": "#login-btn"})]},
        {"json": VISUAL_ANSWER},
        {"tool_calls": [("search_code", {"query": "groq_vision_model"})]},
        {"json": CODE_ANSWER},
    ])
    browser_calls = []

    async def browser_tool(name, args):
        browser_calls.append((name, args))
        return json.dumps([{"selector": "#login-btn", "coveredBy": "#overlay"}])

    agent = UniversalAgent(brain=brain, client=client)
    ui, code, plan = _run_universal(agent, browser_tool)

    assert browser_calls == [("inspect_element", {"selector": "#login-btn"})]
    assert ui["open_questions"] == VISUAL_ANSWER["open_questions"]
    # The real MCP repo server ran search_code against this repo and its output reached the model.
    tool_messages = [m for m in client.requests[3]["messages"] if m.get("role") == "tool"]
    assert "groq_vision_model" in tool_messages[0]["content"]
    # The visual agent's open question was handed to the code agent.
    assert "which component renders the overlay?" in client.requests[2]["messages"][0]["content"]
    assert "Modal overlay stays mounted after close" in plan
    assert agent.llm.calls == 4  # 2 visual turns + 2 code turns; integrator makes none

    # main.py records every run; the repeat then recalls that lesson, which must not bust the cache.
    agent.record_run("login button does nothing", REPO_ROOT, None, code, [], [])
    assert brain.recall(REPO_ROOT, "login button overlay")

    # Same page state and unchanged repo: everything comes from cache, including the tool loops.
    repeat = UniversalAgent(brain=brain, client=ScriptedGroq([]))
    _, _, repeat_plan = _run_universal(repeat, browser_tool)
    assert repeat.llm.calls == 0
    assert repeat_plan == plan
    assert len(browser_calls) == 1


def test_code_agent_without_local_repo_runs_prompt_only():
    client = ScriptedGroq([{"json": CODE_ANSWER}])
    agent = UniversalAgent(brain=Brain(":memory:"), client=client)
    asyncio.run(agent.code_agent("acme/not-mapped", VISUAL_ANSWER, {}, query="q"))
    assert agent.llm.calls == 1
    assert "tools" not in client.requests[0]
    assert "NO access to this project's files" in client.requests[0]["messages"][0]["content"]


def test_recorded_run_is_recalled_by_next_analysis_of_same_project():
    brain = Brain(":memory:")
    agent = UniversalAgent(brain=brain, client=ScriptedGroq([]))
    agent.record_run("login button does nothing", "login", "http://localhost:3000/login", CODE_ANSWER, [], [])

    client = ScriptedGroq([{"json": CODE_ANSWER}])
    later = UniversalAgent(brain=brain, client=client)
    # Different route on the same dev server -> same project key -> the lesson is recalled.
    asyncio.run(later.code_agent("settings", VISUAL_ANSWER, {}, query="login button broken again",
                                 page_url="http://localhost:3000/settings"))
    prompt = client.requests[0]["messages"][0]["content"]
    assert "Modal overlay stays mounted after close" in prompt


def test_ci_analysis_is_one_call_and_cached(tmp_path):
    # tmp_path as the repo: the analyzer writes its markdown reports into <repo>/analysis.
    repo = str(tmp_path)
    brain = Brain(":memory:", cache_ttl_hours=1)
    ci_answer = {"frontend_findings": "No frontend/UI issue found.", "backend_findings": "DB timeout",
                 "root_cause": "Migration lock missing", "pr_title": "Lock migrations",
                 "fix_checklist": ["Add lock"], "files": ["migrate.py"], "confidence": "high"}
    client = ScriptedGroq([{"json": ci_answer}])
    analyzer = MultiAgentAnalyzer(brain=brain, client=client)
    result = asyncio.run(analyzer.analyze_ci_failure("x" * 20_000 + "\nERROR: lock timeout", repo))

    assert analyzer.llm.calls == 1
    assert "Migration lock missing" in result["solution"]
    assert (tmp_path / "analysis" / "solution_plan.md").exists()
    assert len(client.requests[0]["messages"][0]["content"]) < 18_000  # log was truncated to its tail
    assert brain.recall(repo, "lock timeout migration")[0]["root_cause"] == "Migration lock missing"

    # The re-run recalls the lesson recorded above; it must still be a cache hit.
    again = MultiAgentAnalyzer(brain=brain, client=ScriptedGroq([]))
    asyncio.run(again.analyze_ci_failure("x" * 20_000 + "\nERROR: lock timeout", repo))
    assert again.llm.calls == 0


def test_recall_is_scoped_to_project_and_capped():
    brain = Brain(":memory:")
    for i in range(5):
        brain.record_run("universal", "acme/app", f"login fails {i}", "", f"cause {i} login token",
                         [], [], "high")
    brain.record_run("universal", "other/app", "login fails", "", "other project login", [], [], "high")
    lessons = brain.recall("acme/app", "login token fails")
    assert len(lessons) == 2
    assert all("other project" not in lesson["root_cause"] for lesson in lessons)


def test_cache_expires(monkeypatch):
    brain = Brain(":memory:", cache_ttl_hours=1)
    brain.cache_set("k", "v")
    import src.memory.brain as brain_module
    real_time = brain_module.time.time
    monkeypatch.setattr(brain_module.time, "time", lambda: real_time() + 2 * 3600)
    assert brain.cache_get("k") is None


def test_resolve_local_repo_uses_mapping_by_repo_or_host(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REPO_PATHS", config._parse_repo_paths(
        f"Acme/App={tmp_path},localhost:3000={tmp_path}"))
    assert config.resolve_local_repo("acme/app") == tmp_path.resolve()
    assert config.resolve_local_repo("some/route", "http://localhost:3000/some/route") == tmp_path.resolve()
    assert config.resolve_local_repo("unknown/repo") is None
    assert config.resolve_local_repo("", "https://example.com/") is None


def test_project_key():
    assert project_key("acme/app", "https://github.com/acme/app/actions") == "acme/app"
    assert project_key("dashboard/users", "http://localhost:3000/dashboard/users") == "localhost:3000"
