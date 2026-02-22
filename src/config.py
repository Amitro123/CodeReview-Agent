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

    def validate_config(self):
        """Manually trigger validation for critical components."""
        if not self.perplexity.api_key:
            raise ValueError("PERPLEXITY_API_KEY must be set in environment")
        return True

from pathlib import Path

PROJECT_ROOTS = [
    Path.home() / ".gemini/antigravity/scratch/CodeReview-Agent",
    Path.home() / ".gemini/antigravity/scratch/project-rules-generator"
]

def find_repo_root(path_hint: str = "."):
    for root in PROJECT_ROOTS:
        if root.exists():
            return root
    return Path(path_hint).absolute()

settings = Settings()
