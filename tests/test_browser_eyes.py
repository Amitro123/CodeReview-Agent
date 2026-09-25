import asyncio
import json

import src.main as main
from src.agents.llm import LLM
from src.agents.universal_agent import UniversalAgent
from src.kb.cache import ResponseCache
from src.kb.wiki import KnowledgeBase
from src.verify import evaluate, normalize_checks
from tests.fakes import ScriptedGroq
from tests.test_browser_tool import FakeWebSocket

COVERED_BUTTON = [{"selector": "#login-btn", "text": "Log in", "inViewport": True, "coveredBy": "#overlay",
                   "styles": {"display": "inline-block", "pointer-events": "auto"}, "attributes": {}}]
FREE_BUTTON = [{**COVERED_BUTTON[0], "coveredBy": None}]
CHECKS = [
    {"kind": "element", "selector": "#login-btn", "property": "coveredBy", "op": "equals", "value": None,
     "description": "login button is not covered"},
    {"kind": "element", "selector": ".error-toast", "op": "absent"},
    {"kind": "console", "must_not_contain": "handleLogin is not a function"},
    {"kind": "network", "must_not_fail": "/api/login"},
]


def test_normalize_checks_keeps_only_well_formed_ones():
    raw = CHECKS + [
        {"kind": "element", "selector": "", "op": "present"},                 # no selector
        {"kind": "element", "selector": "#x", "op": "equals"},               # no property
        {"kind": "element", "selector": "#x", "property": "text", "op": "rm -rf"},
        {"kind": "console"},                                                 # nothing to look for
        "not a dict",
        {"kind": "element", "selector": "#a", "op": "present"},
        {"kind": "element", "selector": "#b", "op": "present"},              # over the cap of 5
    ]
    checks = normalize_checks(raw)
    assert len(checks) == 5
    assert [c.get("selector") for c in checks] == ["#login-btn", ".error-toast", None, None, "#a"]
    assert normalize_checks("not a list") == []


def test_evaluate_fails_before_the_fix_and_passes_after():
    before = evaluate(
        CHECKS,
        {"#login-btn": COVERED_BUTTON, ".error-toast": [{"text": "Login failed"}]},
        console_errors=[{"text": "Uncaught TypeError: handleLogin is not a function"}],
        network_errors=[{"status": 500, "url": "http://localhost:3000/api/login"}],
    )
    assert not before["passed"]
    assert [r["ok"] for r in before["results"]] == [False, False, False, False]
    assert before["results"][0]["actual"] == "#overlay"

    after = evaluate(CHECKS, {"#login-btn": FREE_BUTTON, ".error-toast": "No elements match '.error-toast'"}, [], [])
    assert after["passed"]


def test_evaluate_element_ops_and_missing_elements():
    checks = normalize_checks([
        {"kind": "element", "selector": "#login-btn", "property": "styles.display", "op": "not_equals", "value": "none"},
        {"kind": "element", "selector": "#login-btn", "property": "text", "op": "contains", "value": "log"},
        {"kind": "element", "selector": "#login-btn", "property": "count", "op": "equals", "value": 1},
        {"kind": "element", "selector": "#login-btn", "property": "inViewport", "op": "equals", "value": "true"},
        {"kind": "element", "selector": "#gone", "property": "text", "op": "equals", "value": "x"},
    ])
    result = evaluate(checks, {"#login-btn": COVERED_BUTTON, "#gone": "No elements match '#gone'"}, [], [])
    assert [r["ok"] for r in result["results"]] == [True, True, True, True, False]
    assert result["results"][4]["actual"] == "no element matches"
    bad = evaluate(checks[:1], {"#login-btn": "Error: invalid CSS selector '#login-btn'"}, [], [])
    assert not bad["passed"]


def test_code_agent_uses_browser_tools_and_writes_checks(tmp_path):
    client = ScriptedGroq([
        {"tool_calls": [("inspect_element", {"selector": "#login-btn"}), ("page_errors", {"filter": "handleLogin"})]},
        {"json": {"summary": "s", "findings": [], "confidence": "high", "open_questions": [],
                  "root_cause": "Overlay covers the button", "fix_checklist": ["Unmount overlay"],
                  "ide_instructions": "i", "files": [],
                  "verification_checks": [CHECKS[0], {"kind": "bogus"}]}},
    ])
    inspected = []

    async def browser_tool(name, args):
        inspected.append(args["selector"])
        return json.dumps(COVERED_BUTTON)

    agent = UniversalAgent(cache=ResponseCache(str(tmp_path / "c"), 1), client=client)
    result = asyncio.run(agent.code_agent(
        "acme/not-mapped", {"summary": "button covered"}, {}, query="login does nothing",
        browser_tool=browser_tool,
        console_errors=[{"text": "Uncaught TypeError: handleLogin is not a function"}, {"text": "unrelated"}],
    ))

    assert {t["function"]["name"] for t in client.requests[0]["tools"]} == {"inspect_element", "page_errors"}
    assert inspected == ["#login-btn"]
    tool_results = [m["content"] for m in client.requests[1]["messages"] if m.get("role") == "tool"]
    assert "coveredBy" in tool_results[0]
    assert "handleLogin" in tool_results[1] and "unrelated" not in tool_results[1]
    assert result["verification_checks"] == normalize_checks([CHECKS[0]])  # the bogus check was dropped
    plan = asyncio.run(agent.integrator({}, result))
    assert "## How to verify" in plan and "login button is not covered" in plan


def test_verify_fix_rechecks_the_page_without_llm_calls(tmp_path):
    kb = KnowledgeBase.for_project("acme/app")
    run_id = kb.record_run("universal", "login does nothing", {"root_cause": "overlay", "verification_checks": CHECKS})

    def answer(sent):
        request = sent[-1]
        data = FREE_BUTTON if request["args"]["selector"] == "#login-btn" else "No elements match '.error-toast'"
        return {"type": "tool_result", "id": request["id"], "result": data}

    ws = FakeWebSocket([answer, answer])
    message = {"type": "verify", "run_ref": {"run_id": run_id, "repo": "acme/app", "page_url": None},
               "console_errors": [], "network_errors": [{"status": 404, "url": "http://x/favicon.ico"}]}
    result = asyncio.run(main.verify_fix(ws, [], message))

    assert [r["args"]["selector"] for r in ws.sent] == ["#login-btn", ".error-toast"]
    assert result["type"] == "verification_result" and result["passed"] is True
    assert result["results"][0]["label"] == "login button is not covered"
    assert kb.load_verification(run_id)["passed"] is True
    assert "browser check of run" in kb.log_path.read_text()


def test_verify_fix_without_checks_says_so(tmp_path):
    kb = KnowledgeBase.for_project("acme/app")
    run_id = kb.record_run("universal", "q", {"root_cause": "x"})
    message = {"run_ref": {"run_id": run_id, "repo": "acme/app"}}
    result = asyncio.run(main.verify_fix(FakeWebSocket([]), [], message))
    assert result["passed"] is False and "no browser checks" in result["message"]


def test_ingest_sees_the_browser_check(tmp_path):
    kb = KnowledgeBase.for_project("acme/app")
    run_id = kb.record_run("universal", "q", {"root_cause": "x", "verification_checks": CHECKS})
    kb.record_verification(run_id, evaluate(CHECKS, {"#login-btn": FREE_BUTTON, ".error-toast": "No elements match"}, [], []))
    kb.record_feedback(run_id, worked=True)
    client = ScriptedGroq([{"json": {"pages": [], "log": "nothing new"}}])
    asyncio.run(kb.ingest(run_id, LLM(ResponseCache(str(tmp_path / "c"), 1), client), "m"))
    assert '"passed": true' in client.prompt(0)
