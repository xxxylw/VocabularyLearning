"""v2 cloud edition auth (C-01..C-04 + email loop) end-to-end tests.

The whole module opts out of the conftest super-session shim with
the ``real_auth`` marker: these tests drive the real Bearer-token flow
end to end (batch 2 removed the VOCAB_REQUIRE_AUTH=0 fallback).
Brevo is monkey-patched everywhere: the auth endpoints must work end
to end without ever touching the real email channel.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import emailing
from app.main import create_app

# Drive the real session flow: no TestClient header injection.
pytestmark = pytest.mark.real_auth


@pytest.fixture
def cloud_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "cloud.sqlite"))
    # Ensure email "sends" never escape into urllib — the mock below
    # records the call; the test asserts on it.
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@test.local")
    monkeypatch.setenv("VOCAB_SUPER_EMAIL", "super@test.local")
    monkeypatch.setenv("VOCAB_SUPER_PASSWORD", "super-pass-2026")
    return tmp_path


class EmailRecorder:
    """Single recorder that captures both recipient and raw token.

    The auth endpoints call either ``send_verification_email`` or
    ``send_password_reset_email``; both have the signature
    ``(to: str, token: str) -> None``. Tests can inspect
    ``verify_calls`` / ``reset_calls`` for recipient lists, and
    ``last_verify_token`` / ``last_reset_token`` for the raw token.
    """

    def __init__(self) -> None:
        self.verify_calls: list[dict[str, str]] = []
        self.reset_calls: list[dict[str, str]] = []
        self.last_verify_token: str | None = None
        self.last_reset_token: str | None = None

    def _verify(self, to: str, token: str) -> None:
        self.verify_calls.append({"to": to})
        self.last_verify_token = token

    def _reset(self, to: str, token: str) -> None:
        self.reset_calls.append({"to": to})
        self.last_reset_token = token


@pytest.fixture
def email_spy(monkeypatch) -> EmailRecorder:
    """Replace Brevo dispatch with a single recorder."""
    recorder = EmailRecorder()
    monkeypatch.setattr(emailing, "send_verification_email", recorder._verify)
    monkeypatch.setattr(emailing, "send_password_reset_email", recorder._reset)
    return recorder


@pytest.fixture
def token_capture(email_spy: EmailRecorder) -> EmailRecorder:
    """Alias for ``email_spy`` — same recorder, different semantic name."""
    return email_spy


def _client() -> TestClient:
    return TestClient(create_app())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Registration + email loop
# ---------------------------------------------------------------------------


def test_register_creates_unverified_user_and_sends_email(
    cloud_env, email_spy, token_capture
):
    client = _client()
    response = client.post(
        "/api/auth/register", json={"email": "alice@example.com", "password": "goodpass1"}
    )
    assert response.status_code == 201, response.text
    assert response.json() == {
        "email": "alice@example.com",
        "message": "账号已创建，请查收验证邮件",
    }
    # Brevo was hit exactly once with the right recipient.
    assert [c["to"] for c in email_spy.verify_calls] == ["alice@example.com"]
    assert token_capture.last_verify_token is not None
    assert len(token_capture.last_verify_token) >= 32

    # The new account is unverified, so login must 403 with the
    # email_not_verified code.
    login = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "goodpass1"}
    )
    assert login.status_code == 403, login.text
    assert login.json()["detail"]["code"] == "email_not_verified"


def test_register_rejects_duplicate_email(cloud_env, email_spy, monkeypatch):
    from app import auth as auth_module

    monkeypatch.setattr(auth_module, "RESEND_COOLDOWN_SECONDS", 0)
    auth_module.clear_rate_limiter()

    client = _client()
    payload = {"email": "dup@example.com", "password": "goodpass1"}
    first = client.post("/api/auth/register", json=payload)
    assert first.status_code == 201, first.text
    auth_module.clear_rate_limiter()  # bypass the 3/min cap for the retry
    second = client.post("/api/auth/register", json=payload)
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "email_taken"


def test_register_rejects_short_password(cloud_env, email_spy):
    client = _client()
    response = client.post(
        "/api/auth/register", json={"email": "weakpw@example.com", "password": "short"}
    )
    assert response.status_code == 400, response.text
    assert "at least" in response.json()["detail"]


def test_register_rolls_back_when_brevo_fails(cloud_env, monkeypatch):
    """If Brevo is unreachable after the user row is inserted, the row
    must be removed so the user can retry the whole flow."""

    def _raise(_to: str, _token: str) -> None:
        raise emailing.EmailError("connection refused")

    def _ok(_to: str, _token: str) -> None:
        return None

    monkeypatch.setattr(emailing, "send_verification_email", _raise)
    monkeypatch.setattr(emailing, "send_password_reset_email", _raise)

    client = _client()
    response = client.post(
        "/api/auth/register", json={"email": "ghost@example.com", "password": "goodpass1"}
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "email_send_failed"

    # Let Brevo work again and retry — must succeed because the
    # rolled-back row is gone.
    monkeypatch.setattr(emailing, "send_verification_email", _ok)
    monkeypatch.setattr(emailing, "send_password_reset_email", _ok)
    from app import auth as auth_module

    auth_module.clear_rate_limiter()
    retry = client.post(
        "/api/auth/register", json={"email": "ghost@example.com", "password": "goodpass1"}
    )
    assert retry.status_code == 201, retry.text


def test_verify_email_marks_user_verified(cloud_env, email_spy):
    client = _client()
    client.post(
        "/api/auth/register", json={"email": "verify@example.com", "password": "goodpass1"}
    )
    token = email_spy.last_verify_token
    assert token is not None

    response = client.get("/api/auth/verify-email", params={"token": token})
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "verify@example.com"

    # Now login should work.
    login = client.post(
        "/api/auth/login", json={"email": "verify@example.com", "password": "goodpass1"}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert "token" in body
    assert body["user"]["email"] == "verify@example.com"


def test_verify_email_rejects_used_token(cloud_env, email_spy):
    client = _client()
    client.post(
        "/api/auth/register", json={"email": "reuse@example.com", "password": "goodpass1"}
    )
    token = email_spy.last_verify_token
    assert token is not None
    first = client.get("/api/auth/verify-email", params={"token": token})
    assert first.status_code == 200, first.text
    second = client.get("/api/auth/verify-email", params={"token": token})
    assert second.status_code == 410, second.text
    assert second.json()["detail"]["code"] == "token_invalid"


def test_verify_email_rejects_expired_token(cloud_env, email_spy):
    """Force a token's expires_at into the past to test the 410 path."""
    import datetime as _dt
    from app import auth as auth_module
    from app.db import connect as _connect

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "expiry@example.com", "password": "goodpass1"}
    )
    token = email_spy.last_verify_token
    assert token is not None
    # Backdate the token directly in the table so consume_email_token
    # rejects it as expired.
    past = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
    with _connect() as conn:
        conn.execute(
            "update email_tokens set expires_at = ? where token_hash = ?",
            (past, auth_module._hash_token(token)),
        )

    response = client.get("/api/auth/verify-email", params={"token": token})
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["code"] == "token_invalid"


def test_resend_throttles_60s_cooldown(cloud_env, email_spy):
    """A resend right after register (which itself consumed the
    cooldown) must be 429."""
    client = _client()
    client.post(
        "/api/auth/register", json={"email": "throttle@example.com", "password": "goodpass1"}
    )
    # Right after register, the cooldown window is still open.
    first = client.post(
        "/api/auth/resend-verification", json={"email": "throttle@example.com"}
    )
    assert first.status_code == 429, first.text
    body = first.json()["detail"]
    assert body["code"] == "rate_limited"
    assert body["retryAfter"] > 0


def test_resend_throttles_3_per_minute(cloud_env, email_spy, monkeypatch):
    """Three sends in a row (with cooldown bypassed) should still trip
    the per-minute ceiling."""

    # Clear the limiter and reset the cooldown so the 3/min cap is
    # the only thing being tested.
    from app import auth as auth_module

    auth_module.clear_rate_limiter()

    # Patch the cooldown to 0s so the 3/min cap triggers cleanly.
    monkeypatch.setattr(auth_module, "RESEND_COOLDOWN_SECONDS", 0)

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "cap@example.com", "password": "goodpass1"}
    )
    # Already one send during register, so 2 more should succeed then 3rd fails.
    r1 = client.post(
        "/api/auth/resend-verification", json={"email": "cap@example.com"}
    )
    r2 = client.post(
        "/api/auth/resend-verification", json={"email": "cap@example.com"}
    )
    r3 = client.post(
        "/api/auth/resend-verification", json={"email": "cap@example.com"}
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r3.status_code == 429, r3.text


def test_forgot_then_reset_password(cloud_env, email_spy, monkeypatch):
    from app import auth as auth_module

    # Register uses 1 of the 3/min budget. To make the forgot send
    # succeed (which uses budget 2), shorten the cooldown so we don't
    # have to wait.
    monkeypatch.setattr(auth_module, "RESEND_COOLDOWN_SECONDS", 0)
    auth_module.clear_rate_limiter()

    client = _client()
    # Register + verify.
    client.post(
        "/api/auth/register", json={"email": "reset@example.com", "password": "old-pass-1"}
    )
    verify_token = email_spy.last_verify_token
    client.get("/api/auth/verify-email", params={"token": verify_token})
    login = client.post(
        "/api/auth/login", json={"email": "reset@example.com", "password": "old-pass-1"}
    )
    assert login.status_code == 200, login.text
    session_token = login.json()["token"]

    # Forgot triggers an email.
    forgot = client.post(
        "/api/auth/forgot-password", json={"email": "reset@example.com"}
    )
    assert forgot.status_code == 200, forgot.text
    reset_token = email_spy.last_reset_token
    assert reset_token is not None

    # Reset succeeds and old password stops working.
    reset = client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "newPassword": "new-pass-1"},
    )
    assert reset.status_code == 200, reset.text
    bad = client.post(
        "/api/auth/login", json={"email": "reset@example.com", "password": "old-pass-1"}
    )
    assert bad.status_code == 401, bad.text
    good = client.post(
        "/api/auth/login", json={"email": "reset@example.com", "password": "new-pass-1"}
    )
    assert good.status_code == 200, good.text

    # All prior sessions are revoked after a password reset.
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {session_token}"}
    )
    assert me.status_code == 401, me.text


def test_reset_rejects_expired_token(cloud_env, email_spy, monkeypatch):
    import datetime as _dt
    from app import auth as auth_module
    from app.db import connect as _connect

    monkeypatch.setattr(auth_module, "RESEND_COOLDOWN_SECONDS", 0)
    auth_module.clear_rate_limiter()

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "resexp@example.com", "password": "old-pass-1"}
    )
    verify_token = email_spy.last_verify_token
    client.get("/api/auth/verify-email", params={"token": verify_token})

    client.post(
        "/api/auth/forgot-password", json={"email": "resexp@example.com"}
    )
    reset_token = email_spy.last_reset_token
    assert reset_token is not None
    past = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
    with _connect() as conn:
        conn.execute(
            "update email_tokens set expires_at = ? where token_hash = ?",
            (past, auth_module._hash_token(reset_token)),
        )
    response = client.post(
        "/api/auth/reset-password",
        json={"token": reset_token, "newPassword": "new-pass-1"},
    )
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["code"] == "token_invalid"


def test_super_account_login_works(cloud_env, email_spy):
    """The super account is provisioned on first boot with
    VOCAB_SUPER_EMAIL / VOCAB_SUPER_PASSWORD envs set; it must be
    pre-verified and login with is_super=True."""
    client = _client()
    # First request triggers boot + ensure_super_account.
    health = client.get("/api/health")
    assert health.status_code == 200, health.text
    login = client.post(
        "/api/auth/login",
        json={"email": "super@test.local", "password": "super-pass-2026"},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["user"]["email"] == "super@test.local"
    assert body["user"]["isSuper"] is True


def test_logout_revokes_session(cloud_env, email_spy):
    client = _client()
    client.post(
        "/api/auth/register", json={"email": "logout@example.com", "password": "goodpass1"}
    )
    verify_token = email_spy.last_verify_token
    client.get("/api/auth/verify-email", params={"token": verify_token})
    login = client.post(
        "/api/auth/login", json={"email": "logout@example.com", "password": "goodpass1"}
    )
    session_token = login.json()["token"]
    # /users/me works.
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {session_token}"}
    )
    assert me.status_code == 200, me.text
    # Logout.
    out = client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {session_token}"}
    )
    assert out.status_code == 200, out.text
    assert out.json() == {"ok": True}
    # Same token is now 401.
    me2 = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {session_token}"}
    )
    assert me2.status_code == 401, me2.text


def test_change_password_revokes_other_sessions(cloud_env, email_spy, monkeypatch):
    from app import auth as auth_module

    monkeypatch.setattr(auth_module, "RESEND_COOLDOWN_SECONDS", 0)
    auth_module.clear_rate_limiter()

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "changepw@example.com", "password": "old-pass-1"}
    )
    verify_token = email_spy.last_verify_token
    client.get("/api/auth/verify-email", params={"token": verify_token})
    login = client.post(
        "/api/auth/login", json={"email": "changepw@example.com", "password": "old-pass-1"}
    )
    session_token = login.json()["token"]

    response = client.post(
        "/api/auth/change-password",
        json={"currentPassword": "old-pass-1", "newPassword": "new-pass-1"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert response.status_code == 200, response.text
    # Session revoked (the API revokes ALL sessions on password change).
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {session_token}"}
    )
    assert me.status_code == 401, me.text
    # New password works, old does not.
    good = client.post(
        "/api/auth/login", json={"email": "changepw@example.com", "password": "new-pass-1"}
    )
    assert good.status_code == 200, good.text
    bad = client.post(
        "/api/auth/login", json={"email": "changepw@example.com", "password": "old-pass-1"}
    )
    assert bad.status_code == 401, bad.text


def test_study_endpoint_requires_auth(cloud_env):
    """When VOCAB_REQUIRE_AUTH=1, calling a study endpoint without a token
    must 401."""
    client = _client()
    response = client.get("/api/reviews/due?date=2026-09-04")
    assert response.status_code == 401, response.text
    # Wrong token also 401.
    response2 = client.get("/api/reviews/due?date=2026-09-04", headers=_auth("not-a-real-token"))
    assert response2.status_code == 401, response2.text


def test_email_status_polling(cloud_env, email_spy):
    """The /auth/email-status endpoint tells the SPA whether the
    address still needs verification; the response is benign for
    unknown emails (no enumeration)."""
    client = _client()
    # Unknown email: status returns 200 with verified=False so the
    # SPA doesn't leak which addresses are registered.
    unknown = client.get(
        "/api/auth/email-status", params={"email": "nobody@example.com"}
    )
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["verified"] is False
    # Registered but unverified: also False until they click the link.
    client.post(
        "/api/auth/register", json={"email": "pending@example.com", "password": "goodpass1"}
    )
    pending = client.get(
        "/api/auth/email-status", params={"email": "pending@example.com"}
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()["verified"] is False
