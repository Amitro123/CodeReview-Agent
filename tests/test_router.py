import asyncio
import json

import httpx
import pytest

import src.main as main
from src.agents.llm import LLM
from src.kb.cache import ResponseCache
from src.kb.wiki import KnowledgeBase
from src.router import classifier as classifier_module
from src.router.classifier import CATEGORIES, Classification, Classifier, JevClassifier, jev_settings
from src.router.pipeline import RoutedAnalysis, route_message
from src.router.policy import DEFAULT_CONFIG_PATH, decide, load_config
from src.router.signals import build_state, detect_source, error_text
from tests.fakes import ScriptedGroq
from tests.test_agents_pipeline import CODE_ANSWER, VISUAL_ANSWER
from tests.test_browser_tool import FakeWebSocket

PAGE = "http://localhost:3000/orders"
AZURE_RUN = "https://dev.azure.com/acme/Shop/_build/results?buildId=812&view=logs"
AZURE_CI = {
    "provider": "azure_devops", "pipeline": "shop-ci", "branch": "refs/heads/main", "commit": "abc123",
    "failed_steps": [{"name": "Run unit tests",
                      "issues": ["tests/test_orders.py::test_total FAILED - assert 90 == 100"],
                      "log_tail": "..." * 10 + "AssertionError: assert 90 == 100"}],
}
CI_ANSWER = {"frontend_findings": "No frontend/UI issue found.", "backend_findings": "Discount applied twice",
             "root_cause": "apply_discount runs twice", "pr_title": "Fix double discount",
             "fix_checklist": ["Remove the second call"], "files": ["shop/orders.py"], "confidence": "high"}


def jev_reply(probabilities, needs_browser=0.2, enough_evidence=0.9):
    top = max(probabilities, key=probabilities.get)
    return {"model": "jev-1.13.0", "usage": {"input_tokens": 900, "output_tokens": 0}, "answers": {
        "category": {"type": "choice", "choice": top, "confidence": probabilities[top], "probabilities": probabilities},
        "needs_browser": {"type": "noul", "noul": needs_browser},
        "enough_evidence": {"type": "noul", "noul": enough_evidence},
    }}


class FakeJev:
    """Records System One requests and answers them like the API does."""

    def __init__(self, reply=None, status=200):
        self.reply, self.status, self.requests = reply, status, []

    def transport(self):
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json=self.reply if self.status < 400 else {"error": "boom"})
        return httpx.MockTransport(handle)

    def classifier(self, llm):
        jev = JevClassifier("https://openrouter.ai/api", "sk-or-test", transport=self.transport())
        return Classifier(llm, jev=jev)


def pipeline(tmp_path, replies, jev=None):
    client = ScriptedGroq(replies)
    routed = RoutedAnalysis(ResponseCache(str(tmp_path / "cache"), ttl_hours=1), client)
    routed.classifier = (jev or FakeJev(jev_reply({"frontend": 0.9, "backend": 0.05, "ci": 0.0, "config_env": 0.05}))
                         ).classifier(routed.agent.llm)
    return routed, client


def page_request(**extra):
    return {"query": "orders page shows an error", "page_url": PAGE, "repo": "orders",
            "dom": {"url": PAGE, "pageTitle": "Orders"},
            "network_errors": [{"status": 500, "url": f"{PAGE}/api/orders?page=2", "statusText": "Server Error"}],
            "console_errors": [{"level": "error", "text": "Failed to load orders"}], **extra}


def test_detect_source():
    assert detect_source(AZURE_RUN) == "azure_devops"
    assert detect_source("https://acme.visualstudio.com/Shop/_build/results?buildId=1") == "azure_devops"
    assert detect_source("https://github.com/o/r/actions/runs/42/job/7") == "github_actions"
    assert detect_source("https://github.com/o/r/pull/3") == "web_page"
    assert detect_source(None) == "none"


def test_state_is_compact_evidence():
    request = page_request(console_errors=[{"level": "error", "text": "x" * 2000}] * 30,
                           dom={"url": PAGE, "selectedElement": {"tagName": "BUTTON", "innerText": "Pay"}},
                           screenshot="data:image/png;base64,AAAA")
    state = build_state(request, prior={"backend": 2})
    assert state["source"] == "web_page"
    assert len(state["console_errors"]) == 10 and len(state["console_errors"][0]["text"]) <= 301
    assert state["network_errors"] == [{"status": 500, "method": None, "path": "/orders/api/orders"}]
    assert state["selected_element"] == {"tagName": "BUTTON", "innerText": "Pay"}
    assert state["previously_verified_as"] == {"backend": 2}
    assert "screenshot" not in json.dumps(state)  # Jev takes no images, and it would cost tokens

    ci_state = build_state({"page_url": AZURE_RUN, "ci": AZURE_CI})
    assert ci_state["ci"]["failed_steps"][0]["issues"] == AZURE_CI["failed_steps"][0]["issues"]


def test_jev_request_and_answer(tmp_path):
    jev = FakeJev(jev_reply({"frontend": 0.03, "backend": 0.92, "ci": 0.0, "config_env": 0.05}, needs_browser=0.1))
    result = asyncio.run(jev.classifier(None).classify({"user_query": "q"}))

    request = jev.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/systemone"
    assert request.headers["authorization"] == "Bearer sk-or-test"
    body = json.loads(request.content)
    assert body["model"] == "jev-latest" and body["state"] == {"user_query": "q"}
    assert body["questions"]["category"]["type"] == "choice"
    assert set(body["questions"]["category"]["criteria"]) == set(CATEGORIES)
    assert body["questions"]["needs_browser"]["type"] == body["questions"]["enough_evidence"]["type"] == "noul"

    assert (result.category, result.confidence, result.method, result.calibrated) == ("backend", 0.92, "jev", True)
    assert result.needs_browser == 0.1 and result.model == "jev-1.13.0"


def test_jev_goes_through_openrouter_with_the_agents_key(monkeypatch):
    for name in ["CLASSIFIER", "CLASSIFIER_BASE_URL", "CLASSIFIER_API_KEY", "TYPESAFE_API_KEY", "CLASSIFIER_MODEL"]:
        monkeypatch.delenv(name, raising=False)
    llm = classifier_module.settings.llm
    monkeypatch.setattr(llm, "provider", "openrouter")
    monkeypatch.setattr(llm, "api_key", "sk-or-1")
    assert jev_settings() == ("https://openrouter.ai/api", "sk-or-1", "jev-latest")

    monkeypatch.setenv("CLASSIFIER", "llm")
    assert jev_settings() is None
    monkeypatch.delenv("CLASSIFIER")

    monkeypatch.setattr(llm, "provider", "groq")
    assert jev_settings() is None  # no OpenRouter and no TypeSafe key: LLM fallback
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-1")
    assert jev_settings() == ("https://api.typesafe.ai", "ts-1", "jev-latest")
    monkeypatch.setenv("CLASSIFIER_BASE_URL", "https://gateway.example/typesafe")
    monkeypatch.setenv("CLASSIFIER_MODEL", "jev-1.13")
    assert jev_settings() == ("https://gateway.example/typesafe", "ts-1", "jev-1.13")


def test_falls_back_to_the_llm_when_jev_fails(tmp_path):
    client = ScriptedGroq([{"json": {"probabilities": {"frontend": 2, "backend": 6, "ci": 0, "config_env": 2},
                                     "needs_browser": 0.4, "enough_evidence": 0.8}}])
    llm = LLM(ResponseCache(str(tmp_path), 1), client)
    result = asyncio.run(FakeJev(status=502).classifier(llm).classify({"user_query": "q"}))
    assert (result.category, result.confidence, result.method, result.calibrated) == ("backend", 0.6, "llm", False)
    assert "Jev unavailable" in result.notes[0]
    assert "Categories:" in client.prompt(0)


def test_a_failed_llm_classification_says_so(tmp_path):
    llm = LLM(ResponseCache(str(tmp_path), 1), None)  # no client: every call returns "Error: ..."
    result = asyncio.run(Classifier(llm, use_env=False).classify({"user_query": "q"}))
    assert result.confidence == 0.25 and "LLM classifier failed: Error:" in result.notes[0]


def test_routing_policy():
    config = load_config()

    def route(**p):
        probabilities = {c: p.get(c, 0.0) for c in CATEGORIES}
        return decide(Classification(max(probabilities, key=probabilities.get), max(probabilities.values()),
                                     probabilities), config)

    assert route(backend=0.9, frontend=0.1).categories == ["backend"]
    # Two close categories: both agents, the one that looks at the page first.
    assert route(backend=0.55, frontend=0.4).categories == ["frontend", "backend"]
    assert route(config_env=0.6, backend=0.3).categories == ["config_env", "backend"]
    unsure = route(frontend=0.4, backend=0.3, config_env=0.3)
    assert unsure.ask_user and unsure.categories == []


def test_agents_yaml(tmp_path):
    config = load_config(DEFAULT_CONFIG_PATH)
    assert config.agents["frontend"].tools == ["browser", "page_errors"]
    assert config.single_agent_threshold == 0.85

    custom = tmp_path / "agents.yaml"
    custom.write_text("routing: {single_agent_threshold: 0.7}\n"
                      "agents: {backend: {model: anthropic/claude-sonnet-4.5, tools: [repo], max_turns: 6}}\n")
    config = load_config(custom)
    assert config.single_agent_threshold == 0.7
    assert (config.agents["backend"].model_id(), config.agents["backend"].max_turns) == ("anthropic/claude-sonnet-4.5", 6)
    assert config.agents["ci"].tools == ["repo", "kb"]  # untouched agents keep their defaults

    custom.write_text("agents: {backend: {tools: [shell]}}\n")
    with pytest.raises(ValueError, match="unknown tools"):
        load_config(custom)


def test_backend_route_skips_the_visual_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # fix plans are saved under the cwd for unmapped repos
    routed, client = pipeline(tmp_path, [{"json": CODE_ANSWER}],
                              FakeJev(jev_reply({"frontend": 0.03, "backend": 0.92, "ci": 0.0, "config_env": 0.05})))
    request = page_request(screenshot="data:image/png;base64,AAAA")

    async def run():
        c, route = await routed.classify(request)
        return c, route, await routed.run(request, c, route)
    c, route, result = asyncio.run(run())

    assert route.categories == ["backend"] and c.method == "jev"
    assert len(client.requests) == 1  # no visual call, no screenshot sent anywhere
    assert client.requests[0]["model"] == routed.config.agents["backend"].model_id()
    prompt = client.prompt(0)
    assert "No visual agent ran" in prompt and "/orders/api/orders" in prompt and "server-side code" in prompt
    run = KnowledgeBase.for_project("orders", PAGE).load_run(result["run_id"])
    assert run["route"]["category"] == "backend" and run["route"]["method"] == "jev"
    assert run["route"]["confidence"] == 0.92 and run["route"]["agents"] == ["backend"]


def test_frontend_route_looks_at_the_page_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    routed, client = pipeline(tmp_path, [{"json": VISUAL_ANSWER}, {"json": CODE_ANSWER}])
    request = page_request(screenshot="data:image/png;base64,AAAA")

    async def run():
        c, route = await routed.classify(request)
        return route, await routed.run(request, c, route)
    route, result = asyncio.run(run())

    assert route.categories == ["frontend"]
    assert client.requests[0]["model"] == routed.config.agents["frontend"].model_id()
    assert client.requests[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png")
    assert "Login button is covered" in client.prompt(1)  # the visual findings are handed on
    assert "client-side code" in client.prompt(1)
    assert "## Root Cause" in result["plan"]


def test_unsure_route_asks_the_user_then_runs_their_pick(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    jev = FakeJev(jev_reply({"frontend": 0.4, "backend": 0.3, "ci": 0.0, "config_env": 0.3}))
    routed, client = pipeline(tmp_path, [{"json": CODE_ANSWER}], jev)
    c, route = asyncio.run(routed.classify(page_request()))
    message = route_message(c, route)
    assert message["ask_user"] and message["agents"] == [] and "Not sure" in message["text"]
    assert client.requests == []

    c, route = asyncio.run(routed.classify(page_request(force_category="config_env")))
    assert (c.method, route.categories) == ("user", ["config_env"])
    assert len(jev.requests) == 1  # the user's pick needs no second classification


def test_ci_run_page_goes_straight_to_the_ci_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    jev = FakeJev(jev_reply({"frontend": 1.0}))
    routed, client = pipeline(tmp_path, [{"json": CI_ANSWER}], jev)
    request = {"query": "Why did this pipeline run fail?", "page_url": AZURE_RUN, "repo": "acme/Shop/shop",
               "dom": {"url": AZURE_RUN}, "ci": AZURE_CI}

    async def run():
        c, route = await routed.classify(request)
        return c, route, await routed.run(request, c, route)
    c, route, result = asyncio.run(run())

    assert jev.requests == []  # nothing to decide on a failed run's page
    assert (c.method, route.categories) == ("source", ["ci"])
    prompt = client.prompt(0)
    assert "test_orders.py::test_total FAILED" in prompt and "Pipeline: shop-ci" in prompt
    kb = KnowledgeBase.for_project("acme/Shop/shop", AZURE_RUN)
    assert kb.project == "acme/Shop/shop"  # the repository, not dev.azure.com
    run = kb.load_run(result["run_id"])
    assert run["kind"] == "ci" and run["route"]["method"] == "source"
    assert run["error_signature"].startswith("tests/test_orders.py::test_total FAILED")


def test_verdicts_and_reroutes_feed_the_prior_and_calibration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backend = FakeJev(jev_reply({"frontend": 0.05, "backend": 0.9, "ci": 0.0, "config_env": 0.05}))
    routed, _ = pipeline(tmp_path, [{"json": CODE_ANSWER}] * 3, backend)

    def analyze(request):
        async def run():
            c, route = await routed.classify(request)
            return await routed.run(request, c, route)
        return asyncio.run(run())["run_id"]

    kb = KnowledgeBase.for_project("orders", PAGE)
    right = analyze(page_request())
    kb.record_feedback(right, worked=True)
    wrong = analyze(page_request(query="totals are off"))
    analyze(page_request(query="totals are off", force_category="config_env", overrides_run=wrong))

    assert kb.category_prior(error_text(page_request())) == {"backend": 1}
    state = json.loads(backend.requests[-1].content)["state"]
    assert state["previously_verified_as"] == {"backend": 1}  # the second run already saw the 👍

    report = kb.calibration()
    assert report == [{"range": "0.85-0.95", "count": 2, "accuracy": 0.5, "mean_confidence": 0.9,
                       "methods": {"jev": 2}}]


def test_websocket_analyze_sends_the_route_then_the_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    routed, _ = pipeline(tmp_path, [{"json": CODE_ANSWER}],
                         FakeJev(jev_reply({"frontend": 0.03, "backend": 0.92, "ci": 0.0, "config_env": 0.05})))
    monkeypatch.setattr("src.router.pipeline.RoutedAnalysis", lambda: routed)
    ws = FakeWebSocket([])
    asyncio.run(main.handle_analyze(ws, [], {"type": "analyze", **page_request()}))

    kinds = [m["type"] for m in ws.sent]
    assert kinds[0] == "status" and "route" in kinds and kinds[-1] == "analysis_result"
    route = next(m for m in ws.sent if m["type"] == "route")
    assert route["category"] == "backend" and route["method"] == "jev" and not route["ask_user"]
    result = ws.sent[-1]
    assert result["answer"].startswith("Routed to: Backend (Backend 92%, Jev)")
    assert result["metadata"]["llm_calls"] == 1 and result["metadata"]["classifier_calls"] == 1
    assert result["run_ref"]["category"] == "backend"
