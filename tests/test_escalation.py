import asyncio

from src.kb.wiki import KnowledgeBase
from src.router.policy import escalation_reason
from tests.test_agents_pipeline import CODE_ANSWER
from tests.test_router import AZURE_CI, AZURE_RUN, CI_ANSWER, FakeJev, jev_reply, page_request, pipeline

BACKEND = {"frontend": 0.03, "backend": 0.92, "ci": 0.0, "config_env": 0.05}


def test_escalation_reason():
    assert escalation_reason({"parse_error": True, "confidence": "high"}, False) == "no usable answer"
    assert escalation_reason({"confidence": "low", "files": ["a.py"]}, True) == "low confidence"
    assert escalation_reason({"confidence": "high", "files": []}, True) == "no code files cited"
    assert escalation_reason({"confidence": "high", "files": []}, False) == ""  # no repo to cite
    assert escalation_reason({"confidence": "medium", "files": ["a.py"]}, True) == ""


def _run(tmp_path, monkeypatch, replies, request, jev=None):
    monkeypatch.chdir(tmp_path)
    routed, client = pipeline(tmp_path, replies, jev or FakeJev(jev_reply(BACKEND)))

    async def go():
        c, route = await routed.classify(request)
        return await routed.run(request, c, route)
    return asyncio.run(go()), client


def test_a_weak_answer_is_retried_once_on_the_fallback_model(tmp_path, monkeypatch):
    unsure = {**CODE_ANSWER, "confidence": "low"}
    result, client = _run(tmp_path, monkeypatch, [{"json": unsure}, {"json": CODE_ANSWER}], page_request())

    assert [r["model"] for r in client.requests] == ["openai/gpt-5.4-mini", "openai/gpt-5.4"]
    assert result["escalation"] == {"from": "openai/gpt-5.4-mini", "to": "openai/gpt-5.4", "reason": "low confidence"}
    run = KnowledgeBase.for_project(result["repo"], result["page_url"]).load_run(result["run_id"])
    assert run["confidence"] == "high" and run["route"]["escalation"]["reason"] == "low confidence"


def test_a_good_answer_is_not_retried(tmp_path, monkeypatch):
    result, client = _run(tmp_path, monkeypatch, [{"json": CODE_ANSWER}], page_request())
    assert len(client.requests) == 1 and result["escalation"] is None


def test_no_retry_when_already_on_the_strongest_model(tmp_path, monkeypatch):
    # Sensitive runs start on gpt-5.4, which is also the fallback: nothing stronger to try.
    request = page_request(console_errors=[{"level": "error", "text": "no customer dana@example.com"}])
    result, client = _run(tmp_path, monkeypatch, [{"json": {**CODE_ANSWER, "confidence": "low"}}], request)
    assert [r["model"] for r in client.requests] == ["openai/gpt-5.4"] and result["escalation"] is None


def test_the_ci_agent_escalates_an_unusable_answer(tmp_path, monkeypatch):
    request = {"query": "Why did this pipeline run fail?", "page_url": AZURE_RUN, "repo": "acme/Shop/shop",
               "dom": {"url": AZURE_RUN}, "ci": AZURE_CI}
    result, client = _run(tmp_path, monkeypatch, [{"text": "I think the tests failed."}, {"json": CI_ANSWER}], request)

    assert [r["model"] for r in client.requests] == ["openai/gpt-5.4-mini", "openai/gpt-5.4"]
    assert result["escalation"]["reason"] == "no usable answer"
    run = KnowledgeBase.for_project("acme/Shop/shop", AZURE_RUN).load_run(result["run_id"])
    assert run["root_cause"] == CI_ANSWER["root_cause"] and run["route"]["escalation"]["to"] == "openai/gpt-5.4"
