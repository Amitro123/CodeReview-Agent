"""End-to-end smoke test against real models on OpenRouter - run by .github/workflows/smoke.yml.

Three small bugs in a generated fixture project (a frontend one, a backend one and a failed
CI run), each sent through the whole routed flow: Jev classification, routing, and the
chosen agents reading the fixture's code through MCP. Reports the routes, confidence, LLM
calls, time and root causes as a table (GitHub step summary + stdout).

Fails only when something breaks (an HTTP error, a crash, an unparseable answer). A route
other than the expected one is reported, not failed: that is exactly what this is for.

Usage: OPENROUTER_API_KEY=... python scripts/smoke_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import textwrap
import time
from pathlib import Path

FIXTURE = {
    "static/checkout.js": """
        // Checkout page script.
        function handlePayment(event) {
            event.preventDefault();
            fetch('/api/pay', { method: 'POST' }).then(r => r.json()).then(showReceipt);
        }

        document.querySelector('#pay').addEventListener('click', handlePay);
    """,
    "static/checkout.html": """
        <form id="checkout"><button id="pay" type="submit">Pay</button></form>
        <script src="checkout.js"></script>
    """,
    "api/orders.py": """
        PAGE_SIZE = 20


        def list_orders(db, page: int):
            orders = db.fetch_orders()
            start = page * PAGE_SIZE
            # Returns the page's orders together with their customers.
            return [{"id": o["id"], "customer": db.customers[o["customer_id"]]["name"]}
                    for o in orders[start:start + PAGE_SIZE]]
    """,
    "api/db.py": """
        class Database:
            def __init__(self, orders, customers):
                self.orders = orders
                # Customers are keyed by their string id, e.g. "c-17".
                self.customers = {c["id"]: c for c in customers}

            def fetch_orders(self):
                return self.orders
    """,
    "shop/totals.py": """
        def apply_discount(total: float, percent: float) -> float:
            return total * (1 - percent / 100)


        def order_total(items, discount_percent=0):
            total = sum(i["price"] * i["qty"] for i in items)
            total = apply_discount(total, discount_percent)
            if discount_percent:
                total = apply_discount(total, discount_percent)
            return round(total, 2)
    """,
    "tests/test_totals.py": """
        from shop.totals import order_total


        def test_total_with_discount():
            assert order_total([{"price": 50, "qty": 2}], discount_percent=10) == 90
    """,
    "azure-pipelines.yml": """
        trigger: [main]
        pool: {vmImage: ubuntu-latest}
        steps:
          - script: pip install pytest
          - script: pytest tests/
            displayName: Run unit tests
    """,
}

PAGE = "http://localhost:3000"
AZURE_RUN = "https://dev.azure.com/acme/Shop/_build/results?buildId=812&view=logs"

SCENARIOS = [
    {
        "name": "Pay button does nothing",
        "expected": "frontend",
        "request": {
            "query": "Clicking Pay does nothing",
            "page_url": f"{PAGE}/checkout", "repo": "checkout",
            "dom": {"url": f"{PAGE}/checkout", "pageTitle": "Checkout",
                    "selectedElement": {"tagName": "BUTTON", "id": "pay", "innerText": "Pay", "selector": "#pay"}},
            "console_errors": [{"level": "error", "text": "Uncaught ReferenceError: handlePay is not defined",
                                "url": f"{PAGE}/static/checkout.js"}],
            "network_errors": [],
        },
    },
    {
        "name": "Orders page 2 fails",
        "expected": "backend",
        "request": {
            "query": "The orders list shows 'Something went wrong' on page 2",
            "page_url": f"{PAGE}/orders?page=2", "repo": "orders",
            "dom": {"url": f"{PAGE}/orders?page=2", "pageTitle": "Orders"},
            "console_errors": [{"level": "error", "text": "Failed to load orders: 500"}],
            "network_errors": [{"status": 500, "statusText": "Internal Server Error", "method": "GET",
                                "url": f"{PAGE}/api/orders?page=2"}],
        },
    },
    {
        "name": "Azure DevOps unit tests fail",
        "expected": "ci",
        "request": {
            "query": "Why did this pipeline run fail?",
            "page_url": AZURE_RUN, "repo": "acme/Shop/shop",
            "dom": {"url": AZURE_RUN, "pageTitle": "Build 812"},
            "ci": {"provider": "azure_devops", "pipeline": "shop-ci", "branch": "refs/heads/main", "commit": "abc123",
                   "failed_steps": [{
                       "name": "Run unit tests",
                       "issues": ["tests/test_totals.py::test_total_with_discount FAILED", "Bash exited with code '1'."],
                       "log_tail": textwrap.dedent("""
                           ============================= FAILURES =============================
                           _____________________ test_total_with_discount _____________________
                               def test_total_with_discount():
                           >       assert order_total([{"price": 50, "qty": 2}], discount_percent=10) == 90
                           E       assert 81.0 == 90
                           tests/test_totals.py:5: AssertionError
                           ===================== 1 failed in 0.03s =====================
                           ##[error]Bash exited with code '1'.
                       """),
                   }]},
        },
    },
]


def write_fixture(root: Path) -> None:
    for relative, content in FIXTURE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def cell(text: object, limit: int = 160) -> str:
    text = " ".join(str(text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[:limit] + "…"


async def run_scenarios() -> tuple[list[dict], bool]:
    from src.router.classifier import Classifier, JevClassifier, jev_settings
    from src.router.pipeline import RoutedAnalysis

    config = jev_settings()
    print(f"Jev endpoint: {config[0] if config else 'not configured - LLM classifier'}", flush=True)
    rows, ok = [], True
    for scenario in SCENARIOS:
        row = {"scenario": scenario["name"], "expected": scenario["expected"]}
        started = time.monotonic()
        try:
            routed = RoutedAnalysis()
            # Report a Jev failure as a failure, rather than silently using the fallback.
            if config:
                routed.classifier = Classifier(routed.agent.llm, jev=JevClassifier(*config))
            c, route = await routed.classify(scenario["request"])
            row.update(category=c.category, confidence=c.confidence, method=c.method,
                       probabilities=c.probabilities, needs_browser=c.needs_browser, notes=c.notes,
                       agents=route.categories, ask_user=route.ask_user)
            if route.ask_user:
                # The panel would ask the user; run the classifier's top pick to exercise the agents.
                c, route = await routed.classify({**scenario["request"], "force_category": c.category})
            result = await routed.run(scenario["request"], c, route)
            from src.kb.wiki import KnowledgeBase
            run = KnowledgeBase.for_project(result["repo"], result["page_url"]).load_run(result["run_id"])
            row.update(root_cause=run["root_cause"], files=run["files"], parse_error=run["parse_error"],
                       llm_calls=routed.llm_calls, jev_calls=routed.classifier_calls)
            if run["parse_error"]:
                ok = False
            if c.notes and any("Jev unavailable" in n for n in c.notes):
                ok = False
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
            ok = False
        row["seconds"] = round(time.monotonic() - started, 1)
        print(json.dumps(row, ensure_ascii=False, default=str), flush=True)
        rows.append(row)
    return rows, ok


def report(rows: list[dict]) -> str:
    lines = ["## Smoke test: routed analysis on real models", "",
             "| Scenario | Expected | Routed to | Confidence | Method | LLM calls | Jev calls | Time | Root cause | Files |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            lines.append(f"| {cell(r['scenario'])} | {r['expected']} | **error** | | | | | {r['seconds']}s | "
                         f"{cell(r['error'], 300)} | |")
            continue
        mark = "✅" if r.get("category") == r["expected"] else "⚠️"
        routed = " → ".join(r.get("agents") or []) or "asked the user"
        lines.append(
            f"| {cell(r['scenario'])} | {r['expected']} | {mark} {routed} | {r.get('confidence', 0):.0%} | "
            f"{r.get('method')} | {r.get('llm_calls')} | {r.get('jev_calls')} | {r['seconds']}s | "
            f"{cell(r.get('root_cause'))} | {cell(', '.join(r.get('files') or []), 80)} |")
    lines += ["", "<details><summary>Probabilities</summary>", ""]
    for r in rows:
        if r.get("probabilities"):
            probs = ", ".join(f"{k} {v:.0%}" for k, v in sorted(r["probabilities"].items(), key=lambda kv: -kv[1]))
            extra = f"; needs_browser {r['needs_browser']:.0%}" if r.get("needs_browser") is not None else ""
            notes = f"; notes: {'; '.join(r['notes'])}" if r.get("notes") else ""
            lines.append(f"- **{r['scenario']}**: {probs}{extra}{notes}")
    lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main() -> int:
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        print("OPENROUTER_API_KEY is not set - skipping the smoke test.")
        return 0
    work = Path(tempfile.mkdtemp(prefix="codereview-smoke-"))
    fixture = work / "fixture"
    write_fixture(fixture)
    # Before importing the app: src.config reads these at import time.
    os.environ.update({
        "LLM_PROVIDER": "openrouter",
        "REPO_PATHS": f"localhost:3000={fixture},acme/Shop/shop={fixture}",
        "KB_LOCATION": "central",
        "KB_DIR": str(work / "kb"),
        "LLM_CACHE_DIR": str(work / "cache"),
        "LLM_CACHE_TTL_HOURS": "0",
    })
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.chdir(work)  # fix plans for unmapped repos are saved under the cwd

    rows, ok = asyncio.run(run_scenarios())
    summary = report(rows)
    print(summary)
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as f:
            f.write(summary)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
