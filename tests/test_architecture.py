import pytest
from src.services.base_client import BaseClient
from src.services.factory import ServiceFactory
from src.services.perplexity_client import PerplexityClient

def test_service_factory_get_perplexity():
    """Test that ServiceFactory returns a PerplexityClient."""
    client = ServiceFactory.get_client("perplexity")
    assert isinstance(client, PerplexityClient)
    assert isinstance(client, BaseClient)

def test_service_factory_invalid_provider():
    """Test that ServiceFactory raises ValueError for invalid provider."""
    with pytest.raises(ValueError, match="Unsupported provider: invalid"):
        ServiceFactory.get_client("invalid")

def test_service_factory_case_insensitivity():
    """Test that ServiceFactory is case-insensitive."""
    client = ServiceFactory.get_client("PERPLEXITY")
    assert isinstance(client, PerplexityClient)

def test_base_client_is_abstract():
    """Test that BaseClient cannot be instantiated directly."""
    with pytest.raises(TypeError):
        BaseClient()
