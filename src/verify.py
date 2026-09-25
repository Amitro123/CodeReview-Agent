"""Browser checks the code agent writes alongside its fix plan, evaluated after the fix
with no LLM call: the extension reloads the page and reports fresh element details and
console/network errors, and each check passes or fails deterministically.

A passing run is evidence, not a verdict: it is stored next to the run and shown to the
user, but only the user's 👍/👎 goes to MISTAKES.md and the wiki.

Check shapes:
  {"kind": "element", "selector": "#login-btn", "property": "coveredBy", "op": "equals", "value": null}
  {"kind": "element", "selector": ".error-toast", "op": "absent"}
  {"kind": "console", "must_not_contain": "TypeError: handleLogin is not a function"}
  {"kind": "network", "must_not_fail": "/api/login"}
`property` is a path into inspect_element's result (coveredBy, inViewport, text,
styles.display, attributes.disabled, ...) or "count" for the number of matches.
"""
from typing import Any

MAX_CHECKS = 5
ELEMENT_OPS = {"equals", "not_equals", "contains", "present", "absent"}

CHECKS_CONTRACT_HELP = """
"verification_checks": [     // up to 5 concrete browser checks that pass once the bug is fixed
  {"kind": "element", "selector": string, "property": string, "op": "equals" | "not_equals" | "contains", "value": any,
   "description": string},  // property: coveredBy, inViewport, text, styles.<css-prop>, attributes.<attr>, count
  {"kind": "element", "selector": string, "op": "present" | "absent", "description": string},
  {"kind": "console", "must_not_contain": string, "description": string},
  {"kind": "network", "must_not_fail": string, "description": string}  // URL substring
]
"""


def normalize_checks(raw: Any) -> list[dict]:
    """Keeps only well-formed checks (the model's output is untrusted), at most MAX_CHECKS."""
    checks = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        description = str(item.get("description", ""))[:200]
        if kind == "element":
            selector, op, prop = str(item.get("selector", "")).strip(), item.get("op"), str(item.get("property", "")).strip()
            if not selector or op not in ELEMENT_OPS or (op not in ("present", "absent") and not prop):
                continue
            check = {"kind": kind, "selector": selector, "op": op, "description": description}
            if op not in ("present", "absent"):
                check.update(property=prop, value=item.get("value"))
            checks.append(check)
        elif kind == "console" and str(item.get("must_not_contain", "")).strip():
            checks.append({"kind": kind, "must_not_contain": str(item["must_not_contain"]).strip(), "description": description})
        elif kind == "network" and str(item.get("must_not_fail", "")).strip():
            checks.append({"kind": kind, "must_not_fail": str(item["must_not_fail"]).strip(), "description": description})
        if len(checks) == MAX_CHECKS:
            break
    return checks


def element_selectors(checks: list[dict]) -> list[str]:
    return list(dict.fromkeys(c["selector"] for c in checks if c["kind"] == "element"))


def _lookup(element: dict, path: str) -> Any:
    value: Any = element
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _same(actual: Any, expected: Any) -> bool:
    if expected is None or expected == "null" or actual is None:
        return actual is None and (expected is None or expected == "null")
    if isinstance(actual, bool) or isinstance(expected, bool):
        return str(actual).lower() == str(expected).lower()
    return str(actual).strip() == str(expected).strip()


def evaluate(checks: list[dict], inspections: dict[str, Any], console_errors: list, network_errors: list) -> dict:
    """`inspections` maps each selector to inspect_element's answer: a list of element
    dicts, or a string such as "No elements match ..." or "Error: ..."."""
    results = []
    for check in checks:
        if check["kind"] == "element":
            found = inspections.get(check["selector"])
            elements = found if isinstance(found, list) else []
            op = check["op"]
            if isinstance(found, str) and found.startswith("Error"):
                ok, actual = False, found
            elif op == "present":
                ok, actual = bool(elements), f"{len(elements)} match(es)"
            elif op == "absent":
                ok, actual = not elements, f"{len(elements)} match(es)"
            elif not elements:
                ok, actual = False, "no element matches"
            else:
                actual = len(elements) if check["property"] == "count" else _lookup(elements[0], check["property"])
                if op == "equals":
                    ok = _same(actual, check["value"])
                elif op == "not_equals":
                    ok = not _same(actual, check["value"])
                else:
                    ok = str(check["value"]).lower() in str(actual).lower()
        elif check["kind"] == "console":
            needle = check["must_not_contain"].lower()
            hits = [str(e.get("text", "")) for e in console_errors if needle in str(e.get("text", "")).lower()]
            ok, actual = not hits, hits[:3] or "not seen"
        else:
            needle = check["must_not_fail"]
            hits = [f"{e.get('status')} {e.get('url')}" for e in network_errors if needle in str(e.get("url", ""))]
            ok, actual = not hits, hits[:3] or "no failed request"
        results.append({"check": check, "ok": ok, "actual": actual})
    return {"passed": bool(results) and all(r["ok"] for r in results), "results": results}


def describe(check: dict) -> str:
    """One-line human description of a check, for the fix plan and the popup."""
    if check.get("description"):
        return check["description"]
    if check["kind"] == "element":
        if check["op"] in ("present", "absent"):
            return f"`{check['selector']}` is {check['op']}"
        return f"`{check['selector']}` {check['property']} {check['op'].replace('_', ' ')} {check.get('value')!r}"
    if check["kind"] == "console":
        return f"no console error containing \"{check['must_not_contain']}\""
    return f"no failed request to {check['must_not_fail']}"
