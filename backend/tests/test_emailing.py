"""Brevo email channel logging + sender self-check (P0 follow-up).

Root cause being guarded against: Brevo answers 201 to a send request even
when the configured sender is not validated, then asynchronously rejects
delivery — the app saw nothing and users never received their verification
email. These tests pin the three hardening behaviours:

1. a successful send logs status + messageId (correlatable with the Brevo
   event log);
2. a non-2xx response is logged (status + body) before the existing
   ``EmailError`` rollback behaviour fires;
3. the startup self-check warns when ``BREVO_SENDER_EMAIL`` is not among
   the validated senders returned by ``GET /v3/senders``.

All Brevo I/O is monkey-patched; nothing here touches the network.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from app import emailing


@pytest.fixture
def brevo_env(monkeypatch):
    """Configure the email channel so ``_send`` reaches the HTTP layer."""

    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@test.local")
    return None


class _FakeResponse:
    """Minimal stand-in for the object returned by ``urlopen``."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._code = status
        self._body = body.encode("utf-8")

    def getcode(self) -> int:
        return self._code

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    error = urllib.error.HTTPError(
        url=emailing.BREVO_API_URL,
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")),
    )
    return error


# ---------------------------------------------------------------------------
# 1. Successful send logs status + messageId
# ---------------------------------------------------------------------------


def test_send_success_logs_status_and_message_id(brevo_env, monkeypatch, caplog):
    calls = {}

    def fake_urlopen(request, timeout=None):
        calls["url"] = request.full_url
        calls["timeout"] = timeout
        return _FakeResponse(201, json.dumps({"messageId": "<abc-123@relay>"}))

    monkeypatch.setattr(emailing.urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.INFO, logger="app.emailing"):
        emailing.send_verification_email("alice@example.com", "tok" * 12)

    assert calls["url"] == emailing.BREVO_API_URL
    assert calls["timeout"] == emailing.REQUEST_TIMEOUT_SECONDS
    accepted = [
        r for r in caplog.records if "Brevo send accepted" in r.getMessage()
    ]
    assert len(accepted) == 1
    message = accepted[0].getMessage()
    assert "status=201" in message
    assert "<abc-123@relay>" in message
    assert "alice@example.com" in message
    assert accepted[0].levelno == logging.INFO


# ---------------------------------------------------------------------------
# 2. Non-2xx response is logged, then EmailError raises (rollback preserved)
# ---------------------------------------------------------------------------


def test_send_non_2xx_logs_body_and_raises_email_error(brevo_env, monkeypatch, caplog):
    def fake_urlopen(request, timeout=None):
        raise _http_error(400, '{"message": "invalid sender"}')

    monkeypatch.setattr(emailing.urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.ERROR, logger="app.emailing"):
        with pytest.raises(emailing.EmailError) as excinfo:
            emailing.send_password_reset_email("bob@example.com", "tok" * 12)

    assert "400" in str(excinfo.value)
    assert "invalid sender" in str(excinfo.value)
    rejected = [
        r for r in caplog.records if "Brevo send rejected" in r.getMessage()
    ]
    assert len(rejected) == 1
    message = rejected[0].getMessage()
    assert "status=400" in message
    assert "invalid sender" in message
    assert "bob@example.com" in message
    assert rejected[0].levelno == logging.ERROR


# ---------------------------------------------------------------------------
# 3. Startup self-check warns when the sender is not validated
# ---------------------------------------------------------------------------


def test_self_check_warns_when_sender_not_validated(brevo_env, monkeypatch, caplog):
    def fake_urlopen(request, timeout=None):
        assert request.full_url == emailing.BREVO_SENDERS_URL
        assert timeout <= 10
        return _FakeResponse(
            200,
            json.dumps(
                {"senders": [{"email": "someone-else@example.com", "name": "x"}]}
            ),
        )

    monkeypatch.setattr(emailing.urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.WARNING, logger="app.emailing"):
        # Must not raise, whatever it finds.
        emailing.verify_sender_configuration()

    warnings = [
        r for r in caplog.records if "NOT a validated Brevo sender" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "noreply@test.local" in warnings[0].getMessage()
    assert warnings[0].levelno == logging.WARNING


def test_self_check_ok_when_sender_validated(brevo_env, monkeypatch, caplog):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(
            200,
            json.dumps(
                {"senders": [{"email": "Noreply@Test.Local", "name": "v"}]}
            ),
        )

    monkeypatch.setattr(emailing.urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.INFO, logger="app.emailing"):
        emailing.verify_sender_configuration()

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "is a validated sender" in r.getMessage() for r in caplog.records
    )


def test_self_check_skipped_without_api_key(monkeypatch, caplog):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.delenv("BREVO_SENDER_EMAIL", raising=False)

    def fail_urlopen(request, timeout=None):  # pragma: no cover - must not run
        raise AssertionError("self-check must not hit the network without a key")

    monkeypatch.setattr(emailing.urllib.request, "urlopen", fail_urlopen)
    with caplog.at_level(logging.WARNING, logger="app.emailing"):
        emailing.verify_sender_configuration()

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_self_check_never_raises_on_network_error(brevo_env, monkeypatch, caplog):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(emailing.urllib.request, "urlopen", fake_urlopen)
    with caplog.at_level(logging.WARNING, logger="app.emailing"):
        emailing.verify_sender_configuration()  # must not raise

    assert any(
        "could not reach the API" in r.getMessage() for r in caplog.records
    )


def test_lifespan_self_check_failure_does_not_block_startup(monkeypatch, caplog):
    """Even an unexpected exception in the self-check must not break boot."""

    from fastapi.testclient import TestClient

    from app.main import create_app

    def exploding_self_check():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(emailing, "verify_sender_configuration", exploding_self_check)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        with TestClient(create_app()) as client:
            assert client.get("/api/health").json()["ok"] is True

    assert any(
        "raised unexpectedly" in r.getMessage() for r in caplog.records
    )
