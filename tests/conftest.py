import pytest


@pytest.fixture(autouse=True)
def isolated_knowledge_and_cache(tmp_path, monkeypatch):
    """Keep every test's knowledge base and LLM cache in its own temp dir - never in the
    repo checkout (where a mapped project's KB would otherwise go) or in ~."""
    monkeypatch.setenv("KB_LOCATION", "central")
    monkeypatch.setenv("KB_DIR", str(tmp_path / "kb"))
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
