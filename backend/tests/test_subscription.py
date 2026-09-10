"""v3 commercial edition subscription tests (V3-02 / V3-08).

Real Bearer-token flow (``real_auth`` marker), Brevo monkey-patched —
subscription endpoints must never touch the email channel anyway
(2026-09-04 拍板: 订阅全程仅 UI 展示).

Covers the v3 read path and configuration surface:
1. 四档定价配置化：默认 500/299/2100/3000 cents + 30/30/180/360 天；
   改环境变量即变（改价不发版）
2. 注册即试用：/me 返回 trialing 视图（试用中用户订阅态）
3. 到期惰性判过期（trialing 同样翻 expired）
4. 2.99 续费优惠资格：试用行不算 / paid active 算 / 宽限期内算 /
   宽限期外不算 / canceled 行不算
5. 未登录 401（全部新端点；/payment/notify 是公开回调除外）
6. super：/me 合成永久视图（不落行）；mock-order / orders / cancel 均 409
7. mock 下单/取消对普通用户 410（V3-08 普通用户不可能 mock）
8. 续费提醒开关默认开、可关
9. 双账号订阅状态互不干扰
10. P3 挂账：_delete_user 跨表删除事务化（含 v3 orders 表）
11. P3 挂账：种子 super 密码缺省时启动告警
12. path 形式 /subscription 301 到 hash 形式
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
    for name in (
        "VOCAB_SUB_PRICE_CENTS",
        "VOCAB_SUB_CURRENCY",
        "VOCAB_PRICE_MONTHLY_CENTS",
        "VOCAB_PRICE_RENEW_CENTS",
        "VOCAB_PRICE_HALFYEAR_CENTS",
        "VOCAB_PRICE_YEARLY_CENTS",
        "VOCAB_TRIAL_DAYS",
        "VOCAB_RENEW_GRACE_DAYS",
        "WECHAT_APPID",
        "WECHAT_MCHID",
        "WECHAT_APIV3_KEY",
        "WECHAT_MCH_PRIVATE_KEY_PATH",
        "WECHAT_MCH_CERT_SERIAL",
        "ALIPAY_APPID",
        "ALIPAY_PRIVATE_KEY_PATH",
        "ALIPAY_PUBLIC_KEY_PATH",
        "PAYMENT_NOTIFY_URL",
        "WECHAT_NOTIFY_URL",
        "ALIPAY_NOTIFY_URL",
    ):
        monkeypatch.delenv(name, raising=False)
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


def _super_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/auth/login", json={"email": "super@test.local", "password": "super-pass-2026"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _db():
    from app.db import connect

    return connect()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _seed_paid_row(
    email: str,
    *,
    plan: str = "monthly",
    status: str = "active",
    source: str = "alipay",
    expires_at: datetime,
    price_cents: int = 500,
) -> None:
    """Insert a paid subscription row directly (bypasses the gateway)."""

    now = datetime.now(timezone.utc).isoformat()
    import uuid

    with _db() as connection:
        user_id = connection.execute(
            "select id from users where email = ?", (email,)
        ).fetchone()["id"]
        connection.execute(
            """
            insert into subscriptions (id, user_id, plan, status, price_cents,
                                       currency, source, started_at, expires_at,
                                       auto_renew, created_at, updated_at)
            values (?, ?, ?, ?, ?, 'CNY', ?, ?, ?, 0, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                user_id,
                plan,
                status,
                price_cents,
                source,
                now,
                expires_at.isoformat(),
                now,
                now,
            ),
        )


# ---------------------------------------------------------------------------
# 1: 四档定价配置化（默认值 + 改配置即变）
# ---------------------------------------------------------------------------


def test_plans_defaults_four_tiers(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    body = client.get("/api/subscription/plans", headers=headers).json()
    assert body["currency"] == "CNY"
    assert body["trialDays"] == 7
    assert body["renewGraceDays"] == 7
    assert body["paymentEnabled"] is False  # 支付渠道密钥未配置
    assert body["renewEligible"] is False  # 试用行不算订阅行
    tiers = {tier["plan"]: tier for tier in body["plans"]}
    assert set(tiers) == {"monthly", "renew", "halfyear", "yearly"}
    assert tiers["monthly"]["priceCents"] == 500
    assert tiers["monthly"]["durationDays"] == 30
    assert tiers["renew"]["priceCents"] == 299
    assert tiers["renew"]["durationDays"] == 30
    assert tiers["halfyear"]["priceCents"] == 2100
    assert tiers["halfyear"]["durationDays"] == 180
    assert tiers["yearly"]["priceCents"] == 3000
    assert tiers["yearly"]["durationDays"] == 360
    assert tiers["monthly"]["label"] == "单月"
    assert tiers["renew"]["label"] == "续费优惠"


def test_plan_price_comes_from_configuration(cloud_env, email_spy, monkeypatch):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setenv("VOCAB_PRICE_MONTHLY_CENTS", "499")
    monkeypatch.setenv("VOCAB_PRICE_YEARLY_CENTS", "2999")
    body = client.get("/api/subscription/plans", headers=headers).json()
    tiers = {tier["plan"]: tier for tier in body["plans"]}
    assert tiers["monthly"]["priceCents"] == 499
    assert tiers["yearly"]["priceCents"] == 2999


# ---------------------------------------------------------------------------
# 2: 注册即试用 — /me 返回 trialing 视图
# ---------------------------------------------------------------------------


def test_me_returns_trial_view_for_new_user(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    body = client.get("/api/subscription/me", headers=headers).json()
    assert body["subscribed"] is True
    assert body["status"] == "trialing"
    assert body["source"] == "trial"
    assert body["plan"] == "trial_7d"
    assert body["trialDaysLeft"] == 7
    assert body["readOnly"] is False
    assert body["renewEligible"] is False
    expires = _parse_iso(body["expiresAt"])
    started = _parse_iso(body["startedAt"])
    assert timedelta(days=7, seconds=-5) <= expires - started <= timedelta(days=7, seconds=5)
    assert body["expiresAt"].endswith("+00:00")


# ---------------------------------------------------------------------------
# 3: 到期后 GET /me 惰性判过期（trialing 同样翻 expired）
# ---------------------------------------------------------------------------


def test_me_lazy_expires_overdue_trial(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with _db() as connection:
        connection.execute("update subscriptions set expires_at = ?", (past,))

    body = client.get("/api/subscription/me", headers=headers).json()
    assert body["subscribed"] is False
    assert body["status"] == "expired"
    assert body["readOnly"] is True
    assert body["expiresAt"] == past

    with _db() as connection:
        status = connection.execute("select status from subscriptions").fetchone()
        assert status["status"] == "expired"


# ---------------------------------------------------------------------------
# 4: 2.99 续费优惠资格（服务端判定口径）
# ---------------------------------------------------------------------------


def test_renew_eligibility_matrix(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    # 试用中 → 不享 2.99（试用行不算订阅行）。
    assert client.get("/api/subscription/me", headers=headers).json()["renewEligible"] is False

    # paid active → 资格 + 宽限截止。
    _seed_paid_row("a@test.local", expires_at=datetime.now(timezone.utc) + timedelta(days=10))
    me = client.get("/api/subscription/me", headers=headers).json()
    assert me["renewEligible"] is True
    assert me["renewDeadline"] is not None

    # 到期 3 天（宽限期内）→ 仍可 2.99。
    _seed_paid_row(
        "a@test.local", expires_at=datetime.now(timezone.utc) - timedelta(days=3)
    )
    assert client.get("/api/subscription/me", headers=headers).json()["renewEligible"] is True

    # 到期 8 天（宽限 7 天外）→ 回标价。
    _seed_paid_row(
        "a@test.local", expires_at=datetime.now(timezone.utc) - timedelta(days=8)
    )
    assert client.get("/api/subscription/me", headers=headers).json()["renewEligible"] is False

    # canceled 行（v2 mock 清退遗留）不作为宽限基准。
    _seed_paid_row(
        "a@test.local",
        status="canceled",
        source="mock",
        expires_at=datetime.now(timezone.utc) + timedelta(days=10),
    )
    assert client.get("/api/subscription/me", headers=headers).json()["renewEligible"] is False


# ---------------------------------------------------------------------------
# 5: 未登录 401（全部端点；notify 是公开回调除外）
# ---------------------------------------------------------------------------


def test_subscription_endpoints_require_auth(cloud_env):
    client = _client()
    assert client.get("/api/subscription/plans").status_code == 401
    assert client.get("/api/subscription/me").status_code == 401
    assert client.post("/api/subscription/orders", json={"plan": "monthly"}).status_code == 401
    assert client.get("/api/subscription/orders/latest").status_code == 401
    assert client.post("/api/subscription/orders/x/cancel").status_code == 401
    assert client.put("/api/subscription/reminder", json={"enabled": False}).status_code == 401
    assert client.post("/api/subscription/mock-order").status_code == 401
    assert client.post("/api/subscription/cancel").status_code == 401


# ---------------------------------------------------------------------------
# 6: super 免订阅读路径
# ---------------------------------------------------------------------------


def test_super_gets_synthetic_permanent_view_without_rows(cloud_env):
    client = _client()
    headers = _super_headers(client)

    me = client.get("/api/subscription/me", headers=headers).json()
    assert me["subscribed"] is True
    assert me["plan"] == "super"
    assert me["status"] == "active"
    assert me["readOnly"] is False
    assert me["trialDaysLeft"] is None
    assert me["renewReminder"] is None

    with _db() as connection:
        count = connection.execute("select count(*) c from subscriptions").fetchone()
        assert count["c"] == 0


def test_super_order_and_stubs_conflict(cloud_env):
    client = _client()
    headers = _super_headers(client)

    response = client.post(
        "/api/subscription/orders",
        json={"plan": "monthly", "channel": "wechat"},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "super_account"

    # mock 下单是 super 专属测试桩：可用（v2 逻辑保留），带 remark 标注。
    response = client.post("/api/subscription/mock-order", headers=headers)
    assert response.status_code == 200
    with _db() as connection:
        row = connection.execute(
            "select source, remark from subscriptions"
        ).fetchone()
    assert row["source"] == "mock"
    assert "测试桩" in str(row["remark"])
    # 真实下单通道对 super 关闭；orders 表始终为空。
    with _db() as connection:
        count = connection.execute("select count(*) c from orders").fetchone()
        assert count["c"] == 0, "orders must stay empty for super"

    # mock 桩取消（super-only）→ 200。
    response = client.post("/api/subscription/cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


# ---------------------------------------------------------------------------
# 7: mock 下单/取消对普通用户 410（V3-08）
# ---------------------------------------------------------------------------


def test_mock_endpoints_gone_for_regular_users(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/api/subscription/mock-order", headers=headers)
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "mock_disabled"

    response = client.post("/api/subscription/cancel", headers=headers)
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "mock_disabled"

    # mock 清退后没有任何新行写入（只有注册时的 trial 行）。
    with _db() as connection:
        rows = connection.execute("select source from subscriptions").fetchall()
        assert [row["source"] for row in rows] == ["trial"]


# ---------------------------------------------------------------------------
# 8: 续费提醒开关（默认开，只控制提醒不影响权益）
# ---------------------------------------------------------------------------


def test_renew_reminder_toggle(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    body = client.get("/api/subscription/me", headers=headers).json()
    assert body["renewReminder"] is True

    off = client.put("/api/subscription/reminder", json={"enabled": False}, headers=headers)
    assert off.status_code == 200
    assert off.json()["renewReminder"] is False
    # 权益不受影响。
    assert off.json()["subscribed"] is True

    on = client.put("/api/subscription/reminder", json={"enabled": True}, headers=headers)
    assert on.json()["renewReminder"] is True


# ---------------------------------------------------------------------------
# 9: 双账号隔离
# ---------------------------------------------------------------------------


def test_two_accounts_subscriptions_do_not_interfere(cloud_env, email_spy):
    client = _client()
    token_a = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    token_b = _register_and_verify(client, "b@test.local", "pass-1234", email_spy)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    _seed_paid_row("a@test.local", expires_at=datetime.now(timezone.utc) + timedelta(days=10))

    me_b = client.get("/api/subscription/me", headers=headers_b).json()
    assert me_b["subscribed"] is True  # b 自己的 trial
    assert me_b["source"] == "trial"

    me_a = client.get("/api/subscription/me", headers=headers_a).json()
    assert me_a["subscribed"] is True
    assert me_a["source"] == "alipay"
    assert me_a["renewEligible"] is True


# ---------------------------------------------------------------------------
# 10 (P3 挂账): _delete_user 跨表删除事务化（含 v3 orders）
# ---------------------------------------------------------------------------


def test_delete_user_cascades_across_all_user_tables(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    now = "2026-09-05T00:00:00+00:00"
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
            " created_on) values ('c1', ?, 'e1', 'new', 0, ?, ?)",
            (user_id, now, now),
        )
        connection.execute(
            "insert into reviews (id, user_id, card_id, rating, reviewed_at,"
            " previous_stage, next_stage, next_due_at, study_date)"
            " values ('r1', ?, 'c1', 'known', ?, 0, 1, ?, ?)",
            (user_id, now, now, now[:10]),
        )
        connection.execute(
            "insert into today_queue (id, user_id, book_id, study_date, position,"
            " card_id, queue_type, created_at)"
            " values ('q1', ?, 'default-book', '2026-09-05', 0, 'c1', 'new', ?)",
            (user_id, now),
        )
        connection.execute(
            "insert into today_queue_snapshots (user_id, book_id, study_date,"
            " created_at) values (?, 'default-book', '2026-09-05', ?)",
            (user_id, now),
        )
        connection.execute(
            "insert into user_settings (user_id, key, value)"
            " values (?, 'current_book_id', 'default-book')",
            (user_id,),
        )
        connection.execute(
            "insert into orders (id, out_trade_no, user_id, plan, amount_cents,"
            " currency, status, channel, created_at, updated_at)"
            " values ('o1', 'VL1', ?, 'monthly', 500, 'CNY', 'pending',"
            " 'wechat', ?, ?)",
            (user_id, now, now),
        )
        session_count = connection.execute(
            "select count(*) c from sessions where user_id = ?", (user_id,)
        ).fetchone()["c"]
        assert session_count == 1

    from app.routes_auth import _delete_user

    _delete_user(str(user_id))

    with _db() as connection:
        assert (
            connection.execute(
                "select count(*) c from users where id = ?", (user_id,)
            ).fetchone()["c"]
            == 0
        )
        for table in (
            "sessions",
            "email_tokens",
            "subscriptions",
            "orders",
            "cards",
            "reviews",
            "today_queue",
            "today_queue_snapshots",
            "user_settings",
        ):
            count = connection.execute(
                f"select count(*) c from {table} where user_id = ?", (user_id,)
            ).fetchone()["c"]
            assert count == 0, f"{table} still holds rows for the deleted user"


# ---------------------------------------------------------------------------
# 11 (P3 挂账): 缺省 super 密码启动告警
# ---------------------------------------------------------------------------


def test_default_super_password_warns_once(cloud_env, monkeypatch, caplog):
    import logging as logging_module

    from app import auth as auth_module
    from app import db as db_module

    monkeypatch.delenv("VOCAB_SUPER_PASSWORD", raising=False)
    monkeypatch.setattr(auth_module, "_default_super_password_warned", False)

    with caplog.at_level(logging_module.WARNING, logger="app.auth"):
        with db_module.connect() as connection:
            auth_module.ensure_super_account(connection)
        with db_module.connect() as connection:
            auth_module.ensure_super_account(connection)

    warnings = [
        record
        for record in caplog.records
        if "VOCAB_SUPER_PASSWORD is not set" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging_module.WARNING


def test_explicit_super_password_does_not_warn(cloud_env, monkeypatch, caplog):
    import logging as logging_module

    from app import auth as auth_module
    from app import db as db_module

    monkeypatch.setattr(auth_module, "_default_super_password_warned", False)

    with caplog.at_level(logging_module.WARNING, logger="app.auth"):
        with db_module.connect() as connection:
            auth_module.ensure_super_account(connection)

    assert not [
        record
        for record in caplog.records
        if "VOCAB_SUPER_PASSWORD is not set" in record.getMessage()
    ]


# ---------------------------------------------------------------------------
# 12: path 形式 /subscription 301 到 hash 形式
# ---------------------------------------------------------------------------


def test_path_form_subscription_redirects_to_hash_form(cloud_env):
    client = _client()

    response = client.get("/subscription", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/#/subscription"
