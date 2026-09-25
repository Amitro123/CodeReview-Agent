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

class Settings(BaseModel):
    """Global application settings."""
    perplexity: PerplexitySettings = Field(default_factory=PerplexitySettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    groq_api_key: str = Field(default=os.getenv("GROQ_API_KEY", "").strip('"'), description="Groq API Key")
    # Must be a vision-capable Groq model. Check https://console.groq.com/docs/models
    # for the current list - Groq renames/retires preview models periodically.
    groq_vision_model: str = Field(
        default=os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b").strip('"'),
        description="Groq vision-capable model used to analyze screenshots",
    )

    def validate_config(self):
        """Manually trigger validation for critical components."""
        if not self.perplexity.api_key:
            raise ValueError("PERPLEXITY_API_KEY must be set in environment")
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
