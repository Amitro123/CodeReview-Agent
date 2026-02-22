from typing import Dict, Type
from src.services.base_client import BaseClient
from src.services.perplexity_client import PerplexityClient

class ServiceFactory:
    """Factory for creating and managing AI service clients."""
    
    _clients: Dict[str, Type[BaseClient]] = {
        "perplexity": PerplexityClient
    }

    @classmethod
    def get_client(cls, provider: str, **kwargs) -> BaseClient:
        """Returns an instance of the requested client."""
        client_cls = cls._clients.get(provider.lower())
        if not client_cls:
            raise ValueError(f"Unsupported provider: {provider}")
        return client_cls(**kwargs)
