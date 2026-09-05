import pytest


@pytest.fixture(autouse=True)
def use_fallback_enrichment(monkeypatch):
    monkeypatch.setenv("VOCAB_ENRICHMENT_SOURCE", "fallback")


# ---------------------------------------------------------------------------
# v2 batch 2 (C-05): per-user data isolation for the legacy v1.1 suites.
#
# The legacy study suites drive the study API through TestClient without
# real sessions. Since batch 2 removed the VOCAB_REQUIRE_AUTH=0 fallback,
# every study request must carry a Bearer token. Instead of rewriting the
# whole suite, this autouse fixture:
#
#   1. injects an ``Authorization: Bearer test-suite-token`` header into
#      every TestClient (per-request headers still win, so tests that pass
#      their own Authorization are unaffected);
#   2. patches ``app.auth.resolve_session`` so the well-known
#      ``test-suite-token`` resolves to the super account of the *current*
#      test database (the lookup runs per request, so per-test
#      VOCAB_DB_PATH switching keeps working). Any other token falls
#      through to the real resolver.
#
# Suites that exercise the real auth flow (test_auth.py, the batch-2
# isolation suite) opt out with the ``real_auth`` marker.
# ---------------------------------------------------------------------------
TEST_SUITE_TOKEN = "test-suite-token"


@pytest.fixture(autouse=True)
def super_user_session(monkeypatch, request):
    if request.node.get_closest_marker("real_auth"):
        return None

    from fastapi.testclient import TestClient

    original_init = TestClient.__init__

    def patched_init(client, *args, **kwargs):
        original_init(client, *args, **kwargs)
        client.headers.update(
            {"Authorization": f"Bearer {TEST_SUITE_TOKEN}"}
        )

    monkeypatch.setattr(TestClient, "__init__", patched_init)

    from app import auth as auth_module
    from app import db as db_module

    real_resolve_session = auth_module.resolve_session

    def fake_resolve_session(raw_token: str):
        if raw_token != TEST_SUITE_TOKEN:
            return real_resolve_session(raw_token)
        with db_module.connect() as connection:
            row = connection.execute(
                "select id, email, is_super from users"
                " where is_super = 1 order by created_at limit 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "is_super": row["is_super"],
        }

    monkeypatch.setattr(auth_module, "resolve_session", fake_resolve_session)
    return None


def super_user_id(connection=None) -> str:
    """Id of the super account provisioned in the current test database.

    Pass the caller's open ``connection`` when called from inside a
    ``with connect()`` block — opening a second connection there would
    deadlock on the SQLite write lock.
    """

    if connection is None:
        from app import db as db_module

        with db_module.connect() as connection:  # noqa: SIM117
            return super_user_id(connection)

    row = connection.execute(
        "select id from users where is_super = 1 order by created_at limit 1"
    ).fetchone()
    assert row is not None, "super account missing — migrate() should provision it"
    return str(row["id"])


@pytest.fixture(autouse=True)
def reset_resend_rate_limiter():
    """Keep Brevo resend throttling from bleeding across test cases."""

    from app import auth

    auth.clear_rate_limiter()
    yield
    auth.clear_rate_limiter()
