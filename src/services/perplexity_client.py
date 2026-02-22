import httpx
import logging
from typing import Optional, Dict, Any
from src.config import settings
from src.schemas.perplexity import PerplexityRequest, PerplexityResponse

logger = logging.getLogger(__name__)

from src.services.base_client import BaseClient

logger = logging.getLogger(__name__)

class PerplexityClient(BaseClient):
    """Client for interacting with the Perplexity API."""

    def __init__(self, api_key: str = None, base_url: str = "https://api.perplexity.ai"):
        self.api_key = api_key or settings.perplexity.api_key
        self.base_url = base_url
        self.model = settings.perplexity.model

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def analyze(self, request: PerplexityRequest) -> PerplexityResponse:
        """
        Send a query to Perplexity API and return the response.
        """
        if not self.api_key:
            raise ValueError("Perplexity API key is not configured.")

        # Default project context from spec.md
        default_context = """
        You are a senior code reviewer. Your workspace contains several specialized tools/projects:
        - GithubAgent: MCP-based code analysis
        - DevLens-AI: Video/code architecture analysis
        - mcp-python-auditor: Python auditing server

        Mandatory tasks for every review:
        1. Find bugs, security issues, and performance problems.
        2. Rate the code quality from 1-10 with a detailed explanation.
        3. Suggest specific fixes and ALWAYS show the diff for the fix.
        4. Suggest architecture improvements for scale.
        """

        # Construct the messages payload
        messages = []
        system_content = request.context if request.context else default_context
        messages.append({"role": "system", "content": system_content})
        
        user_query = request.query
        # If the request doesn't follow the spec format, wrap it
        if "Repo:" not in user_query:
            user_query = f"Query: {user_query}"
            
        messages.append({"role": "user", "content": user_query})

        payload = {
            "model": self.model,
            "messages": messages
        }

        async with httpx.AsyncClient(timeout=settings.perplexity.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._get_headers()
                )
                response.raise_for_status()
                data = response.json()
                
                # Extract answer from the response
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError("No choices returned from Perplexity API")
                
                answer = choices[0].get("message", {}).get("content", "")
                
                return PerplexityResponse(
                    answer=answer,
                    metadata={"model": data.get("model", "unknown"), "usage": data.get("usage", {})}
                )

            except httpx.HTTPStatusError as e:
                logger.error(f"Perplexity API HTTP error: {e.response.text}")
                raise RuntimeError(f"Perplexity API error: {e.response.status_code}") from e
            except httpx.RequestError as e:
                logger.error(f"Perplexity API connection error: {e}")
                raise RuntimeError("Failed to connect to Perplexity API") from e
            except Exception as e:
                logger.error(f"Unexpected error in Perplexity client: {e}")
                raise
