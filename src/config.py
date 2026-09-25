import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables from .env file if it exists
env_path = os.path.join(os.getcwd(), '.env')
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path, override=True)
    print(f"Loaded .env from {env_path}")
else:
    print(f"No .env found at {env_path}")
from typing import Optional

class PerplexitySettings(BaseModel):
    """Configuration for Perplexity API."""
    api_key: str = Field(default=os.getenv("PERPLEXITY_API_KEY", "").strip('"'), description="Perplexity API Key")
    model: str = Field(default="sonar-pro", description="Perplexity model to use")
    timeout_seconds: int = Field(default=30, ge=1, description="Request timeout in seconds")
    max_retries: int = Field(default=3, ge=0, description="Max retries for failed requests")

class GitHubSettings(BaseModel):
    """Configuration for GitHub API."""
    token: str = Field(default=os.getenv("GITHUB_TOKEN", ""), description="GitHub Personal Access Token")
    watched_repos: list[str] = Field(default_factory=list, description="List of repositories to watch")

def _env(name: str) -> str:
    return os.getenv(name, "").strip().strip('"')


# Every provider speaks the OpenAI chat-completions API, so one client covers them all.
# (base_url, api-key env var, default models). Check the provider's model list before
# changing defaults - model ids get renamed and retired.
LLM_PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                   {"vision": "google/gemini-2.5-flash", "code": "google/gemini-2.5-flash",
                    "text": "google/gemini-2.5-flash"}),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY",
             {"vision": "qwen/qwen3.6-27b", "code": "openai/gpt-oss-20b", "text": "openai/gpt-oss-120b"}),
    # Any other OpenAI-compatible server (Ollama, LM Studio, vLLM...): set LLM_BASE_URL and LLM_MODEL.
    "custom": ("", "LLM_API_KEY", {}),
}


class LLMSettings(BaseModel):
    provider: str
    base_url: str
    api_key: str
    vision_model: str   # must accept images
    code_model: str     # must support tool calling
    text_model: str


def load_llm_settings() -> LLMSettings:
    """LLM_PROVIDER picks the provider; without it, OpenRouter unless only GROQ_API_KEY is
    set. Models: VISION_MODEL / CODE_MODEL / TEXT_MODEL, else LLM_MODEL for all three, else
    the provider's defaults."""
    provider = _env("LLM_PROVIDER").lower()
    if not provider:
        provider = "groq" if _env("GROQ_API_KEY") and not _env("OPENROUTER_API_KEY") else "openrouter"
    if provider not in LLM_PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {', '.join(LLM_PROVIDERS)}, not {provider!r}")
    base_url, key_env, defaults = LLM_PROVIDERS[provider]
    base_url = _env("LLM_BASE_URL") or base_url
    if not base_url:
        raise ValueError("LLM_PROVIDER=custom needs LLM_BASE_URL (e.g. http://localhost:11434/v1 for Ollama)")

    def model(role: str, legacy: str = "") -> str:
        chosen = _env(f"{role.upper()}_MODEL") or (_env(legacy) if legacy and provider == "groq" else "")
        chosen = chosen or _env("LLM_MODEL") or defaults.get(role, "")
        if not chosen:
            raise ValueError(f"set {role.upper()}_MODEL or LLM_MODEL for LLM_PROVIDER={provider}")
        return chosen

    return LLMSettings(
        provider=provider,
        base_url=base_url,
        api_key=_env(key_env),
        vision_model=model("vision", legacy="GROQ_VISION_MODEL"),
        code_model=model("code"),
        text_model=model("text"),
    )


class Settings(BaseModel):
    """Global application settings."""
    perplexity: PerplexitySettings = Field(default_factory=PerplexitySettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    llm: LLMSettings = Field(default_factory=load_llm_settings)

    def validate_config(self):
        """Warns about missing keys instead of refusing to start: each feature reports its own
        missing key when used, and the Perplexity-only endpoints shouldn't block the agents."""
        if not self.llm.api_key and self.llm.provider != "custom":
            print(f"WARNING: no API key for LLM provider '{self.llm.provider}' "
                  f"(set {LLM_PROVIDERS[self.llm.provider][1]}); analyses will fail until it is set.", flush=True)
        return True

from pathlib import Path
from urllib.parse import urlparse


def _parse_repo_paths(raw: str) -> dict[str, Path]:
    """Parses REPO_PATHS="owner/repo=/path,localhost:3000=/other/path" into {key: Path}."""
    mapping = {}
    for entry in raw.split(","):
        if "=" not in entry:
            continue
        key, path = entry.split("=", 1)
        if key.strip() and path.strip():
            mapping[key.strip().lower()] = Path(path.strip()).expanduser()
    return mapping


REPO_PATHS = _parse_repo_paths(os.getenv("REPO_PATHS", ""))


def resolve_local_repo(repo_hint: Optional[str], page_url: Optional[str] = None) -> Optional[Path]:
    """Maps what the extension sends to a local checkout, or None if there isn't one.

    The extension's `repo` is `owner/repo` from a GitHub URL (or a route segment on other
    sites), not a filesystem path, so it only resolves through REPO_PATHS - keyed by
    `owner/repo` or by the page's host (e.g. `localhost:3000` for a local dev server) -
    or when the hint already is an existing local directory.
    """
    if page_url:
        host = urlparse(page_url).netloc.lower()
        mapped = REPO_PATHS.get(host)
        if mapped and mapped.is_dir():
            return mapped.resolve()
    if repo_hint:
        mapped = REPO_PATHS.get(repo_hint.strip().lower())
        if mapped and mapped.is_dir():
            return mapped.resolve()
        candidate = Path(repo_hint).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    return None


def find_repo_root(path_hint: str = ".") -> Path:
    """Where to read local git state / save analysis files; falls back to the backend's cwd."""
    return resolve_local_repo(path_hint) or Path.cwd()

settings = Settings()
