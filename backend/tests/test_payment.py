"""V3-03 虎皮椒支付主链路验收 + V3-02 金额/累加规则.

验收口径（规格 2026-09-06 第四章 V3-03）:
1. 测试模式全链路：下单（金额快照）→ 扫码支付页 → 回调入账
2. 三异常拒绝: 签名不符 / 金额不符 / 未知订单 → 不确认入账（fail）；
   重复通知 → 幂等 success 不重复续期
3. 对账: 主动查询补单（回调丢失）+ 超时关单
4. 通道不可用（未配置密钥）→ 明确失败（503），其余功能不受影响

网关 HTTP 层全部 monkeypatch（gateway_create_payment /
gateway_query_order），不发真实请求。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import emailing
from app import payment as payment_module
from app.main import create_app

pytestmark = pytest.mark.real_auth


@pytest.fixture
def cloud_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "cloud.sqlite"))
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@test.local")
    monkeypatch.setenv("VOCAB_SUPER_EMAIL", "super@test.local")
    monkeypatch.setenv("VOCAB_SUPER_PASSWORD", "super-pass-2026")
    monkeypatch.setenv("XUNHUPAY_APPID", "test-appid")
    monkeypatch.setenv("XUNHUPAY_APPSECRET", "test-appsecret")
    monkeypatch.setenv("XUNHUPAY_NOTIFY_URL", "https://example.com/api/payment/notify")
    for name in (
        "VOCAB_PRICE_MONTHLY_CENTS",
        "VOCAB_PRICE_RENEW_CENTS",
        "VOCAB_PRICE_HALFYEAR_CENTS",
        "VOCAB_PRICE_YEARLY_CENTS",
        "XUNHUPAY_ORDER_TTL_MINUTES",
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


@pytest.fixture
def fake_gateway(monkeypatch):
    """Replace the gateway HTTP layer; record created payments."""

    created: list[str] = []
    query_results: dict[str, dict[str, str]] = {}

    def create_payment(out_trade_no: str, total_fee_yuan: str) -> dict[str, str]:
        created.append(out_trade_no)
        return {
            "errcode": "0",
            "url": f"https://pay.example.com/{out_trade_no}",
            "url_qrcode": f"https://qr.example.com/{out_trade_no}.png",
        }

    def query_order(out_trade_no: str) -> dict[str, str] | None:
        return query_results.get(out_trade_no)

    monkeypatch.setattr(payment_module, "gateway_create_payment", create_payment)
    monkeypatch.setattr(payment_module, "gateway_query_order", query_order)
    return {"created": created, "query_results": query_results}


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


def _signed_notify(out_trade_no: str, total_fee: str, **extra) -> dict[str, str]:
    """Build a gateway callback payload with a valid hash."""

    payload: dict[str, str] = {
        "appid": "test-appid",
        "trade_order_id": out_trade_no,
        "total_fee": total_fee,
        "transaction_id": f"tx-{uuid.uuid4().hex[:8]}",
        "open_order_id": f"open-{uuid.uuid4().hex[:8]}",
        "status": "OD",
        "time": str(int(datetime.now(timezone.utc).timestamp())),
        "nonce_str": uuid.uuid4().hex,
        "built_in": "0",
        **extra,
    }
    payload["hash"] = payment_module._sign(payload)
    return payload


def _place_order(client: TestClient, token: str, plan: str = "monthly") -> dict:
    response = client.post(
        "/api/subscription/orders", json={"plan": plan}, headers=_headers(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 验收 4: 通道不可用 → 明确失败，其余功能不受影响
# ---------------------------------------------------------------------------


def test_unconfigured_gateway_rejects_orders_cleanly(tmp_path, monkeypatch, email_spy):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "cloud.sqlite"))
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@test.local")
    monkeypatch.setenv("VOCAB_SUPER_EMAIL", "super@test.local")
    monkeypatch.setenv("VOCAB_SUPER_PASSWORD", "super-pass-2026")
    for name in ("XUNHUPAY_APPID", "XUNHUPAY_APPSECRET", "XUNHUPAY_NOTIFY_URL"):
        monkeypatch.delenv(name, raising=False)

    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    assert payment_module.is_configured() is False

    response = client.post(
        "/api/subscription/orders", json={"plan": "monthly"}, headers=_headers(token)
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "payment_not_configured"

    # 其余功能不受影响：/me、plans、书架照常。
    assert client.get("/api/subscription/me", headers=_headers(token)).status_code == 200
    plans = client.get("/api/subscription/plans", headers=_headers(token)).json()
    assert plans["paymentEnabled"] is False
    assert client.get("/api/books", headers=_headers(token)).status_code == 200

    with _db() as connection:
        assert connection.execute("select count(*) c from orders").fetchone()["c"] == 0


# ---------------------------------------------------------------------------
# 验收 1: 下单 → 金额快照 → 回调入账全链路
# ---------------------------------------------------------------------------


def test_full_order_pay_confirm_flow(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    order = _place_order(client, token, "monthly")
    assert order["amountCents"] == 500
    assert order["status"] == "pending"
    assert order["channel"] == "xunhupay"
    assert order["payUrl"].startswith("https://pay.example.com/")
    assert order["payQrUrl"].startswith("https://qr.example.com/")
    assert order["expiresAt"] is not None

    with _db() as connection:
        row = connection.execute(
            "select * from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row is not None
        assert row["amount_cents"] == 500
        assert row["plan"] == "monthly"

    # 回调：total_fee 为元，5.00 元 == 快照 500 cents。
    notify = _signed_notify(order["outTradeNo"], "5.00")
    response = client.post("/api/payment/notify", data=notify)
    assert response.status_code == 200
    assert response.text == "success"

    me = client.get("/api/subscription/me", headers=_headers(token)).json()
    assert me["subscribed"] is True
    assert me["status"] == "active"
    assert me["source"] == "alipay"
    assert me["plan"] == "monthly"
    started = datetime.fromisoformat(me["startedAt"])
    expires = datetime.fromisoformat(me["expiresAt"])
    assert expires - started == timedelta(days=30)

    with _db() as connection:
        row = connection.execute(
            "select status, transaction_id from orders where out_trade_no = ?",
            (order["outTradeNo"],),
        ).fetchone()
        assert row["status"] == "paid"
        assert row["transaction_id"] == notify["transaction_id"]

    # 原始报文留档。
    with _db() as connection:
        archives = connection.execute(
            "select result from payment_callbacks order by created_at"
        ).fetchall()
        assert any(row["result"] == "confirmed" for row in archives)


def test_create_order_unknown_plan_and_renew_gate(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    response = client.post(
        "/api/subscription/orders", json={"plan": "weekly"}, headers=_headers(token)
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "plan_not_found"

    # 试用用户不享 2.99：renew 档被拒。
    response = client.post(
        "/api/subscription/orders", json={"plan": "renew"}, headers=_headers(token)
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "renew_not_eligible"


def test_gateway_failure_writes_no_order(cloud_env, email_spy, monkeypatch):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    def boom(out_trade_no: str, total_fee_yuan: str):
        raise payment_module.PaymentError(
            "payment_gateway_error", "gateway down", status_code=502
        )

    monkeypatch.setattr(payment_module, "gateway_create_payment", boom)
    response = client.post(
        "/api/subscription/orders", json={"plan": "monthly"}, headers=_headers(token)
    )
    assert response.status_code == 502

    with _db() as connection:
        assert connection.execute("select count(*) c from orders").fetchone()["c"] == 0


def test_pending_order_reused_within_ttl(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    first = _place_order(client, token, "monthly")
    second = _place_order(client, token, "monthly")
    assert second["outTradeNo"] == first["outTradeNo"]
    assert len(fake_gateway["created"]) == 1  # 网关只被调用一次


# ---------------------------------------------------------------------------
# 验收 2: 三异常拒绝 + 幂等
# ---------------------------------------------------------------------------


def test_notify_bad_signature_rejected(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly")

    notify = _signed_notify(order["outTradeNo"], "5.00")
    notify["total_fee"] = "1.00"  # tampered after signing
    response = client.post("/api/payment/notify", data=notify)
    assert response.text == "fail"

    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"
        assert (
            connection.execute("select count(*) c from subscriptions where source = 'alipay'").fetchone()["c"]
            == 0
        )
        archive = connection.execute(
            "select result from payment_callbacks order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "rejected_bad_signature"


def test_notify_amount_mismatch_not_confirmed(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly")  # snapshot 500 cents

    response = client.post(
        "/api/payment/notify", data=_signed_notify(order["outTradeNo"], "1.00")
    )
    assert response.text == "fail"

    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"
        assert (
            connection.execute(
                "select count(*) c from subscriptions where source = 'alipay'"
            ).fetchone()["c"]
            == 0
        )
        archive = connection.execute(
            "select result from payment_callbacks order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "rejected_amount_mismatch"


def test_notify_unknown_order_fails(cloud_env, email_spy, fake_gateway):
    client = _client()

    response = client.post(
        "/api/payment/notify", data=_signed_notify("VLDOESNOTEXIST", "5.00")
    )
    assert response.text == "fail"
    with _db() as connection:
        archive = connection.execute(
            "select result from payment_callbacks order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "rejected_unknown_order"


def test_duplicate_notify_is_idempotent(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "yearly")

    notify = _signed_notify(order["outTradeNo"], "34.00")
    first = client.post("/api/payment/notify", data=notify)
    assert first.text == "success"
    second = client.post("/api/payment/notify", data=notify)
    assert second.text == "success"

    with _db() as connection:
        rows = connection.execute(
            "select * from subscriptions where source = 'alipay'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["plan"] == "yearly"
        archives = connection.execute(
            "select result from payment_callbacks order by created_at"
        ).fetchall()
        assert [row["result"] for row in archives] == ["confirmed", "already_paid"]


def test_notify_refund_status_archived_not_confirmed(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly")

    payload: dict[str, str] = {
        "appid": "test-appid",
        "trade_order_id": order["outTradeNo"],
        "total_fee": "5.00",
        "status": "CD",  # 已退款
        "time": "0",
        "nonce_str": uuid.uuid4().hex,
    }
    payload["hash"] = payment_module._sign(payload)
    response = client.post("/api/payment/notify", data=payload)
    assert response.text == "success"  # 无需重试
    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# 验收 3: 对账 — 补单 + 超时关单
# ---------------------------------------------------------------------------


def test_reconcile_confirms_order_after_lost_callback(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "halfyear")

    # 回调丢失：网关查询接口返回已支付。
    fake_gateway["query_results"][order["outTradeNo"]] = {
        "errcode": "0",
        "out_trade_order": order["outTradeNo"],
        "status": "OD",
        "total_fee": "17.00",
        "transaction_id": "tx-reconcile",
    }

    me = client.get("/api/subscription/me", headers=_headers(token)).json()
    assert me["subscribed"] is True
    assert me["plan"] == "halfyear"
    assert me["source"] == "alipay"

    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "paid"


def test_reconcile_closes_overdue_pending(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly")

    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with _db() as connection:
        connection.execute(
            "update orders set created_at = ? where out_trade_no = ?",
            (old, order["outTradeNo"]),
        )

    latest = client.get("/api/subscription/orders/latest", headers=_headers(token)).json()
    assert latest["order"]["status"] == "closed"


def test_reconcile_skipped_when_unconfigured(cloud_env, email_spy, fake_gateway, monkeypatch):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly")

    monkeypatch.delenv("XUNHUPAY_APPID")
    result = payment_module.reconcile_pending_orders()
    assert result["skipped"] is True
    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"


def test_reconcile_amount_mismatch_not_confirmed(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly")

    fake_gateway["query_results"][order["outTradeNo"]] = {
        "errcode": "0",
        "status": "OD",
        "total_fee": "0.01",
        "transaction_id": "tx-bad",
    }
    payment_module.reconcile_pending_orders()

    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"
        assert (
            connection.execute(
                "select count(*) c from subscriptions where source = 'alipay'"
            ).fetchone()["c"]
            == 0
        )


# ---------------------------------------------------------------------------
# 收银台: latest 订单读 + 取消支付
# ---------------------------------------------------------------------------


def test_cancel_order_flow(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly")

    canceled = client.post(
        f"/api/subscription/orders/{order['outTradeNo']}/cancel", headers=_headers(token)
    )
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "closed"

    # 取消后不可再取消。
    again = client.post(
        f"/api/subscription/orders/{order['outTradeNo']}/cancel", headers=_headers(token)
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "order_not_cancellable"

    # 已支付订单不可取消。
    order2 = _place_order(client, token, "monthly")
    client.post("/api/payment/notify", data=_signed_notify(order2["outTradeNo"], "5.00"))
    paid_cancel = client.post(
        f"/api/subscription/orders/{order2['outTradeNo']}/cancel", headers=_headers(token)
    )
    assert paid_cancel.status_code == 409


def test_cancel_order_of_another_user_404(cloud_env, email_spy, fake_gateway):
    client = _client()
    token_a = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    token_b = _register_and_verify(client, "b@test.local", "pass-1234", email_spy)
    order = _place_order(client, token_a, "monthly")

    response = client.post(
        f"/api/subscription/orders/{order['outTradeNo']}/cancel", headers=_headers(token_b)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "order_not_found"


def test_latest_order_returns_none_initially(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    latest = client.get("/api/subscription/orders/latest", headers=_headers(token)).json()
    assert latest["order"] is None
    assert latest["subscription"]["status"] == "trialing"


# ---------------------------------------------------------------------------
# V3-02: 续费累加规则 + 各档金额
# ---------------------------------------------------------------------------


def _confirm_order(client: TestClient, out_trade_no: str, fee: str) -> None:
    response = client.post("/api/payment/notify", data=_signed_notify(out_trade_no, fee))
    assert response.text == "success", response.text


def test_accumulation_while_active(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    first = _place_order(client, token, "monthly")
    _confirm_order(client, first["outTradeNo"], "5.00")
    me1 = client.get("/api/subscription/me", headers=_headers(token)).json()
    first_expires = datetime.fromisoformat(me1["expiresAt"])

    # 有效期内再买半年：从原 expires_at 累加 180 天。
    second = _place_order(client, token, "halfyear")
    _confirm_order(client, second["outTradeNo"], "17.00")
    me2 = client.get("/api/subscription/me", headers=_headers(token)).json()
    second_expires = datetime.fromisoformat(me2["expiresAt"])
    assert abs((second_expires - first_expires).total_seconds() - timedelta(days=180).total_seconds()) < 5


def test_accumulation_after_expiry_starts_now(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    first = _place_order(client, token, "monthly")
    _confirm_order(client, first["outTradeNo"], "5.00")

    # 订阅到期（惰性过期）。
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with _db() as connection:
        connection.execute(
            "update subscriptions set expires_at = ? where source = 'alipay'",
            (past,),
        )

    before = datetime.now(timezone.utc)
    second = _place_order(client, token, "yearly")
    _confirm_order(client, second["outTradeNo"], "34.00")
    me = client.get("/api/subscription/me", headers=_headers(token)).json()
    started = datetime.fromisoformat(me["startedAt"])
    expires = datetime.fromisoformat(me["expiresAt"])
    assert started >= before - timedelta(seconds=5)
    assert abs((expires - started).total_seconds() - timedelta(days=360).total_seconds()) < 5


def test_trial_does_not_accumulate(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    before = datetime.now(timezone.utc)
    order = _place_order(client, token, "monthly")
    _confirm_order(client, order["outTradeNo"], "5.00")
    me = client.get("/api/subscription/me", headers=_headers(token)).json()
    started = datetime.fromisoformat(me["startedAt"])
    expires = datetime.fromisoformat(me["expiresAt"])
    # 试用剩余天数不结转：从支付时刻起算 30 天。
    assert started >= before - timedelta(seconds=5)
    assert abs((expires - started).total_seconds() - timedelta(days=30).total_seconds()) < 5


def test_renew_price_order_by_eligible_user(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    first = _place_order(client, token, "monthly")
    _confirm_order(client, first["outTradeNo"], "5.00")

    # active 用户可下 renew 档（2.99 = 299 cents），且从原到期累加 30 天。
    me1 = client.get("/api/subscription/me", headers=_headers(token)).json()
    renew = _place_order(client, token, "renew")
    assert renew["amountCents"] == 299
    _confirm_order(client, renew["outTradeNo"], "2.99")
    me2 = client.get("/api/subscription/me", headers=_headers(token)).json()
    added = (
        datetime.fromisoformat(me2["expiresAt"])
        - datetime.fromisoformat(me1["expiresAt"])
    ).total_seconds()
    assert abs(added - timedelta(days=30).total_seconds()) < 5
