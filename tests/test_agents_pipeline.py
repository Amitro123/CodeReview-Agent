import asyncio
import json
from pathlib import Path

from src import config
from src.agents.llm import LLM
from src.agents.multi_agent import MultiAgentAnalyzer
from src.agents.universal_agent import UniversalAgent
from src.kb.cache import ResponseCache
from src.kb.wiki import KnowledgeBase
from tests.fakes import ScriptedGroq

REPO_ROOT = str(Path(__file__).resolve().parent.parent)

VISUAL_ANSWER = {"summary": "Login button is covered", "findings": ["#overlay covers #login-btn"],
                 "confidence": "medium", "open_questions": ["which component renders the overlay?"]}
CODE_ANSWER = {"summary": "Overlay never unmounts", "findings": ["found config"], "confidence": "high",
               "open_questions": [], "root_cause": "Modal overlay stays mounted after close",
               "fix_checklist": ["Unmount the overlay on close"], "ide_instructions": "Edit the modal",
               "files": ["src/config.py"]}


def _cache(tmp_path):
    return ResponseCache(str(tmp_path / "llm-cache"), ttl_hours=1)


def _run_universal(agent, browser_tool):
    async def run():
        ui = await agent.visual_agent("data:image/png;base64,abc", "login button does nothing",
                                      {"url": "http://localhost:3000/login"}, [], [], browser_tool=browser_tool)
        code = await agent.code_agent(REPO_ROOT, ui, {"tagName": "BUTTON"}, query="login button does nothing")
        plan = await agent.integrator(ui, code)
        return ui, code, plan
    return asyncio.run(run())


def test_universal_pipeline_uses_tools_then_serves_repeat_from_cache(tmp_path):
    cache = _cache(tmp_path)
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

    agent = UniversalAgent(cache=cache, client=client)
    ui, code, plan = _run_universal(agent, browser_tool)

    assert browser_calls == [("inspect_element", {"selector": "#login-btn"})]
    assert ui["open_questions"] == VISUAL_ANSWER["open_questions"]
    # The real MCP repo server ran search_code against this repo and its output reached the model.
    tool_messages = [m for m in client.requests[3]["messages"] if m.get("role") == "tool"]
    assert "groq_vision_model" in tool_messages[0]["content"]
    # The visual agent's open question was handed to the code agent.
    assert "which component renders the overlay?" in client.prompt(2)
    assert "Modal overlay stays mounted after close" in plan
    assert agent.llm.calls == 4  # 2 visual turns + 2 code turns; integrator makes none

    # Same page state and unchanged repo: everything comes from cache, including the tool loops.
    repeat = UniversalAgent(cache=cache, client=ScriptedGroq([]))
    _, _, repeat_plan = _run_universal(repeat, browser_tool)
    assert repeat.llm.calls == 0
    assert repeat_plan == plan
    assert len(browser_calls) == 1


def test_code_agent_without_local_repo_runs_prompt_only(tmp_path):
    client = ScriptedGroq([{"json": CODE_ANSWER}])
    agent = UniversalAgent(cache=_cache(tmp_path), client=client)
    asyncio.run(agent.code_agent("acme/not-mapped", VISUAL_ANSWER, {}, query="q"))
    assert agent.llm.calls == 1
    assert "tools" not in client.requests[0]
    assert "NO access to this project's files" in client.prompt(0)


def test_verdict_teaches_the_next_analysis_and_invalidates_the_cached_answer(tmp_path):
    """The full cycle: analyze -> record -> 👎 -> ingest -> the next analysis of the same
    project sees the wiki and can read it through the KB MCP server."""
    cache = _cache(tmp_path)
    page_url = "http://localhost:3000/login"
    first = UniversalAgent(cache=cache, client=ScriptedGroq([{"json": CODE_ANSWER}]))
    code = asyncio.run(first.code_agent("login", VISUAL_ANSWER, {}, query="login button does nothing",
                                        page_url=page_url))
    run_id = first.record_run("login button does nothing", "login", page_url, code, [], [])

    # Before any verdict, the same question is served from cache.
    cached = UniversalAgent(cache=cache, client=ScriptedGroq([]))
    asyncio.run(cached.code_agent("login", VISUAL_ANSWER, {}, query="login button does nothing", page_url=page_url))
    assert cached.llm.calls == 0

    kb = KnowledgeBase.for_project("settings", "http://localhost:3000/settings")  # same host -> same project
    assert kb.record_feedback(run_id, worked=False, note="the overlay is a z-index bug in Modal.css")
    ingest_client = ScriptedGroq([{"json": {"pages": [{
        "slug": "issues/login-overlay-blocks-clicks", "title": "Login overlay blocks clicks",
        "summary": "Login button unclickable because of the modal overlay", "tags": ["login", "overlay"],
        "related_pages": [],
        "body": "# Summary\n\nThe login button can't be clicked.\n\n# What didn't work\n\n"
                f"- Unmounting the overlay (run {run_id}). Actual cause: z-index bug in Modal.css",
    }], "log": "added login overlay issue"}}])
    asyncio.run(kb.ingest(run_id, LLM(cache, ingest_client), "model"))

    # The next analysis gets the wiki map and best page in its prompt, and query_kb works
    # through the real KB MCP server.
    later_client = ScriptedGroq([
        {"tool_calls": [("query_kb", {"question": "login overlay"})]},
        {"json": CODE_ANSWER},
    ])
    later = UniversalAgent(cache=cache, client=later_client)
    asyncio.run(later.code_agent("settings", VISUAL_ANSWER, {}, query="login button broken again",
                                 page_url="http://localhost:3000/settings"))
    assert "z-index bug in Modal.css" in later_client.prompt(0)
    tool_names = {t["function"]["name"] for t in later_client.requests[0]["tools"]}
    assert tool_names == {"query_kb", "get_page"}  # no repo mapped -> only the KB tools
    tool_result = [m for m in later_client.requests[1]["messages"] if m.get("role") == "tool"][0]["content"]
    assert "issues/login-overlay-blocks-clicks" in tool_result

    # After the 👎 the original question must not get the cached wrong answer back.
    repeat_client = ScriptedGroq([{"json": CODE_ANSWER}])
    repeat = UniversalAgent(cache=cache, client=repeat_client)
    asyncio.run(repeat.code_agent("login", VISUAL_ANSWER, {}, query="login button does nothing", page_url=page_url))
    assert repeat.llm.calls == 1
    assert "z-index bug in Modal.css" in repeat_client.prompt(0)


def test_ci_analysis_is_one_call_recorded_and_cached(tmp_path):
    # tmp_path as the repo: the analyzer writes its markdown reports into <repo>/analysis.
    repo = str(tmp_path / "repo")
    Path(repo).mkdir()
    cache = _cache(tmp_path)
    ci_answer = {"frontend_findings": "No frontend/UI issue found.", "backend_findings": "DB timeout",
                 "root_cause": "Migration lock missing", "pr_title": "Lock migrations",
                 "fix_checklist": ["Add lock"], "files": ["migrate.py"], "confidence": "high"}
    client = ScriptedGroq([{"json": ci_answer}])
    analyzer = MultiAgentAnalyzer(cache=cache, client=client)
    log = "x" * 20_000 + "\nERROR: lock timeout"
    result = asyncio.run(analyzer.analyze_ci_failure(log, repo))

    assert analyzer.llm.calls == 1
    assert "Migration lock missing" in result["solution"]
    assert (Path(repo) / "analysis" / "solution_plan.md").exists()
    assert len(client.prompt(0)) < 18_000  # log was truncated to its tail
    assert KnowledgeBase.for_project(repo).load_run(result["run_id"])["root_cause"] == "Migration lock missing"

    again = MultiAgentAnalyzer(cache=cache, client=ScriptedGroq([]))
    asyncio.run(again.analyze_ci_failure(log, repo))
    assert again.llm.calls == 0


def test_resolve_local_repo_uses_mapping_by_repo_or_host(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REPO_PATHS", config._parse_repo_paths(
        f"Acme/App={tmp_path},localhost:3000={tmp_path}"))
    assert config.resolve_local_repo("acme/app") == tmp_path.resolve()
    assert config.resolve_local_repo("some/route", "http://localhost:3000/some/route") == tmp_path.resolve()
    assert config.resolve_local_repo("unknown/repo") is None
    assert config.resolve_local_repo("", "https://example.com/") is None
