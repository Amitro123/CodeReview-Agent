"""Decides whether a problem is sensitive, which picks the agents' sensitive_model (agents.yaml)
and tells OpenRouter not to use providers that store or train on the data.

Three independent signals, cheapest first:
  project  - the project is listed under sensitivity.projects in agents.yaml. The only
             reliable one for the code itself, which the agents read after routing.
  pattern  - secrets or personal data in the evidence (API keys, tokens, emails, card
             numbers), found with regexes and redacted before the classifier sees them.
  jev      - the classifier's sensitive_data probability reaches sensitivity.threshold.
"""
import re
from typing import Any, Optional

PATTERNS = {
    "api key": re.compile(r"\b(sk-(?:or-|proj-|ant-)?[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                          r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[abp]-[A-Za-z0-9-]{10,})"),
    "bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # Not \b: env-var names like DB_PASSWORD or CLIENT_SECRET put an underscore (a word char) in front.
    "password": re.compile(r"(?<![a-z])(password|passwd|pwd|secret)\s*[=:]\s*\S{4,}", re.IGNORECASE),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "card number": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
}
REDACTED = "[REDACTED]"


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _luhn(digits: str) -> bool:
    total, double = 0, False
    for ch in reversed(digits):
        n = int(ch) * (2 if double else 1)
        total += n - 9 if n > 9 else n
        double = not double
    return total % 10 == 0


def _matches(kind: str, text: str) -> list[str]:
    found = [m.group(0) for m in PATTERNS[kind].finditer(text)]
    if kind == "card number":
        # Long ids and timestamps are common in logs; only card-like numbers count.
        found = [f for f in found if _luhn(re.sub(r"\D", "", f))]
    return found


def find_sensitive(value: Any) -> list[str]:
    """The kinds of sensitive data present anywhere in a (nested) value."""
    kinds = []
    for text in _strings(value):
        for kind in PATTERNS:
            if kind not in kinds and _matches(kind, text):
                kinds.append(kind)
    return kinds


def redact(value: Any) -> Any:
    """A copy of a (nested) value with every sensitive match replaced by [REDACTED]."""
    if isinstance(value, str):
        for kind in PATTERNS:
            for match in _matches(kind, value):
                value = value.replace(match, REDACTED)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def project_is_sensitive(projects: list[str], repo: Optional[str], page_url: Optional[str]) -> bool:
    """Matches sensitivity.projects entries (owner/repo, org/project/repo or a host, case-
    insensitive) against the request's repo and page host."""
    from urllib.parse import urlparse
    keys = {(repo or "").strip().lower()}
    if page_url:
        keys.add(urlparse(page_url).netloc.lower())
    return any(p.strip().lower() in keys for p in projects if p.strip())
