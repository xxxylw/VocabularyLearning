import pytest


@pytest.fixture(autouse=True)
def use_fallback_enrichment(monkeypatch):
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")
