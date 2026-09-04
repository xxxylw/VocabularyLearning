import pytest


@pytest.fixture(autouse=True)
def use_fallback_enrichment(monkeypatch):
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")


# v2 cloud auth: the legacy v1.1 suites drive the study API directly
# without sessions, so disable the request guard for them. The new
# auth test suite flips ``VOCAB_REQUIRE_AUTH`` back to "1" per-test to
# exercise the protected paths.
@pytest.fixture(autouse=True)
def disable_auth_guard(monkeypatch):
    monkeypatch.setenv("VOCAB_REQUIRE_AUTH", "0")


@pytest.fixture(autouse=True)
def reset_resend_rate_limiter():
    """Keep Brevo resend throttling from bleeding across test cases."""

    from app import auth

    auth.clear_rate_limiter()
    yield
    auth.clear_rate_limiter()
