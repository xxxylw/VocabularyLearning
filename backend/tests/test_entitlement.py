"""V3-01 只读模式：学习动作后端 403 拦截 + 读端点不受影响.

验收口径（规格 2026-09-06 第三章 V3-01 产品定调 3）:
- 学习动作（学新 /prepare-jobs、复习 & 拼写背后的卡片写入
  /cards/{id}/reviews、开今日学习 /study/today/start）→ 403
  code=subscription_expired
- 只读端点（书架 /books、进度 /book-words/progress、统计类）→ 200
- 试用中 / active / super 三种状态学习动作放行
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import emailing
from app.main import create_app

pytestmark = pytest.mark.real_auth


@pytest.fixture
def cloud_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "cloud.sqlite"))
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@test.local")
    monkeypatch.setenv("VOCAB_SUPER_EMAIL", "super@test.local")
    monkeypatch.setenv("VOCAB_SUPER_PASSWORD", "super-pass-2026")
    monkeypatch.delenv("XUNHUPAY_APPID", raising=False)
    monkeypatch.delenv("XUNHUPAY_APPSECRET", raising=False)
    monkeypatch.delenv("XUNHUPAY_NOTIFY_URL", raising=False)
    return tmp_path


class EmailRecorder:
    def __init__(self) -> None:
        self.last_verify_code: str | None = None

    def _verify(self, to: str, code: str) -> None:
        self.last_verify_code = code

    def _reset(self, to: str, code: str) -> None:
        pass


@pytest.fixture
def email_spy(monkeypatch) -> EmailRecorder:
    recorder = EmailRecorder()
    monkeypatch.setattr(emailing, "send_verification_email", recorder._verify)
    monkeypatch.setattr(emailing, "send_password_reset_email", recorder._reset)
    return recorder


def _client() -> TestClient:
    return TestClient(create_app())


def _register_and_verify(client: TestClient, email: str, password: str, email_spy):
    response = client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    verified = client.post(
        "/api/auth/verify-email",
        json={"email": email, "code": str(email_spy.last_verify_code)},
    )
    assert verified.status_code == 200, verified.text
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["token"]


def _db():
    from app.db import connect

    return connect()


def _expire_trial(email: str) -> None:
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with _db() as connection:
        connection.execute("update subscriptions set expires_at = ?", (past,))


def _activate_paid(email: str) -> None:
    from app import subscription as subscription_module
    from app.db import connect

    with connect() as connection:
        user_id = connection.execute(
            "select id from users where email = ?", (email,)
        ).fetchone()["id"]
        connection.execute("BEGIN IMMEDIATE")
        subscription_module.activate_subscription(
            connection,
            user_id=str(user_id),
            plan="monthly",
            amount_cents=500,
            source="alipay",
        )


# ---------------------------------------------------------------------------
# 到期只读：学习动作 403 / 读端点 200
# ---------------------------------------------------------------------------


def test_expired_trial_blocks_study_actions_with_403(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}
    _expire_trial("a@test.local")

    blocked = [
        client.post(
            "/api/prepare-jobs", json={"scope": "today"}, headers=headers
        ),
        client.post(
            "/api/study/today/start", json={"dailyNewWordTarget": 5}, headers=headers
        ),
        client.post(
            "/api/cards/some-card/reviews",
            json={"rating": "known", "reviewedAt": "2026-09-06T00:00:00+00:00"},
            headers=headers,
        ),
    ]
    for response in blocked:
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "subscription_expired"

    # 读端点不受影响（书架 / 进度 / 今日统计）。
    assert client.get("/api/books", headers=headers).status_code == 200
    assert client.get("/api/book-words/progress", headers=headers).status_code == 200
    assert (
        client.get(
            "/api/reviews/due", params={"date": "2026-09-06"}, headers=headers
        ).status_code
        == 200
    )


def test_trial_active_allows_study_actions(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/api/study/today/start", json={"dailyNewWordTarget": 5}, headers=headers
    )
    assert response.status_code != 403


def test_paid_active_allows_study_actions(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}
    _expire_trial("a@test.local")
    _activate_paid("a@test.local")

    response = client.post(
        "/api/study/today/start", json={"dailyNewWordTarget": 5}, headers=headers
    )
    assert response.status_code != 403


def test_super_bypasses_entitlement(cloud_env):
    client = _client()
    login = client.post(
        "/api/auth/login", json={"email": "super@test.local", "password": "super-pass-2026"}
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    response = client.post(
        "/api/study/today/start", json={"dailyNewWordTarget": 5}, headers=headers
    )
    assert response.status_code != 403
