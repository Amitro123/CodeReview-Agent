"""Turns what the extension sends into the compact state the classifier sees.

No LLM calls here: everything is read from the request as-is and trimmed, so the classifier
gets the evidence (errors, failing CI steps, the user's words) without page noise.
"""
from typing import Any, Optional
from urllib.parse import urlparse

MAX_ERRORS = 10
MAX_ERROR_CHARS = 300
MAX_CI_STEPS = 5
MAX_CI_ISSUES = 8
CLASSIFIER_LOG_TAIL = 3000


def detect_source(page_url: Optional[str]) -> str:
    """Where the problem was reported from: a CI run page or any other web page."""
    if not page_url:
        return "none"
    parsed = urlparse(page_url)
    host, path = parsed.netloc.lower(), parsed.path
    if (host == "dev.azure.com" or host.endswith(".visualstudio.com")) and "/_build" in path:
        return "azure_devops"
    if host == "github.com" and "/actions/runs/" in path:
        return "github_actions"
    return "web_page"


def _clip(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _path_of(url: str) -> str:
    parsed = urlparse(str(url or ""))
    return parsed.path or str(url or "")


def compact_errors(network_errors: list, console_errors: list) -> dict:
    return {
        "console_errors": [
            {"level": e.get("level"), "text": _clip(e.get("text"), MAX_ERROR_CHARS)}
            for e in (console_errors or [])[:MAX_ERRORS]
        ],
        "network_errors": [
            {"status": e.get("status"), "method": e.get("method"), "path": _clip(_path_of(e.get("url")), 200)}
            for e in (network_errors or [])[:MAX_ERRORS]
        ],
    }


def compact_ci(ci: Optional[dict], log_tail_chars: int = CLASSIFIER_LOG_TAIL) -> Optional[dict]:
    """The failing part of a CI run: step names, the CI system's own error annotations and
    the end of each failing step's log (where the error almost always is)."""
    if not ci:
        return None
    steps = []
    for step in (ci.get("failed_steps") or [])[:MAX_CI_STEPS]:
        steps.append({
            "name": _clip(step.get("name"), 200),
            "issues": [_clip(i, MAX_ERROR_CHARS) for i in (step.get("issues") or [])[:MAX_CI_ISSUES]],
            "log_tail": str(step.get("log_tail") or "")[-log_tail_chars:],
        })
    return {
        "provider": ci.get("provider"),
        "pipeline": _clip(ci.get("pipeline"), 200),
        "branch": ci.get("branch"),
        "commit": ci.get("commit"),
        "failed_steps": steps,
        # Scraped text when the CI system's API wasn't reachable.
        "log_tail": str(ci.get("log") or "")[-log_tail_chars:] if not steps else "",
    }


def build_state(request: dict, prior: Optional[dict] = None) -> dict:
    """The classifier's input. `prior` is what the knowledge base verified for the same errors
    before, e.g. {"backend": 2}."""
    dom = request.get("dom") or {}
    element = dom.get("selectedElement") or {}
    state: dict[str, Any] = {
        "source": detect_source(request.get("page_url") or dom.get("url")),
        "user_query": _clip(request.get("query"), 500),
        "page_title": _clip(dom.get("pageTitle"), 200),
        **compact_errors(request.get("network_errors") or [], request.get("console_errors") or []),
    }
    if element:
        state["selected_element"] = {k: _clip(element.get(k), 200) for k in ("tagName", "id", "className", "innerText", "selector")
                                     if element.get(k)}
    if ci := compact_ci(request.get("ci")):
        state["ci"] = ci
    if prior:
        state["previously_verified_as"] = prior
    return state


def error_text(request: dict) -> str:
    """What identifies "the same problem" across runs, for the knowledge base's prior."""
    from src.kb.wiki import error_signature
    ci = request.get("ci") or {}
    issues = [i for s in (ci.get("failed_steps") or []) for i in (s.get("issues") or [])]
    if issues:
        return " | ".join(_clip(i, 120) for i in issues[:5])
    return error_signature(request.get("network_errors") or [], request.get("console_errors") or [])
