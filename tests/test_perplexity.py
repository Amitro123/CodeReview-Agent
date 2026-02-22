import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from src.main import app
from src.services.perplexity_client import PerplexityClient

client = TestClient(app)

@pytest.fixture
def mock_perplexity_client():
    with patch("src.api.endpoints.perplexity.ServiceFactory.get_client") as mock_get_client:
        mock_instance = AsyncMock()
        mock_get_client.return_value = mock_instance
        yield mock_instance

def test_analyze_code_success(mock_perplexity_client):
    mock_perplexity_client.analyze.return_value = {
        "answer": "This is a test answer",
        "sources": [],
        "metadata": {"model": "sonar-huge"}
    }

    response = client.post("/perplexity/analyze", json={"query": "test query"})
    
    assert response.status_code == 200
    assert response.json() == {
        "answer": "This is a test answer",
        "sources": [],
        "metadata": {"model": "sonar-huge"}
    }

def test_analyze_code_error(mock_perplexity_client):
    mock_perplexity_client.analyze.side_effect = RuntimeError("Perplexity API error")

    response = client.post("/perplexity/analyze", json={"query": "test query"})
    
    assert response.status_code == 502
    assert response.json()["detail"] == "Perplexity API error"
