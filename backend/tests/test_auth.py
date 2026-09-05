"""v2 cloud edition auth (C-01..C-04 + email loop) end-to-end tests.

The whole module opts out of the conftest super-session shim with
the ``real_auth`` marker: these tests drive the real Bearer-token flow
end to end (batch 2 removed the VOCAB_REQUIRE_AUTH=0 fallback).
Brevo is monkey-patched everywhere: the auth endpoints must work end
to end without ever touching the real email channel.

C-01a (2026-09-05): the email loop is 6-digit numeric codes —
10-minute TTL, max 5 wrong submissions per code, one active code per
user+purpose (a resend voids the previous code instantly), and codes
are stored salted-hashed in ``email_tokens``.
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
    """Single recorder that captures both recipient and raw code.

    The auth endpoints call either ``send_verification_email`` or
    ``send_password_reset_email``; both have the signature
    ``(to: str, code: str) -> None``. Tests can inspect
    ``verify_calls`` / ``reset_calls`` for recipient lists, and
    ``last_verify_code`` / ``last_reset_code`` for the raw code.
    """

    def __init__(self) -> None:
        self.verify_calls: list[dict[str, str]] = []
        self.reset_calls: list[dict[str, str]] = []
        self.last_verify_code: str | None = None
        self.last_reset_code: str | None = None

    def _verify(self, to: str, code: str) -> None:
        self.verify_calls.append({"to": to})
        self.last_verify_code = code

    def _reset(self, to: str, code: str) -> None:
        self.reset_calls.append({"to": to})
        self.last_reset_code = code


@pytest.fixture
def email_spy(monkeypatch) -> EmailRecorder:
    """Replace Brevo dispatch with a single recorder."""
    recorder = EmailRecorder()
    monkeypatch.setattr(emailing, "send_verification_email", recorder._verify)
    monkeypatch.setattr(emailing, "send_password_reset_email", recorder._reset)
    return recorder


@pytest.fixture
def code_capture(email_spy: EmailRecorder) -> EmailRecorder:
    """Alias for ``email_spy`` — same recorder, different semantic name."""
    return email_spy


def _client() -> TestClient:
    return TestClient(create_app())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bypass_rate_limits(monkeypatch) -> None:
    """Drop the 60s cooldown so a test can send repeatedly.

    The 3/min ceiling is a separate limiter — tests that need more
    than 3 sends must additionally call ``clear_rate_limiter()``.
    """
    from app import auth as auth_module

    monkeypatch.setattr(auth_module, "RESEND_COOLDOWN_SECONDS", 0)
    auth_module.clear_rate_limiter()


def _submit_verify_code(client: TestClient, email: str, code: str):
    return client.post("/api/auth/verify-email", json={"email": email, "code": code})


def _register_and_verify(client: TestClient, email: str, password: str, email_spy):
    """Register, activate via the emailed code, return the login token."""

    response = client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    verified = _submit_verify_code(client, email, str(email_spy.last_verify_code))
    assert verified.status_code == 200, verified.text
    login = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert login.status_code == 200, login.text
    return login.json()["token"]


# ---------------------------------------------------------------------------
# Registration + email loop
# ---------------------------------------------------------------------------


def test_register_creates_unverified_user_and_sends_email(
    cloud_env, email_spy, code_capture
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
    assert code_capture.last_verify_code is not None
    code = code_capture.last_verify_code
    assert len(code) == 6
    assert code.isdigit()

    # The new account is unverified, so login must 403 with the
    # email_not_verified code.
    login = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "goodpass1"}
    )
    assert login.status_code == 403, login.text
    assert login.json()["detail"]["code"] == "email_not_verified"


def test_register_stores_code_hashed_not_plaintext(cloud_env, email_spy):
    """C-01a hard constraint: the DB must never contain the code in
    cleartext — email_tokens.token_hash holds a salted scrypt hash."""

    from app.db import connect

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "hashed@example.com", "password": "goodpass1"}
    )
    code = email_spy.last_verify_code
    assert code is not None
    with connect() as conn:
        rows = conn.execute(
            "select token_hash from email_tokens where used_at is null"
        ).fetchall()
    assert rows, "expected an active code row after register"
    stored = rows[0]["token_hash"]
    assert stored != code
    assert code not in stored
    # And it verifies against the real scrypt hash.
    from app import auth as auth_module

    assert auth_module.verify_password(code, stored) is True


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

    def _raise(_to: str, _code: str) -> None:
        raise emailing.EmailError("connection refused")

    def _ok(_to: str, _code: str) -> None:
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


# ---------------------------------------------------------------------------
# Code verification (C-01a)
# ---------------------------------------------------------------------------


def test_verify_email_marks_user_verified(cloud_env, email_spy):
    client = _client()
    client.post(
        "/api/auth/register", json={"email": "verify@example.com", "password": "goodpass1"}
    )
    code = email_spy.last_verify_code
    assert code is not None

    response = _submit_verify_code(client, "verify@example.com", code)
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


def test_verify_email_rejects_used_code(cloud_env, email_spy):
    """A code is single-use: the second submission of the same code
    finds no active row and answers 410 code_missing."""

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "reuse@example.com", "password": "goodpass1"}
    )
    code = email_spy.last_verify_code
    first = _submit_verify_code(client, "reuse@example.com", code)
    assert first.status_code == 200, first.text
    second = _submit_verify_code(client, "reuse@example.com", code)
    assert second.status_code == 410, second.text
    assert second.json()["detail"]["code"] == "code_missing"


def test_verify_email_rejects_malformed_code(cloud_env, email_spy):
    """Non-6-digit or non-numeric submissions are rejected client-side
    style with 400 code_invalid before any code lookup."""

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "shape@example.com", "password": "goodpass1"}
    )
    for bad in ("12345", "1234567", "12345a", "123 45", ""):
        response = _submit_verify_code(client, "shape@example.com", bad)
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "code_invalid"


def test_verify_email_wrong_code_reports_remaining_attempts(cloud_env, email_spy):
    client = _client()
    client.post(
        "/api/auth/register", json={"email": "wrong@example.com", "password": "goodpass1"}
    )
    code = email_spy.last_verify_code
    wrong = "000000" if code != "000000" else "111111"
    first = _submit_verify_code(client, "wrong@example.com", wrong)
    assert first.status_code == 400, first.text
    detail = first.json()["detail"]
    assert detail["code"] == "code_invalid"
    assert "4" in detail["message"]  # 5 attempts total, 1 burned → 4 left
    second = _submit_verify_code(client, "wrong@example.com", wrong)
    assert second.status_code == 400, second.text
    assert "3" in second.json()["detail"]["message"]
    # The right code still works within the attempt budget.
    good = _submit_verify_code(client, "wrong@example.com", code)
    assert good.status_code == 200, good.text


def test_verify_email_voids_code_after_5_wrong_attempts(cloud_env, email_spy):
    """Same code wrong 5 times → voided; even the correct code must be
    rejected afterwards (410 code_max_attempts / code_missing)."""

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "brute@example.com", "password": "goodpass1"}
    )
    code = email_spy.last_verify_code
    wrong = "000000" if code != "000000" else "111111"
    for i in range(4):
        response = _submit_verify_code(client, "brute@example.com", wrong)
        assert response.status_code == 400, f"attempt {i + 1}: {response.text}"
    # 5th wrong submission voids the code.
    fifth = _submit_verify_code(client, "brute@example.com", wrong)
    assert fifth.status_code == 410, fifth.text
    assert fifth.json()["detail"]["code"] == "code_max_attempts"
    # Even the correct code can no longer activate the account.
    late = _submit_verify_code(client, "brute@example.com", code)
    assert late.status_code == 410, late.text
    assert late.json()["detail"]["code"] in ("code_max_attempts", "code_missing")
    login = client.post(
        "/api/auth/login", json={"email": "brute@example.com", "password": "goodpass1"}
    )
    assert login.status_code == 403, login.text


def test_verify_email_rejects_expired_code(cloud_env, email_spy):
    """Force the code's expires_at into the past to test the 410 path."""

    import datetime as _dt
    from app.db import connect as _connect

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "expiry@example.com", "password": "goodpass1"}
    )
    code = email_spy.last_verify_code
    assert code is not None
    # Backdate the active code row so consume_email_code rejects it
    # as expired.
    past = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
    with _connect() as conn:
        conn.execute(
            "update email_tokens set expires_at = ? where used_at is null",
            (past,),
        )

    response = _submit_verify_code(client, "expiry@example.com", code)
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["code"] == "code_expired"


def test_verify_email_unknown_email_is_missing(cloud_env, email_spy):
    """Unknown address gets the same 410 code_missing as a consumed
    code — no account enumeration."""

    client = _client()
    response = _submit_verify_code(client, "nobody@example.com", "123456")
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["code"] == "code_missing"


def test_resend_voids_previous_code(cloud_env, email_spy, monkeypatch):
    """Single active code: a resend instantly kills the older code."""

    _bypass_rate_limits(monkeypatch)
    client = _client()
    client.post(
        "/api/auth/register", json={"email": "revoke@example.com", "password": "goodpass1"}
    )
    old_code = email_spy.last_verify_code
    assert old_code is not None

    resend = client.post(
        "/api/auth/resend-verification", json={"email": "revoke@example.com"}
    )
    assert resend.status_code == 200, resend.text
    new_code = email_spy.last_verify_code
    assert new_code is not None
    assert new_code != old_code

    # The old code is voided…
    stale = _submit_verify_code(client, "revoke@example.com", old_code)
    assert stale.status_code == 410, stale.text
    assert stale.json()["detail"]["code"] == "code_missing"
    # …and the new one activates the account.
    fresh = _submit_verify_code(client, "revoke@example.com", new_code)
    assert fresh.status_code == 200, fresh.text


def test_legacy_link_entry_returns_410(cloud_env, email_spy):
    """C-01a: the old GET /auth/verify-email?token=… entry point is
    retired — any token value gets a uniform 410 link_disabled."""

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "legacy@example.com", "password": "goodpass1"}
    )
    for token in ("whatever", "a" * 64):
        response = client.get("/api/auth/verify-email", params={"token": token})
        assert response.status_code == 410, response.text
        assert response.json()["detail"]["code"] == "link_disabled"
    # The code itself was NOT consumed by the legacy probes.
    code = email_spy.last_verify_code
    good = _submit_verify_code(client, "legacy@example.com", code)
    assert good.status_code == 200, good.text


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
    _bypass_rate_limits(monkeypatch)

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "reset@example.com", "password": "old-pass-1"}
    )
    _submit_verify_code(client, "reset@example.com", str(email_spy.last_verify_code))
    login = client.post(
        "/api/auth/login", json={"email": "reset@example.com", "password": "old-pass-1"}
    )
    assert login.status_code == 200, login.text
    session_token = login.json()["token"]

    # Forgot triggers an email with a 6-digit code.
    forgot = client.post(
        "/api/auth/forgot-password", json={"email": "reset@example.com"}
    )
    assert forgot.status_code == 200, forgot.text
    code = email_spy.last_reset_code
    assert code is not None
    assert len(code) == 6 and code.isdigit()

    # Reset succeeds and old password stops working.
    reset = client.post(
        "/api/auth/reset-password",
        json={"email": "reset@example.com", "code": code, "newPassword": "new-pass-1"},
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


def test_reset_password_code_is_single_use(cloud_env, email_spy, monkeypatch):
    """Reusing a reset code (e.g. after a successful reset) fails."""

    _bypass_rate_limits(monkeypatch)
    client = _client()
    client.post(
        "/api/auth/register", json={"email": "onceonly@example.com", "password": "old-pass-1"}
    )
    _submit_verify_code(client, "onceonly@example.com", str(email_spy.last_verify_code))
    client.post("/api/auth/forgot-password", json={"email": "onceonly@example.com"})
    code = email_spy.last_reset_code

    first = client.post(
        "/api/auth/reset-password",
        json={"email": "onceonly@example.com", "code": code, "newPassword": "new-pass-1"},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/auth/reset-password",
        json={"email": "onceonly@example.com", "code": code, "newPassword": "newer-pass-1"},
    )
    assert second.status_code == 410, second.text
    assert second.json()["detail"]["code"] in ("code_missing", "code_max_attempts")


def test_reset_password_rejects_wrong_code_with_attempts(cloud_env, email_spy, monkeypatch):
    """A wrong reset code burns an attempt; the message tells the user
    how many attempts remain."""

    _bypass_rate_limits(monkeypatch)
    client = _client()
    client.post(
        "/api/auth/register", json={"email": "wrongrst@example.com", "password": "old-pass-1"}
    )
    _submit_verify_code(client, "wrongrst@example.com", str(email_spy.last_verify_code))
    client.post("/api/auth/forgot-password", json={"email": "wrongrst@example.com"})
    code = email_spy.last_reset_code
    wrong = "000000" if code != "000000" else "111111"

    response = client.post(
        "/api/auth/reset-password",
        json={"email": "wrongrst@example.com", "code": wrong, "newPassword": "new-pass-1"},
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "code_invalid"
    assert "4" in detail["message"]
    # Password unchanged.
    login = client.post(
        "/api/auth/login", json={"email": "wrongrst@example.com", "password": "old-pass-1"}
    )
    assert login.status_code == 200, login.text


def test_reset_rejects_expired_code(cloud_env, email_spy, monkeypatch):
    import datetime as _dt
    from app.db import connect as _connect

    _bypass_rate_limits(monkeypatch)

    client = _client()
    client.post(
        "/api/auth/register", json={"email": "resexp@example.com", "password": "old-pass-1"}
    )
    _submit_verify_code(client, "resexp@example.com", str(email_spy.last_verify_code))

    client.post(
        "/api/auth/forgot-password", json={"email": "resexp@example.com"}
    )
    code = email_spy.last_reset_code
    assert code is not None
    past = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
    with _connect() as conn:
        conn.execute(
            "update email_tokens set expires_at = ? where used_at is null",
            (past,),
        )
    response = client.post(
        "/api/auth/reset-password",
        json={"email": "resexp@example.com", "code": code, "newPassword": "new-pass-1"},
    )
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["code"] == "code_expired"


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
    _register_and_verify(client, "logout@example.com", "goodpass1", email_spy)
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
    _bypass_rate_limits(monkeypatch)

    client = _client()
    _register_and_verify(client, "changepw@example.com", "old-pass-1", email_spy)
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
    # Registered but unverified: also False until the code is entered.
    client.post(
        "/api/auth/register", json={"email": "pending@example.com", "password": "goodpass1"}
    )
    pending = client.get(
        "/api/auth/email-status", params={"email": "pending@example.com"}
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()["verified"] is False
