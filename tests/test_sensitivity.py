import asyncio
import json

from src.router.classifier import QUESTIONS
from src.router.pipeline import describe_route
from src.router.sensitivity import find_sensitive, project_is_sensitive, redact
from tests.test_agents_pipeline import CODE_ANSWER
from tests.test_router import FakeJev, jev_reply, page_request, pipeline

BACKEND = {"frontend": 0.03, "backend": 0.92, "ci": 0.0, "config_env": 0.05}


def test_finds_secrets_and_personal_data():
    assert find_sensitive("Authorization failed for Bearer abcdefghijklmnopqrstuvwxyz123") == ["bearer token"]
    assert find_sensitive({"errors": ["key sk-or-v1-0123456789abcdef0123 rejected"]}) == ["api key"]
    assert find_sensitive(["user dana@example.com not found"]) == ["email"]
    assert find_sensitive("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N") == ["jwt"]
    assert find_sensitive("charge 4242 4242 4242 4242 declined") == ["card number"]  # passes the Luhn check
    assert find_sensitive("DB_PASSWORD=hunter2hunter2") == ["password"]
    # Ordinary log noise isn't sensitive: ids, timestamps, versions, URLs.
    assert find_sensitive("request 1695651234567890 failed at 2026-09-25T21:00:00Z, v1.2.3, GET /api/orders?page=2") == []


def test_redact_keeps_the_structure():
    state = {"console_errors": [{"level": "error", "text": "no user dana@example.com, token Bearer abcdefghijklmnop1234"}],
             "user_query": "login fails"}
    clean = redact(state)
    assert clean["console_errors"][0]["text"] == "no user [REDACTED], token [REDACTED]"
    assert clean["user_query"] == "login fails" and find_sensitive(clean) == []


def test_project_match():
    projects = ["acme/Billing/billing-api", "admin.acme.io"]
    assert project_is_sensitive(projects, "ACME/billing/billing-api", None)
    assert project_is_sensitive(projects, "whatever", "https://admin.acme.io/users")
    assert not project_is_sensitive(projects, "acme/Shop/shop", "https://shop.acme.io")
    assert not project_is_sensitive([], "anything", None)


def test_jev_is_asked_about_sensitive_data():
    assert QUESTIONS["sensitive_data"]["type"] == "noul"


def _run(tmp_path, monkeypatch, request, jev, projects=()):
    monkeypatch.chdir(tmp_path)
    routed, client = pipeline(tmp_path, [{"json": CODE_ANSWER}], jev)
    routed.config.sensitive_projects = list(projects)

    async def go():
        c, route = await routed.classify(request)
        return c, route, await routed.run(request, c, route)
    return (*asyncio.run(go()), client)


def test_non_sensitive_problem_runs_on_the_regular_model(tmp_path, monkeypatch):
    jev = FakeJev(jev_reply(BACKEND))
    c, route, _, client = _run(tmp_path, monkeypatch, page_request(), jev)
    assert not route.sensitive
    assert client.requests[0]["model"] == "deepseek/deepseek-v4-pro"
    assert "data_collection" not in client.requests[0]["extra_body"].get("provider", {})


def test_personal_data_in_the_evidence_is_redacted_and_goes_to_the_sensitive_model(tmp_path, monkeypatch):
    jev = FakeJev(jev_reply(BACKEND))
    request = page_request(console_errors=[{"level": "error", "text": "No customer for dana@example.com"}])
    c, route, result, client = _run(tmp_path, monkeypatch, request, jev)

    assert route.sensitive and route.sensitive_reason == "found email"
    assert "dana@example.com" not in jev.requests[0].content.decode()  # the classifier never saw it
    assert "[REDACTED]" in json.loads(jev.requests[0].content)["state"]["console_errors"][0]["text"]
    assert client.requests[0]["model"] == "openai/gpt-5.4"
    assert client.requests[0]["extra_body"]["provider"]["data_collection"] == "deny"
    assert "🔒 sensitive (found email)" in describe_route(c, route)


def test_a_listed_project_is_sensitive(tmp_path, monkeypatch):
    c, route, _, client = _run(tmp_path, monkeypatch, page_request(), FakeJev(jev_reply(BACKEND)), projects=["orders"])
    assert route.sensitive and route.sensitive_reason == "project marked sensitive"
    assert client.requests[0]["model"] == "openai/gpt-5.4"


def test_the_classifier_can_flag_sensitive_data(tmp_path, monkeypatch):
    reply = jev_reply(BACKEND)
    reply["answers"]["sensitive_data"] = {"type": "noul", "noul": 0.83}
    c, route, result, client = _run(tmp_path, monkeypatch, page_request(query="refund amounts are wrong"), FakeJev(reply))
    assert route.sensitive and route.sensitive_reason == "classifier: sensitive data 83%"
    assert client.requests[0]["model"] == "openai/gpt-5.4"

    from src.kb.wiki import KnowledgeBase
    run = KnowledgeBase.for_project(result["repo"], result["page_url"]).load_run(result["run_id"])
    assert run["route"]["sensitive"] and run["route"]["sensitive_reason"].startswith("classifier")
