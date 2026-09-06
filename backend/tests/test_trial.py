"""V3-01 试用链路验收（7 天免费试用 + 到期降级只读）.

验收口径（规格 2026-09-06 第三章 V3-01）:
1. 注册即 7 天免费试用：register 事务内写 trial 行（不绑支付），
   source=trial / plan=trial_7d / status=trialing / price_cents=0 /
   expires_at=注册+7 天（UTC）
2. 到期降级只读：学习动作 403 subscription_expired；书架/进度/统计可看
   （读端点测试见 test_entitlement.py，这里验订阅读路径）
3. 付费后恢复：进度保留（卡片/复习记录不被清）
4. 重复注册不发第二次试用（同邮箱注册 409，行数仍为 1）
5. super 注册路径不写 trial 行
"""

from __future__ import annotations

import uuid
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
    monkeypatch.delenv("VOCAB_TRIAL_DAYS", raising=False)
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


# ---------------------------------------------------------------------------
# 验收 1: 注册事务内写 trial 行
# ---------------------------------------------------------------------------


def test_register_writes_exactly_one_trial_row(cloud_env, email_spy):
    client = _client()
    before = datetime.now(timezone.utc).replace(microsecond=0)
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    with _db() as connection:
        rows = connection.execute(
            "select * from subscriptions where user_id = "
            "(select id from users where email = 'a@test.local')"
        ).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "trial"
    assert row["plan"] == "trial_7d"
    assert row["status"] == "trialing"
    assert row["price_cents"] == 0
    started = datetime.fromisoformat(row["started_at"])
    expires = datetime.fromisoformat(row["expires_at"])
    assert timedelta(seconds=-1) <= started - before <= timedelta(seconds=5)
    assert (
        timedelta(days=7, seconds=-5)
        <= expires - started
        <= timedelta(days=7, seconds=5)
    )
    assert str(row["expires_at"]).endswith("+00:00")


def test_trial_is_configurable(cloud_env, email_spy, monkeypatch):
    monkeypatch.setenv("VOCAB_TRIAL_DAYS", "3")
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    body = client.get(
        "/api/subscription/me", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert body["trialDaysLeft"] == 3
    started = datetime.fromisoformat(body["startedAt"])
    expires = datetime.fromisoformat(body["expiresAt"])
    assert timedelta(days=3, seconds=-5) <= expires - started <= timedelta(days=3, seconds=5)


# ---------------------------------------------------------------------------
# 验收 3: 付费后恢复，进度保留
# ---------------------------------------------------------------------------


def test_paid_after_expiry_preserves_progress(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    now = datetime.now(timezone.utc).isoformat()
    with _db() as connection:
        user_id = connection.execute(
            "select id from users where email = 'a@test.local'"
        ).fetchone()["id"]
        connection.execute(
            "insert into words (id, text, normalized_text, created_at, updated_at)"
            " values ('w1', 'test', 'test', ?, ?)",
            (now, now),
        )
        connection.execute(
            "insert into entries (id, word_id, sense_order, part_of_speech,"
            " definition, definition_source, created_at, updated_at)"
            " values ('e1', 'w1', 1, 'noun', 'a test', 'manual', ?, ?)",
            (now, now),
        )
        connection.execute(
            "insert into cards (id, user_id, entry_id, status, stage, due_at,"
            " created_on) values ('c1', ?, 'e1', 'learning', 2, ?, ?)",
            (user_id, now, now),
        )

    # 试用到期（惰性翻 expired）。
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with _db() as connection:
        connection.execute("update subscriptions set expires_at = ?", (past,))
    me = client.get("/api/subscription/me", headers=headers).json()
    assert me["subscribed"] is False
    assert me["readOnly"] is True

    # 付费（直接走 activate_subscription，支付链路测试见 test_payment.py）。
    from app import subscription as subscription_module
    from app.db import connect

    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        subscription_module.activate_subscription(
            connection,
            user_id=str(user_id),
            plan="monthly",
            amount_cents=500,
            source="alipay",
            order_no="VLTEST",
        )

    me = client.get("/api/subscription/me", headers=headers).json()
    assert me["subscribed"] is True
    assert me["status"] == "active"
    assert me["source"] == "alipay"

    # 进度保留：卡片仍在。
    with _db() as connection:
        count = connection.execute(
            "select count(*) c from cards where user_id = ?", (str(user_id),)
        ).fetchone()["c"]
        assert count == 1


# ---------------------------------------------------------------------------
# 验收 4: 重复注册不发第二次试用
# ---------------------------------------------------------------------------


def test_duplicate_registration_writes_no_second_trial(cloud_env, email_spy):
    client = _client()
    _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    repeat = client.post(
        "/api/auth/register", json={"email": "a@test.local", "password": "pass-1234"}
    )
    # 409 邮箱已存在；429 是发送频率限制（同样不允许进入建号路径）。
    assert repeat.status_code in (409, 429)

    with _db() as connection:
        count = connection.execute("select count(*) c from subscriptions").fetchone()["c"]
        assert count == 1


# ---------------------------------------------------------------------------
# 验收 5: super 不写 trial 行
# ---------------------------------------------------------------------------


def test_super_never_gets_trial_row(cloud_env):
    client = _client()
    login = client.post(
        "/api/auth/login", json={"email": "super@test.local", "password": "super-pass-2026"}
    )
    assert login.status_code == 200

    with _db() as connection:
        rows = connection.execute(
            "select * from subscriptions where user_id = "
            "(select id from users where email = 'super@test.local')"
        ).fetchall()
        assert rows == []


# ---------------------------------------------------------------------------
# 边界态: 注册失败（验证邮件发送失败）整体回滚 — 不留下「有账号无试用行」
# ---------------------------------------------------------------------------


def test_register_rollback_leaves_no_user_nor_trial(cloud_env, monkeypatch):
    client = _client()

    def boom(to: str, code: str) -> None:
        raise emailing.EmailError("smtp down")

    monkeypatch.setattr(emailing, "send_verification_email", boom)

    response = client.post(
        "/api/auth/register", json={"email": "a@test.local", "password": "pass-1234"}
    )
    assert response.status_code == 503

    with _db() as connection:
        users = connection.execute("select count(*) c from users").fetchone()["c"]
        subs = connection.execute("select count(*) c from subscriptions").fetchone()["c"]
        # super 种子账号可能已写入 users，但绝不应有 a@test.local 的行。
        emails = [
            row["email"]
            for row in connection.execute("select email from users").fetchall()
        ]
        assert "a@test.local" not in emails
        assert subs == 0
