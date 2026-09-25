"""One entry point for every reported problem: classify, decide the route, run the agents.

  request ──> signals ──> classifier (Jev, or 1 LLM call) ──> policy ──> agents
               (0 calls)   skipped on a CI run page or when         frontend: visual agent, then code agent
                           the user picked the category             backend / config_env: code agent
                                                                    ci: CI agent
"""
from typing import Any, Awaitable, Callable, Optional

from src.agents.llm import ToolExecutor
from src.agents.multi_agent import MultiAgentAnalyzer
from src.agents.universal_agent import UniversalAgent
from src.config import settings
from src.kb.cache import ResponseCache
from src.kb.wiki import KnowledgeBase
from src.router.classifier import CATEGORIES, Classification, Classifier
from src.config import resolve_local_repo
from src.router.policy import FOCUS, AgentProfile, Route, RoutingConfig, decide, escalation_reason, load_config
from src.router.sensitivity import find_sensitive, project_is_sensitive, redact
from src.router.signals import build_state, detect_source, error_text

CI_SOURCES = ("azure_devops", "github_actions")
LABELS = {"frontend": "Frontend", "backend": "Backend", "ci": "CI", "config_env": "Config / environment"}
StatusCallback = Callable[[str], Awaitable[None]]


async def _no_status(_: str) -> None:
    return None


def describe_route(c: Classification, route: Route) -> str:
    how = {"jev": "Jev", "llm": "LLM estimate, not calibrated", "source": "CI run page", "user": "your choice"}[c.method]
    agents = " → ".join(LABELS[x] for x in route.categories) or "none yet"
    text = f"Routed to: {agents} ({LABELS[c.category]} {c.confidence:.0%}, {how})"
    return text + (f" · 🔒 sensitive ({route.sensitive_reason})" if route.sensitive else "")


class RoutedAnalysis:
    def __init__(self, cache: Optional[ResponseCache] = None, client: Any = None,
                 classifier: Optional[Classifier] = None, config: Optional[RoutingConfig] = None):
        cache = cache or ResponseCache()
        self.agent = UniversalAgent(cache, client)
        self.ci_agent = MultiAgentAnalyzer(cache, client)
        self.classifier = classifier or Classifier(self.agent.llm)
        self.config = config or load_config()

    @property
    def llm_calls(self) -> int:
        return self.agent.llm.calls + self.ci_agent.llm.calls

    @property
    def usage(self) -> dict:
        """Tokens and OpenRouter-reported cost (USD) across both agents' calls."""
        return {k: self.agent.llm.usage[k] + self.ci_agent.llm.usage[k] for k in self.agent.llm.usage}

    @property
    def classifier_calls(self) -> int:
        return self.classifier.jev.calls if self.classifier.jev else 0

    async def classify(self, request: dict) -> tuple[Classification, Route]:
        page_url = request.get("page_url") or (request.get("dom") or {}).get("url")
        forced = request.get("force_category")
        kb = KnowledgeBase.for_project(request.get("repo"), page_url)
        state = build_state(request, kb.category_prior(error_text(request)))
        # Secrets and personal data in the evidence make the run sensitive, and never reach the classifier.
        found = find_sensitive(state)
        if forced in CATEGORIES:
            c = Classification.certain(forced, "user")
        elif detect_source(page_url) in CI_SOURCES and request.get("ci"):
            # A failed pipeline run page: there is nothing to decide.
            c = Classification.certain("ci", "source")
        else:
            c = await self.classifier.classify(redact(state) if found else state)
        route = Route([c.category], False, "") if c.method in ("user", "source") else decide(c, self.config)

        if project_is_sensitive(self.config.sensitive_projects, request.get("repo"), page_url):
            route.sensitive, route.sensitive_reason = True, "project marked sensitive"
        elif found:
            route.sensitive, route.sensitive_reason = True, "found " + ", ".join(found)
        elif (c.sensitive_data or 0) >= self.config.sensitive_threshold:
            route.sensitive, route.sensitive_reason = True, f"classifier: sensitive data {c.sensitive_data:.0%}"
        return c, route

    async def run(self, request: dict, c: Classification, route: Route,
                  browser_tool: Optional[ToolExecutor] = None, status: StatusCallback = _no_status) -> dict:
        query = request.get("query") or ""
        dom = request.get("dom") or {}
        repo = request.get("repo") or "."
        page_url = request.get("page_url") or dom.get("url")
        network_errors = request.get("network_errors") or []
        console_errors = request.get("console_errors") or []
        sensitive = route.sensitive
        self.agent.llm.private = self.ci_agent.llm.private = sensitive
        record = {**c.to_dict(), "agents": route.categories, "overrides": request.get("overrides_run"),
                  "sensitive": sensitive, "sensitive_reason": route.sensitive_reason}
        focus = "; ".join(FOCUS[x] for x in route.categories)
        agents = self.config.agents
        repo_mapped = resolve_local_repo(repo, page_url) is not None

        if "ci" in route.categories:
            profile = agents["ci"]
            ci_log = request.get("ci_log") or (request.get("ci") or {}).get("log") or ""

            async def run_ci(model: str, run_record: dict) -> dict:
                return await self.ci_agent.analyze_ci_failure(
                    ci_log, repo, ci=request.get("ci"), query=query, page_url=page_url, model=model,
                    tools=set(profile.tools), max_turns=profile.max_turns,
                    focus=focus if len(route.categories) > 1 else "", route=run_record,
                )
            await status("CI agent: reading the failed steps...")
            model = profile.model_id(sensitive)
            result = await run_ci(model, record)
            escalation = await self._escalation(profile, model, result["analysis"], repo_mapped, "CI", status)
            if escalation:
                result = await run_ci(escalation["to"], {**record, "escalation": escalation})
            return {"plan": result["solution"], "run_id": result["run_id"], "files": list(result["files"].values()),
                    "repo": repo, "page_url": page_url, "escalation": escalation}

        screenshot = request.get("screenshot")
        ui_analysis: dict = {}
        wants_page = (c.needs_browser or 0) >= 0.5 and bool(screenshot or browser_tool)
        if "frontend" in route.categories or wants_page:
            profile = agents["frontend"]
            await status("Frontend agent: looking at the page...")
            # Without a screenshot the "vision" alias falls back to the text model (None).
            explicit = profile.model != "vision" or (sensitive and profile.sensitive_model)
            model = profile.model_id(sensitive) if (screenshot or explicit) else None
            ui_analysis = await self.agent.visual_agent(
                screenshot, query, dom, network_errors, console_errors,
                browser_tool=browser_tool if profile.uses("browser") else None,
                model=model, max_turns=profile.max_turns,
            )

        # The agent that writes the plan: the last one in the route; a frontend finding still
        # needs the code, so frontend-only routes use the backend agent's code access.
        lead = route.categories[-1]
        profile = agents["backend"] if lead == "frontend" else agents[lead]

        async def run_code(model: str) -> dict:
            return await self.agent.code_agent(
                repo, ui_analysis, dom.get("selectedElement") or {}, query=query, page_url=page_url,
                browser_tool=browser_tool, network_errors=network_errors, console_errors=console_errors,
                model=model, tools=set(profile.tools), max_turns=profile.max_turns, focus=focus,
            )
        await status(f"{LABELS[lead]} agent: reviewing the code...")
        model = profile.model_id(sensitive)
        code_analysis = await run_code(model)
        escalation = await self._escalation(profile, model, code_analysis, repo_mapped, LABELS[lead], status)
        if escalation:
            code_analysis = await run_code(escalation["to"])
            record["escalation"] = escalation
        plan = await self.agent.integrator(ui_analysis, code_analysis)
        run_id = self.agent.record_run(query, repo, page_url, code_analysis, network_errors, console_errors,
                                       route=record)
        md_path = self.agent.save_universal_mds(plan, query, repo, network_errors, console_errors, screenshot)
        return {"plan": plan, "run_id": run_id, "files": [md_path], "repo": repo, "page_url": page_url,
                "escalation": escalation}

    @staticmethod
    async def _escalation(profile: AgentProfile, model: str, analysis: dict, repo_mapped: bool, label: str,
                          status: StatusCallback) -> Optional[dict]:
        """One re-run on the agent's fallback_model when the answer isn't good enough (see
        policy.escalation_reason). None when it is, or when there's no stronger model to use."""
        reason = escalation_reason(analysis, repo_mapped)
        fallback = profile.fallback_model
        if not reason or not fallback or fallback == model:
            return None
        print(f"DEBUG: escalating {label} agent from {model} to {fallback}: {reason}", flush=True)
        await status(f"{label} agent: {reason} - retrying with {fallback}...")
        return {"from": model, "to": fallback, "reason": reason}


def route_message(c: Classification, route: Route) -> dict:
    """What the side panel shows before the agents run: the decision and the alternatives."""
    return {
        "type": "route",
        "category": c.category,
        "confidence": c.confidence,
        "probabilities": c.probabilities,
        "method": c.method,
        "calibrated": c.calibrated,
        "agents": route.categories,
        "ask_user": route.ask_user,
        "enough_evidence": c.enough_evidence,
        "text": describe_route(c, route) if not route.ask_user else
                f"Not sure where this is ({route.reason}). Pick where to look:",
        "labels": LABELS,
        "notes": c.notes,
        "sensitive": route.sensitive,
        "sensitive_reason": route.sensitive_reason,
        "provider": settings.llm.provider,
    }
