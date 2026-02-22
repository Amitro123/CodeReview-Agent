from abc import ABC, abstractmethod
from typing import Any, Dict
from pydantic import BaseModel

class BaseClient(ABC):
    """Abstract base class for all AI service clients."""

    @abstractmethod
    async def analyze(self, request: Any) -> Any:
        """Analyze code or data using the service."""
        pass

    @abstractmethod
    def _get_headers(self) -> Dict[str, str]:
        """Get required headers for the service."""
        pass
