"""End-to-end smoke test against real models on OpenRouter - run by .github/workflows/smoke.yml.

Three small bugs in a generated fixture project (a frontend one, a backend one and a failed
CI run), each sent through the whole routed flow: Jev classification, routing, and the
chosen agents reading the fixture's code through MCP. Reports the routes, confidence, LLM
calls, time and root causes as a table (GitHub step summary + stdout).

Fails only when something breaks (an HTTP error, a crash, an unparseable answer). A route
other than the expected one is reported, not failed: that is exactly what this is for.

With SMOKE_COMPARE_BACKEND_MODELS="model-a,model-b,...", it also runs the backend scenario
once per model (as the backend agent's model) and reports whether each found the real cause,
with calls, tokens, OpenRouter's reported cost and time. Comparison runs never fail the job.

Usage: OPENROUTER_API_KEY=... python scripts/smoke_test.py
"""
import asyncio
import json
import os
import re
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
        from api.seed import CUSTOMERS, ORDERS


        class Database:
            def __init__(self, orders=ORDERS, customers=CUSTOMERS):
                self.orders = orders
                self.customers = {c["id"]: c for c in customers}

            def fetch_orders(self):
                return self.orders
    """,
    "api/seed.py": """
        CUSTOMERS = [{"id": f"c-{n}", "name": f"Customer {n}"} for n in range(1, 6)]

        ORDERS = (
            [{"id": n, "customer_id": f"c-{n % 5 + 1}", "total": 10 * n} for n in range(1, 21)]
            # Imported from the legacy shop in March.
            + [{"id": 20 + n, "customer_id": n, "total": 15 * n} for n in range(1, 6)]
        )
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
    # The learning scenario: a cause no one can see in this code - the external fx-service
    # rounds its rates - known only from a human's 👎 note.
    "shop/currency.py": """
        import httpx

        FX_URL = "https://fx-service.internal/v3/rate"


        def convert(amount: float, currency: str) -> float:
            if currency == "USD":
                return amount
            rate = httpx.get(FX_URL, params={"from": "USD", "to": currency}).json()["rate"]
            return round(amount * rate, 2)
    """,
    "api/cart.py": """
        from shop.currency import convert


        def cart_totals(items, currency):
            lines = [convert(i["price"] * i["qty"], currency) for i in items]
            return {"lines": lines, "total": round(sum(lines), 2)}
    """,
    "api/emails.py": """
        from shop.currency import convert


        def confirmation_email(order, currency):
            total = convert(order["total_usd"], currency)
            return f"Thanks for your order #{order['id']}. Total: {total:.2f} {currency}"
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
        "name": "Orders page 2 fails, customer email in the error",
        "expected": "backend",
        "expect_sensitive": True,
        "request": {
            "query": "The orders list shows 'Something went wrong' on page 2",
            "page_url": f"{PAGE}/orders?page=2", "repo": "orders",
            "dom": {"url": f"{PAGE}/orders?page=2", "pageTitle": "Orders"},
            "console_errors": [{"level": "error", "text": "Failed to load orders for dana.levi@example.com: 500"}],
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
                       agents=route.categories, ask_user=route.ask_user, sensitive=route.sensitive,
                       sensitive_reason=route.sensitive_reason, sensitive_data=c.sensitive_data)
            lead = (route.categories or [c.category])[-1]
            profile = routed.config.agents["backend" if lead == "frontend" else lead]
            row["model"] = profile.model_id(route.sensitive)
            if bool(scenario.get("expect_sensitive")) != route.sensitive:
                ok = False  # a missed (or invented) sensitive route is a privacy bug, not a routing miss
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


LEARN_FIRST = {
    "query": "Cart totals are a few cents off for customers paying in EUR",
    "page_url": f"{PAGE}/cart", "repo": "shop", "force_category": "backend",
    "dom": {"url": f"{PAGE}/cart", "pageTitle": "Cart"},
    "console_errors": [], "network_errors": [],
}
LEARN_NOTE = ("Actual cause: fx-service v3 returns exchange rates rounded to 2 decimals, so every converted "
              "amount drifts by a few cents. Our code is fine - request precision=6 from fx-service.")
LEARN_SECOND = {
    "query": "Order confirmation emails show GBP totals that are slightly wrong",
    "page_url": f"{PAGE}/orders/confirmation", "repo": "shop", "force_category": "backend",
    "dom": {"url": f"{PAGE}/orders/confirmation", "pageTitle": "Order confirmed"},
    "console_errors": [], "network_errors": [],
}


def knows_hidden_cause(root_cause: str) -> bool:
    """The note's knowledge: the rates themselves come rounded to 2 decimals from fx-service."""
    text = (root_cause or "").lower()
    rounded_rate = "rate" in text and re.search(r"\b(2|two) decimal", text) is not None
    return rounded_rate or "precision=6" in text or ("fx-service" in text and "round" in text and "rate" in text)


async def learning_scenario() -> dict:
    """Does the knowledge base make the agents better? A bug whose cause is outside the code, a 👎
    with the real cause, one ingest into the wiki, then a different symptom of the same cause."""
    from src.agents.llm import LLM
    from src.config import settings
    from src.kb.cache import ResponseCache
    from src.kb.wiki import KnowledgeBase
    from src.router.pipeline import RoutedAnalysis

    async def analyze(request: dict) -> tuple[dict, RoutedAnalysis]:
        routed = RoutedAnalysis()
        c, route = await routed.classify(request)
        result = await routed.run(request, c, route)
        run = KnowledgeBase.for_project(result["repo"], result["page_url"]).load_run(result["run_id"])
        return run, routed

    out: dict = {}
    started = time.monotonic()
    first, _ = await analyze(LEARN_FIRST)
    out["first"] = {"root_cause": first["root_cause"], "knew": knows_hidden_cause(first["root_cause"])}

    kb = KnowledgeBase.for_project("shop", LEARN_FIRST["page_url"])
    kb.record_feedback(first["run_id"], worked=False, note=LEARN_NOTE)
    pages = await kb.ingest(first["run_id"], LLM(ResponseCache()), settings.llm.text_model)
    out["pages"] = pages
    out["mistakes_logged"] = LEARN_NOTE[:40] in kb.mistakes_path.read_text(encoding="utf-8")
    out["recalled"] = bool(kb.recall(LEARN_SECOND["query"]))

    second, routed = await analyze(LEARN_SECOND)
    tools = routed.agent.llm.tool_calls
    out["second"] = {"root_cause": second["root_cause"], "knew": knows_hidden_cause(second["root_cause"]),
                     "used_wiki_tools": sorted({t for t in tools if t in ("query_kb", "get_page")})}
    out["seconds"] = round(time.monotonic() - started, 1)
    print(json.dumps(out, ensure_ascii=False, default=str), flush=True)
    return out


def learning_report(r: dict) -> str:
    if "error" in r:
        return f"## Learning from a 👎\n\n**error**: {cell(r['error'], 300)}\n"
    yes = lambda b: "✅" if b else "❌"
    return "\n".join([
        "## Learning from a 👎", "",
        "A cause outside the code (fx-service rounds its rates) → 👎 with the real cause → one ingest into the wiki → "
        "a different symptom of the same cause.", "",
        "| Step | Result |", "|---|---|",
        f"| 1. Cart totals off (empty wiki) | knew the cause: {yes(r['first']['knew'])} - {cell(r['first']['root_cause'])} |",
        f"| 2. 👎 + note → MISTAKES.md | {yes(r['mistakes_logged'])} |",
        f"| 3. Ingest → wiki pages | {len(r['pages'])}: {cell(', '.join(r['pages']), 120)} |",
        f"| 4. Recall matches the next problem | {yes(r['recalled'])} |",
        f"| 5. Email totals off (with the wiki) | knew the cause: {yes(r['second']['knew'])} - {cell(r['second']['root_cause'])} |",
        f"| Wiki tools the agent called | {', '.join(r['second']['used_wiki_tools']) or 'none (used the recalled page in its prompt)'} |",
        f"| Time | {r['seconds']}s |", ""])


def found_backend_cause(root_cause: str) -> bool:
    """The orders bug: legacy orders have a numeric customer_id, customers are keyed by "c-N",
    so the lookup raises KeyError. Credit an answer that names the KeyError, the seed data, or
    the id type mismatch - whole words only ("int" must not match "point" or "print")."""
    text = (root_cause or "").lower()
    if "keyerror" in text or "seed" in text:
        return True
    return "customer_id" in text and re.search(r"\b(int|integer|integers|numeric|legacy)\b", text) is not None


async def compare_backend_models(models: list[str]) -> list[dict]:
    from src.router.pipeline import RoutedAnalysis

    scenario = next(s for s in SCENARIOS if s["expected"] == "backend")
    request = {**scenario["request"], "force_category": "backend"}  # routing isn't what's compared
    rows = []
    for model in models:
        row = {"model": model}
        started = time.monotonic()
        try:
            routed = RoutedAnalysis()
            routed.config.agents["backend"].model = model
            c, route = await routed.classify(request)
            result = await routed.run(request, c, route)
            from src.kb.wiki import KnowledgeBase
            run = KnowledgeBase.for_project(result["repo"], result["page_url"]).load_run(result["run_id"])
            row.update(root_cause=run["root_cause"], files=run["files"], parse_error=run["parse_error"],
                       found=not run["parse_error"] and found_backend_cause(run["root_cause"]),
                       calls=routed.llm_calls, **routed.usage)
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        row["seconds"] = round(time.monotonic() - started, 1)
        print(json.dumps(row, ensure_ascii=False, default=str), flush=True)
        rows.append(row)
    return rows


def comparison_report(rows: list[dict]) -> str:
    lines = ["## Backend agent: model comparison", "",
             "Same bug (orders page 2 → 500; the cause is in the seed data), backend agent only, 6 tool turns.", "",
             "| Model | Found the real cause | Calls | Tokens in / out | Cost (OpenRouter) | Time | Root cause | Files |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            lines.append(f"| `{r['model']}` | **error** | | | | {r['seconds']}s | {cell(r['error'], 200)} | |")
            continue
        verdict = "✅" if r["found"] else ("❌ (no JSON)" if r["parse_error"] else "❌")
        cost = f"${r['cost']:.4f}" if r.get("cost") else "n/a"
        lines.append(f"| `{r['model']}` | {verdict} | {r['calls']} | {r['input_tokens']:,} / {r['output_tokens']:,} | "
                     f"{cost} | {r['seconds']}s | {cell(r.get('root_cause'), 140)} | "
                     f"{cell(', '.join(r.get('files') or []), 60)} |")
    return "\n".join(lines) + "\n"


def report(rows: list[dict]) -> str:
    lines = ["## Smoke test: routed analysis on real models", "",
             "| Scenario | Expected | Routed to | Confidence | Method | Sensitive | Model | LLM calls | Jev calls | Time | Root cause | Files |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            lines.append(f"| {cell(r['scenario'])} | {r['expected']} | **error** | | | | | | | {r['seconds']}s | "
                         f"{cell(r['error'], 300)} | |")
            continue
        mark = "✅" if r.get("category") == r["expected"] else "⚠️"
        routed = " → ".join(r.get("agents") or []) or "asked the user"
        lines.append(
            f"| {cell(r['scenario'])} | {r['expected']} | {mark} {routed} | {r.get('confidence', 0):.0%} | "
            f"{r.get('method')} | {'🔒 ' + cell(r.get('sensitive_reason'), 40) if r.get('sensitive') else '-'} | "
            f"`{r.get('model')}` | {r.get('llm_calls')} | {r.get('jev_calls')} | {r['seconds']}s | "
            f"{cell(r.get('root_cause'))} | {cell(', '.join(r.get('files') or []), 80)} |")
    lines += ["", "<details><summary>Probabilities</summary>", ""]
    for r in rows:
        if r.get("probabilities"):
            probs = ", ".join(f"{k} {v:.0%}" for k, v in sorted(r["probabilities"].items(), key=lambda kv: -kv[1]))
            extra = f"; needs_browser {r['needs_browser']:.0%}" if r.get("needs_browser") is not None else ""
            if r.get("sensitive_data") is not None:
                extra += f"; sensitive_data {r['sensitive_data']:.0%}"
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
        "REPO_PATHS": f"localhost:3000={fixture},acme/Shop/shop={fixture},shop={fixture}",
        "KB_LOCATION": "central",
        "KB_DIR": str(work / "kb"),
        "LLM_CACHE_DIR": str(work / "cache"),
        "LLM_CACHE_TTL_HOURS": "0",
    })
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.chdir(work)  # fix plans for unmapped repos are saved under the cwd

    rows, ok = asyncio.run(run_scenarios())
    summary = report(rows)
    if os.getenv("SMOKE_LEARNING", "1") == "1":
        try:
            learned = asyncio.run(learning_scenario())
            # Failing here means the cycle is broken (nothing written, nothing recalled), not that
            # the second answer missed: that's the measurement, reported in the table.
            ok = ok and bool(learned["pages"]) and learned["mistakes_logged"] and learned["recalled"]
        except Exception as e:
            learned, ok = {"error": f"{type(e).__name__}: {e}"}, False
        summary += "\n" + learning_report(learned)
    if models := [m.strip() for m in os.getenv("SMOKE_COMPARE_BACKEND_MODELS", "").split(",") if m.strip()]:
        summary += "\n" + comparison_report(asyncio.run(compare_backend_models(models)))
    print(summary)
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as f:
            f.write(summary)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
