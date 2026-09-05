"""v2 cloud batch 3 (C-09) subscription tests.

Real Bearer-token flow (``real_auth`` marker), Brevo monkey-patched —
subscription endpoints must never touch the email channel anyway
(2026-09-04 拍板: 订阅全程仅 UI 展示).

Coverage: C-09 原 7 条验收 + 批次 3 新增口径:
1. mock 下单立即 active，expires_at = 下单时刻 + 30 天（UTC）
2. 有效期内重复下单：返回当前订阅，不新建行
3. 到期后 GET /me 惰性判过期（返回 expired）
4. 价格读自配置（VOCAB_SUB_PRICE_CENTS），改配置即变
5. 未登录调用订阅接口 401
6. 订单行 source=mock
7. 双账号订阅状态互不干扰
8. 取消后再订可成功
9. super：/me 合成永久视图（不落行）；mock-order 409 且不产生订单行
10. POST cancel：取消后立刻未订阅；无生效订阅时 409
11. P3 挂账：_delete_user 跨表删除事务化
12. P3 挂账：种子 super 密码缺省时启动告警
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
    monkeypatch.delenv("VOCAB_SUB_PRICE_CENTS", raising=False)
    monkeypatch.delenv("VOCAB_SUB_CURRENCY", raising=False)
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


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# 1 + 6: mock 下单立即生效，30 天 UTC，source=mock
# ---------------------------------------------------------------------------


def test_mock_order_activates_for_30_days_utc(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    before = datetime.now(timezone.utc).replace(microsecond=0)
    response = client.post("/api/subscription/mock-order", headers=headers)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["subscribed"] is True
    assert body["status"] == "active"
    assert body["source"] == "mock"
    started = _parse_iso(body["startedAt"])
    expires = _parse_iso(body["expiresAt"])
    assert timedelta(seconds=-1) <= started - before <= timedelta(seconds=5)
    assert timedelta(days=30, seconds=-1) <= expires - started <= timedelta(days=30, seconds=5)
    # Both timestamps must be UTC (offset-aware, +00:00).
    assert body["startedAt"].endswith("+00:00")
    assert body["expiresAt"].endswith("+00:00")

    with _db() as connection:
        rows = connection.execute(
            "select * from subscriptions where user_id = "
            "(select id from users where email = 'a@test.local')"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["source"] == "mock"
    assert rows[0]["status"] == "active"


# ---------------------------------------------------------------------------
# 2: 有效期内重复下单 → 幂等返回当前订阅，不新建行
# ---------------------------------------------------------------------------


def test_repeat_order_while_active_is_idempotent(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/api/subscription/mock-order", headers=headers).json()
    second = client.post("/api/subscription/mock-order", headers=headers)

    assert second.status_code == 200
    body = second.json()
    assert body["subscribed"] is True
    assert body["startedAt"] == first["startedAt"]
    assert body["expiresAt"] == first["expiresAt"]

    with _db() as connection:
        count = connection.execute("select count(*) c from subscriptions").fetchone()
        assert count["c"] == 1


# ---------------------------------------------------------------------------
# QA batch-3 (P2): 并发下单竞态回归
# ---------------------------------------------------------------------------


def test_concurrent_mock_orders_leave_single_active_row(cloud_env, email_spy, monkeypatch):
    """QA batch-3 regression (P2): two threads ordering concurrently for
    the same user must end with exactly ONE active row. Without the
    BEGIN IMMEDIATE write lock in ``create_mock_order`` the
    read → active-check → INSERT sequence races: both threads read "no
    active row" and both insert, leaving duplicate active
    subscriptions.

    The race window is widened deterministically: the first read of
    ``_latest_row`` is slowed so both threads complete their read
    before either proceeds to the write — exactly the interleaving the
    write lock serializes away."""

    import time
    from concurrent.futures import ThreadPoolExecutor

    from app import auth as auth_module
    from app import subscription as subscription_module

    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    with _db() as connection:
        user_id = connection.execute(
            "select id from users where email = 'a@test.local'"
        ).fetchone()["id"]
    user = auth_module.find_user_by_id(str(user_id))
    assert user is not None

    original_latest_row = subscription_module._latest_row

    def slowed_latest_row(connection, user_id_arg):
        row = original_latest_row(connection, user_id_arg)
        time.sleep(0.2)
        return row

    monkeypatch.setattr(subscription_module, "_latest_row", slowed_latest_row)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(subscription_module.create_mock_order, user)
            for _ in range(2)
        ]
        for future in futures:
            future.result(timeout=30)

    with _db() as connection:
        rows = connection.execute(
            "select status from subscriptions where user_id = ?",
            (str(user_id),),
        ).fetchall()
    assert len(rows) == 1, (
        "concurrent mock orders created duplicate rows — check the "
        "BEGIN IMMEDIATE write lock in create_mock_order"
    )
    assert rows[0]["status"] == "active"


def test_concurrent_cancels_succeed_exactly_once(cloud_env, email_spy, monkeypatch):
    """QA batch-3 regression (P3): two concurrent cancels of the same
    active subscription — with the BEGIN IMMEDIATE write lock one of
    them observes the row already canceled and raises
    SubscriptionError(NO_ACTIVE_SUBSCRIPTION), so exactly one call
    succeeds and the final status is 'canceled'."""

    import time
    from concurrent.futures import ThreadPoolExecutor

    from app import subscription as subscription_module

    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/subscription/mock-order", headers=headers).status_code == 200

    with _db() as connection:
        user_id = connection.execute(
            "select id from users where email = 'a@test.local'"
        ).fetchone()["id"]

    # Same window-widening as the mock-order race test above: the race
    # window is read → check → write, so the sleep sits BETWEEN the
    # read and the write decision, not before the read.
    original_latest_row = subscription_module._latest_row

    def slowed_latest_row(connection, user_id_arg):
        row = original_latest_row(connection, user_id_arg)
        time.sleep(0.25)
        return row

    monkeypatch.setattr(subscription_module, "_latest_row", slowed_latest_row)

    def do_cancel() -> bool:
        try:
            subscription_module.cancel_subscription({"id": str(user_id), "is_super": False})
            return True
        except subscription_module.SubscriptionError as error:
            assert error.code == subscription_module.NO_ACTIVE_SUBSCRIPTION
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(do_cancel) for _ in range(2)]
        outcomes = [future.result(timeout=30) for future in futures]

    assert sum(1 for ok in outcomes if ok) == 1, (
        "exactly one concurrent cancel must succeed — the loser must "
        "see the already-canceled row (BEGIN IMMEDIATE in "
        "cancel_subscription)"
    )
    with _db() as connection:
        row = connection.execute(
            "select status from subscriptions where user_id = ?",
            (str(user_id),),
        ).fetchone()
    assert row is not None and row["status"] == "canceled"


# ---------------------------------------------------------------------------
# Designer C-01a walkthrough P2-⑤: path 形式 /verify-email?token=…
# 必须 301 到 hash 形式，SPA 的链接失效页才能渲染。
# ---------------------------------------------------------------------------


def test_path_form_verify_email_redirects_to_hash_form(cloud_env):
    client = _client()

    response = client.get("/verify-email", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/#/verify-email"

    response = client.get(
        "/verify-email", params={"token": "abc123"}, follow_redirects=False
    )
    assert response.status_code == 301
    assert response.headers["location"] == "/#/verify-email?token=abc123"


# ---------------------------------------------------------------------------
# 3: 到期后 GET /me 惰性判过期
# ---------------------------------------------------------------------------


def test_me_lazy_expires_overdue_subscription(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/subscription/mock-order", headers=headers).status_code == 200

    # Fast-forward the clock by rewinding expires_at into the past.
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with _db() as connection:
        connection.execute("update subscriptions set expires_at = ?", (past,))

    body = client.get("/api/subscription/me", headers=headers).json()
    assert body["subscribed"] is False
    assert body["status"] == "expired"
    assert body["expiresAt"] == past

    # The lazy judgment also persisted the flip.
    with _db() as connection:
        status = connection.execute("select status from subscriptions").fetchone()
        assert status["status"] == "expired"


# ---------------------------------------------------------------------------
# 4: 价格读自配置
# ---------------------------------------------------------------------------


def test_plan_price_comes_from_configuration(cloud_env, email_spy, monkeypatch):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    default_plan = client.get("/api/subscription/plan", headers=headers).json()
    assert default_plan == {
        "plan": "monthly",
        "priceCents": 10,
        "currency": "CNY",
        "period": "month",
    }

    monkeypatch.setenv("VOCAB_SUB_PRICE_CENTS", "499")
    changed = client.get("/api/subscription/plan", headers=headers).json()
    assert changed["priceCents"] == 499

    ordered = client.post("/api/subscription/mock-order", headers=headers).json()
    assert ordered["subscribed"] is True
    with _db() as connection:
        row = connection.execute(
            "select price_cents from subscriptions"
        ).fetchone()
        assert row["price_cents"] == 499


# ---------------------------------------------------------------------------
# 5: 未登录 401（四条接口全部）
# ---------------------------------------------------------------------------


def test_subscription_endpoints_require_auth(cloud_env):
    client = _client()
    assert client.get("/api/subscription/plan").status_code == 401
    assert client.get("/api/subscription/me").status_code == 401
    assert client.post("/api/subscription/mock-order").status_code == 401
    assert client.post("/api/subscription/cancel").status_code == 401


# ---------------------------------------------------------------------------
# 7: 双账号隔离
# ---------------------------------------------------------------------------


def test_two_accounts_subscriptions_do_not_interfere(cloud_env, email_spy):
    client = _client()
    token_a = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    token_b = _register_and_verify(client, "b@test.local", "pass-1234", email_spy)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    assert client.post("/api/subscription/mock-order", headers=headers_a).status_code == 200

    me_b = client.get("/api/subscription/me", headers=headers_b).json()
    assert me_b["subscribed"] is False
    assert me_b["status"] is None

    me_a = client.get("/api/subscription/me", headers=headers_a).json()
    assert me_a["subscribed"] is True


# ---------------------------------------------------------------------------
# 取消 / 取消后再订
# ---------------------------------------------------------------------------


def test_cancel_then_reorder_succeeds(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/api/subscription/mock-order", headers=headers).json()
    canceled = client.post("/api/subscription/cancel", headers=headers)
    assert canceled.status_code == 200
    assert canceled.json()["subscribed"] is False
    assert canceled.json()["status"] == "canceled"

    me = client.get("/api/subscription/me", headers=headers).json()
    assert me["subscribed"] is False

    # 取消后再订：可成功，产生新的一行（新周期）。
    second = client.post("/api/subscription/mock-order", headers=headers).json()
    assert second["subscribed"] is True
    assert second["startedAt"] != first["startedAt"]

    with _db() as connection:
        rows = connection.execute(
            "select status from subscriptions order by started_at"
        ).fetchall()
    assert [row["status"] for row in rows] == ["canceled", "active"]


def test_cancel_without_active_subscription_conflicts(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    response = client.post(
        "/api/subscription/cancel", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_active_subscription"


# ---------------------------------------------------------------------------
# 9: super 免订阅读路径
# ---------------------------------------------------------------------------


def test_super_gets_synthetic_permanent_view_without_rows(cloud_env):
    client = _client()
    login = client.post(
        "/api/auth/login", json={"email": "super@test.local", "password": "super-pass-2026"}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    me = client.get("/api/subscription/me", headers=headers).json()
    assert me == {
        "subscribed": True,
        "plan": "super",
        "status": "active",
        "startedAt": None,
        "expiresAt": None,
        "autoRenew": None,
        "source": None,
    }

    with _db() as connection:
        count = connection.execute("select count(*) c from subscriptions").fetchone()
        assert count["c"] == 0


def test_super_mock_order_returns_409_and_writes_nothing(cloud_env):
    client = _client()
    login = client.post(
        "/api/auth/login", json={"email": "super@test.local", "password": "super-pass-2026"}
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    response = client.post("/api/subscription/mock-order", headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "super_account"

    with _db() as connection:
        count = connection.execute("select count(*) c from subscriptions").fetchone()
        assert count["c"] == 0


def test_super_cancel_conflicts(cloud_env):
    client = _client()
    login = client.post(
        "/api/auth/login", json={"email": "super@test.local", "password": "super-pass-2026"}
    )
    response = client.post(
        "/api/subscription/cancel",
        headers={"Authorization": f"Bearer {login.json()['token']}"},
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# 11 (P3 挂账): _delete_user 跨表删除事务化
# ---------------------------------------------------------------------------


def test_delete_user_cascades_across_all_user_tables(cloud_env, email_spy):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/subscription/mock-order", headers=headers).status_code == 200

    now = "2026-09-05T00:00:00+00:00"
    with _db() as connection:
        user_id = connection.execute(
            "select id from users where email = 'a@test.local'"
        ).fetchone()["id"]
        # Seed one row in every user-scoped table: the study layer
        # (words → entries → cards → reviews → today_queue), the queue
        # header, settings, subscription, session and email token.
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
            " previous_stage, next_stage, next_due_at)"
            " values ('r1', ?, 'c1', 'known', ?, 0, 1, ?)",
            (user_id, now, now),
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
        # sessions row for this user (created by login).
        session_count = connection.execute(
            "select count(*) c from sessions where user_id = ?", (user_id,)
        ).fetchone()["c"]
        assert session_count == 1

    from app.routes_auth import _delete_user

    _delete_user(str(user_id))

    with _db() as connection:
        # users is keyed by id; every other user-scoped table by user_id.
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
# 12 (P3 挂账): 缺省 super 密码启动告警
# ---------------------------------------------------------------------------


def test_default_super_password_warns_once(cloud_env, monkeypatch, caplog):
    import logging as logging_module

    from app import auth as auth_module
    from app import db as db_module

    monkeypatch.delenv("VOCAB_SUPER_PASSWORD", raising=False)
    # Reset the once-per-process latch so the warning fires in this test.
    monkeypatch.setattr(auth_module, "_default_super_password_warned", False)

    with caplog.at_level(logging_module.WARNING, logger="app.auth"):
        with db_module.connect() as connection:
            auth_module.ensure_super_account(connection)
        # A second connect (every request migrates) must not re-warn.
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

    # cloud_env sets VOCAB_SUPER_PASSWORD; make sure the latch is clean.
    monkeypatch.setattr(auth_module, "_default_super_password_warned", False)

    with caplog.at_level(logging_module.WARNING, logger="app.auth"):
        with db_module.connect() as connection:
            auth_module.ensure_super_account(connection)

    assert not [
        record
        for record in caplog.records
        if "VOCAB_SUPER_PASSWORD is not set" in record.getMessage()
    ]
