"""Agent profiles (agents.yaml) and the routing policy: from a classification to the agents
that run. The classifier only judges; thresholds and composition live here, in code."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from src.config import settings
from src.router.classifier import CATEGORIES, Classification

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "agents.yaml"
KNOWN_TOOLS = {"browser", "page_errors", "repo", "kb"}

# What the plan-writing agent is told to concentrate on, per category in the route.
FOCUS = {
    "frontend": "client-side code: components, templates, styles and client-side JavaScript/state",
    "backend": "server-side code: the API handler behind the failing request, business logic, database "
               "queries and auth",
    "ci": "why the pipeline step failed: the failing test/build/lint step and the code or pipeline "
          "definition it points at",
    "config_env": "configuration: env vars, config files, CORS/hosts, permissions and dependency versions - "
                  "check the code itself before blaming configuration",
}


@dataclass
class AgentProfile:
    name: str
    model: str = "code"
    tools: list[str] = field(default_factory=list)
    max_turns: int = 4
    description: str = ""

    def model_id(self) -> str:
        """Role aliases resolve to the configured models; anything else is a model id."""
        return {"vision": settings.llm.vision_model, "code": settings.llm.code_model,
                "text": settings.llm.text_model}.get(self.model, self.model)

    def uses(self, tool: str) -> bool:
        return tool in self.tools


@dataclass
class RoutingConfig:
    agents: dict[str, AgentProfile]
    single_agent_threshold: float = 0.85
    second_agent_min: float = 0.2


DEFAULT_AGENTS = {
    "frontend": AgentProfile("frontend", "vision", ["browser", "page_errors"], 2),
    "backend": AgentProfile("backend", "code", ["repo", "kb", "page_errors", "browser"], 4),
    "ci": AgentProfile("ci", "code", ["repo", "kb"], 4),
    "config_env": AgentProfile("config_env", "code", ["repo", "kb", "page_errors"], 3),
}


def load_config(path: Optional[Path] = None) -> RoutingConfig:
    """Reads agents.yaml; a missing file or missing entries fall back to the defaults above."""
    path = Path(path or os.getenv("AGENTS_CONFIG") or DEFAULT_CONFIG_PATH)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw = raw or {}
    agents = dict(DEFAULT_AGENTS)
    for name, spec in (raw.get("agents") or {}).items():
        if name not in CATEGORIES:
            raise ValueError(f"{path}: unknown agent {name!r}; expected one of {', '.join(CATEGORIES)}")
        spec = spec or {}
        tools = [str(t) for t in spec.get("tools", agents[name].tools)]
        if unknown := set(tools) - KNOWN_TOOLS:
            raise ValueError(f"{path}: agent {name!r} has unknown tools {sorted(unknown)}")
        agents[name] = AgentProfile(name, str(spec.get("model", agents[name].model)), tools,
                                    int(spec.get("max_turns", agents[name].max_turns)),
                                    str(spec.get("description", "")))
    routing = raw.get("routing") or {}
    return RoutingConfig(agents, float(routing.get("single_agent_threshold", 0.85)),
                         float(routing.get("second_agent_min", 0.2)))


@dataclass
class Route:
    categories: list[str]        # agents to run, in order; empty when asking the user
    ask_user: bool
    reason: str

    def to_dict(self) -> dict:
        return {"categories": self.categories, "ask_user": self.ask_user, "reason": self.reason}


def decide(c: Classification, config: RoutingConfig) -> Route:
    ranked = sorted(c.probabilities.items(), key=lambda kv: kv[1], reverse=True)
    (first, p1), (second, p2) = ranked[0], ranked[1]
    if p1 >= config.single_agent_threshold:
        return Route([first], False, f"{first} at {p1:.0%}")
    if p1 + p2 >= config.single_agent_threshold and p2 >= config.second_agent_min:
        # The frontend agent looks at the page, so it goes first and hands its findings on.
        pair = [second, first] if second == "frontend" else [first, second]
        return Route(pair, False, f"{first} {p1:.0%} / {second} {p2:.0%}: running both")
    return Route([], True, f"not sure: {first} {p1:.0%}, {second} {p2:.0%}")
