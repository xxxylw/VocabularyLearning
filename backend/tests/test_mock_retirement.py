"""V3-08 mock 订阅清退验收.

验收口径（规格 2026-09-06 第八章 V3-08）:
1. 普通用户调用 mock 下单/取消 → 410（新用户不可能再走 mock 通道）
2. 存量 mock active 行 → 一次性迁移置 canceled + remark 留档
3. 迁移只跑一次：清退之后新写入的 mock 行（super 测试桩）不会被误清
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
# 验收 1: 普通用户 mock 410（普通用户不可能 mock）
# ---------------------------------------------------------------------------


def test_regular_users_cannot_mock(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/api/subscription/mock-order", headers=headers).status_code == 410
    assert client.post("/api/subscription/cancel", headers=headers).status_code == 410

    with _db() as connection:
        sources = [
            row["source"] for row in connection.execute("select source from subscriptions")
        ]
        assert sources == ["trial"]


# ---------------------------------------------------------------------------
# 验收 2: 存量 mock active 行一次性清退（带备注）
# ---------------------------------------------------------------------------


def test_legacy_mock_rows_retired_with_remark(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    # 模拟 v2 遗留数据：一条 source=mock 的 active 行。
    now = datetime.now(timezone.utc).isoformat()
    with _db() as connection:
        user_id = connection.execute(
            "select id from users where email = 'a@test.local'"
        ).fetchone()["id"]
        connection.execute(
            """
            insert into subscriptions (id, user_id, plan, status, price_cents,
                                      currency, source, started_at, expires_at,
                                      auto_renew, created_at, updated_at)
            values (?, ?, 'monthly', 'active', 0, 'CNY', 'mock', ?, ?, 0, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                str(user_id),
                now,
                (datetime.now(timezone.utc) + timedelta(days=20)).isoformat(),
                now,
                now,
            ),
        )
        # 重置一次性迁移守卫，模拟下一次 connect 触发迁移。
        connection.execute("delete from settings where key = 'v3_mock_cleanup_done'")

    from app.db import connect

    with connect() as connection:
        rows = connection.execute(
            "select status, remark from subscriptions where source = 'mock'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "canceled"
        assert "v3 mock 清退" in str(rows[0]["remark"])
        # 守卫已写入：迁移不会重复执行。
        flag = connection.execute(
            "select value from settings where key = 'v3_mock_cleanup_done'"
        ).fetchone()
        assert flag is not None

    # 用户视角：最新行为 canceled mock → 未订阅（V3-08: 清退即失效，
    # 与 v2 mock 用户到期后一致；再购买须走真实支付通道）。
    me = client.get(
        "/api/subscription/me", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert me["subscribed"] is False
    assert me["status"] == "canceled"
    assert me["source"] == "mock"


# ---------------------------------------------------------------------------
# 验收 3: 迁移一次性 — 清退后新写的 mock 行（super 测试桩）不被误清
# ---------------------------------------------------------------------------


def test_super_mock_stub_survives_after_cleanup(cloud_env):
    client = _client()
    login = client.post(
        "/api/auth/login", json={"email": "super@test.local", "password": "super-pass-2026"}
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    # 触发一次 connect（迁移已在该 DB 上跑过，守卫已置位）。
    assert client.post("/api/subscription/mock-order", headers=headers).status_code == 200

    with _db() as connection:
        row = connection.execute(
            "select status, remark from subscriptions where source = 'mock'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "active"
    assert "测试桩" in str(row["remark"])

    # 再 connect 一次：守卫生效，super 桩不被清退。
    from app.db import connect

    with connect() as connection:
        row = connection.execute(
            "select status from subscriptions where source = 'mock'"
        ).fetchone()
    assert row["status"] == "active"
