"""V3-03 官方支付主链路验收 + V3-02 金额/累加规则（微信支付 Native +
支付宝官方收银台，2026-09-06 拍板替换虎皮椒）.

验收口径（规格 2026-09-06 第四章 V3-03，通道替换后语义不变）:
1. 测试模式全链路：下单（金额快照）→ 微信 code_url / 支付宝跳转链接
   → 回调入账
2. 三异常拒绝: 签名不符 / 金额不符 / 未知订单 → 不确认入账（fail）；
   重复通知 → 幂等 success 不重复续期
3. 对账: 主动查询补单（回调丢失）+ 超时关单
4. 通道不可用（未配置密钥）→ 明确失败（503 payment_not_configured），
   其余功能不受影响
5. QA P2（2026-09-06）: 回调处理段抛异常时报文仍先落档再应答失败

网关 HTTP 层全部 monkeypatch（wechat_create_native_payment /
alipay_create_payment / *_query_order / *_close_order），不发真实请求；
但**验签与解密走真实实现**——测试用 cryptography 现场生成 RSA 密钥对
（微信平台密钥对 / 支付宝公私钥对），回调报文按官方算法真签真加密。
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from app import emailing
from app import payment as payment_module
from app.main import create_app

pytestmark = pytest.mark.real_auth

APIV3_KEY = "0123456789abcdef0123456789abcdef"  # 32 bytes
WECHAT_PLATFORM_SERIAL = "PLAT-SERIAL-TEST-0001"

PAYMENT_ENV_NAMES = (
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
    "ALIPAY_GATEWAY",
    "ALIPAY_RETURN_URL",
    "WECHAT_PAY_GATEWAY",
    "PAYMENT_ORDER_TTL_MINUTES",
    "PAYMENT_ORDER_TITLE",
)


class Keys:
    """Per-test RSA material (wechat platform / mch, alipay)."""

    def __init__(self, tmp_path) -> None:
        def pair():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            return key, key.public_key()

        self.wechat_platform_priv, self.wechat_platform_pub = pair()
        self.wechat_mch_priv, _ = pair()
        self.alipay_priv, self.alipay_pub = pair()

        self.wechat_mch_key_file = tmp_path / "apiclient_key.pem"
        self.wechat_mch_key_file.write_bytes(
            self.wechat_mch_priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.alipay_key_file = tmp_path / "alipay_app_private.pem"
        self.alipay_key_file.write_bytes(
            self.alipay_priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.alipay_pub_file = tmp_path / "alipay_public.pem"
        self.alipay_pub_file.write_bytes(
            self.alipay_pub.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )


@pytest.fixture
def keys(tmp_path) -> Keys:
    return Keys(tmp_path)


def _rsa_sign(private_key, message: bytes) -> str:
    return base64.b64encode(
        private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    ).decode("ascii")


@pytest.fixture
def cloud_env(tmp_path, monkeypatch, keys):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "cloud.sqlite"))
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@test.local")
    monkeypatch.setenv("VOCAB_SUPER_EMAIL", "super@test.local")
    monkeypatch.setenv("VOCAB_SUPER_PASSWORD", "super-pass-2026")
    monkeypatch.setenv("WECHAT_APPID", "wx-test-appid")
    monkeypatch.setenv("WECHAT_MCHID", "1900000001")
    monkeypatch.setenv("WECHAT_APIV3_KEY", APIV3_KEY)
    monkeypatch.setenv("WECHAT_MCH_PRIVATE_KEY_PATH", str(keys.wechat_mch_key_file))
    monkeypatch.setenv("WECHAT_MCH_CERT_SERIAL", "MCH-SERIAL-TEST-0001")
    monkeypatch.setenv("ALIPAY_APPID", "2026000000000001")
    monkeypatch.setenv("ALIPAY_PRIVATE_KEY_PATH", str(keys.alipay_key_file))
    monkeypatch.setenv("ALIPAY_PUBLIC_KEY_PATH", str(keys.alipay_pub_file))
    monkeypatch.setenv("PAYMENT_NOTIFY_URL", "https://example.com")
    # 平台证书缓存注入（跳过 /v3/certificates 网络拉取）。
    monkeypatch.setattr(
        payment_module,
        "_wechat_platform_certs",
        lambda force=False: {WECHAT_PLATFORM_SERIAL: keys.wechat_platform_pub},
    )
    payment_module._PLATFORM_CERTS["keys"] = {}
    payment_module._PLATFORM_CERTS["loaded_at"] = 0.0
    yield keys
    payment_module._PLATFORM_CERTS["keys"] = {}
    payment_module._PLATFORM_CERTS["loaded_at"] = 0.0


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

    created: list[dict[str, str]] = []
    wechat_query_results: dict[str, dict] = {}
    alipay_query_results: dict[str, dict] = {}
    closed_at_gateway: list[str] = []
    is_mobile_flags: list[bool] = []

    def wechat_create(out_trade_no: str, amount_cents: int, time_expire) -> str:
        created.append({"channel": "wechat", "out_trade_no": out_trade_no})
        return f"weixin://wxpay/upi/{out_trade_no}"

    def alipay_create(
        out_trade_no: str, amount_cents: int, time_expire, *, is_mobile: bool
    ) -> str:
        created.append({"channel": "alipay", "out_trade_no": out_trade_no})
        is_mobile_flags.append(is_mobile)
        return f"https://openapi.example.com/gateway.do?out_trade_no={out_trade_no}&sign=abc"

    def wechat_query(out_trade_no: str):
        return wechat_query_results.get(out_trade_no)

    def alipay_query(out_trade_no: str):
        return alipay_query_results.get(out_trade_no)

    def wechat_close(out_trade_no: str) -> None:
        closed_at_gateway.append(out_trade_no)

    def alipay_close(out_trade_no: str) -> None:
        closed_at_gateway.append(out_trade_no)

    monkeypatch.setattr(payment_module, "wechat_create_native_payment", wechat_create)
    monkeypatch.setattr(payment_module, "alipay_create_payment", alipay_create)
    monkeypatch.setattr(payment_module, "wechat_query_order", wechat_query)
    monkeypatch.setattr(payment_module, "alipay_query_order", alipay_query)
    monkeypatch.setattr(payment_module, "wechat_close_order", wechat_close)
    monkeypatch.setattr(payment_module, "alipay_close_order", alipay_close)
    return {
        "created": created,
        "wechat_query_results": wechat_query_results,
        "alipay_query_results": alipay_query_results,
        "closed_at_gateway": closed_at_gateway,
        "is_mobile_flags": is_mobile_flags,
    }


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


def _place_order(
    client: TestClient,
    token: str,
    plan: str = "monthly",
    channel: str = "wechat",
    *,
    mobile: bool = False,
) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if mobile:
        headers["User-Agent"] = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile"
        )
    response = client.post(
        "/api/subscription/orders",
        json={"plan": plan, "channel": channel},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 回调报文构造（真实签名 + 真实 AES-256-GCM 加密）
# ---------------------------------------------------------------------------


def _wechat_notify_body(
    out_trade_no: str, amount_cents: int, trade_state: str = "SUCCESS"
) -> str:
    resource = {
        "out_trade_no": out_trade_no,
        "transaction_id": f"wx-tx-{uuid.uuid4().hex[:12]}",
        "trade_state": trade_state,
        "trade_type": "NATIVE",
        "amount": {"total": amount_cents, "currency": "CNY"},
    }
    plaintext = json.dumps(resource, ensure_ascii=False, separators=(",", ":"))
    nonce = uuid.uuid4().hex[:12]
    associated = "transaction"
    ciphertext = AESGCM(APIV3_KEY.encode()).encrypt(
        nonce.encode(), plaintext.encode(), associated.encode()
    )
    body = {
        "id": str(uuid.uuid4()),
        "event_type": "TRANSACTION.SUCCESS",
        "resource": {
            "algorithm": "AEAD_AES_256_GCM",
            "nonce": nonce,
            "associated_data": associated,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        },
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _wechat_notify(
    keys: Keys, out_trade_no: str, amount_cents: int, trade_state: str = "SUCCESS"
) -> tuple[dict[str, str], str]:
    body = _wechat_notify_body(out_trade_no, amount_cents, trade_state)
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    nonce = uuid.uuid4().hex
    signature = _rsa_sign(
        keys.wechat_platform_priv, f"{timestamp}\n{nonce}\n{body}\n".encode()
    )
    headers = {
        "Wechatpay-Serial": WECHAT_PLATFORM_SERIAL,
        "Wechatpay-Timestamp": timestamp,
        "Wechatpay-Nonce": nonce,
        "Wechatpay-Signature": signature,
    }
    return headers, body


def _alipay_notify_form(
    keys: Keys, out_trade_no: str, total_amount: str, **extra
) -> dict[str, str]:
    form: dict[str, str] = {
        "app_id": "2026000000000001",
        "out_trade_no": out_trade_no,
        "trade_no": f"2026090622001{uuid.uuid4().hex[:10]}",
        "total_amount": total_amount,
        "seller_id": "2088000000000001",
        "trade_status": "TRADE_SUCCESS",
        "notify_time": datetime.now(timezone.utc).isoformat(),
        "notify_type": "trade_status_sync",
        "version": "1.0",
        **extra,
    }
    items = sorted(
        (key, value) for key, value in form.items() if value not in (None, "")
    )
    content = "&".join(f"{key}={value}" for key, value in items)
    form["sign"] = _rsa_sign(keys.alipay_priv, content.encode())
    form["sign_type"] = "RSA2"
    return form


# ---------------------------------------------------------------------------
# 验收 4: 通道不可用 → 明确失败，其余功能不受影响
# ---------------------------------------------------------------------------


def test_unconfigured_channels_reject_orders_cleanly(
    tmp_path, monkeypatch, email_spy
):
    monkeypatch.setenv("VOCAB_DB_PATH", str(tmp_path / "cloud.sqlite"))
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@test.local")
    monkeypatch.setenv("VOCAB_SUPER_EMAIL", "super@test.local")
    monkeypatch.setenv("VOCAB_SUPER_PASSWORD", "super-pass-2026")
    for name in PAYMENT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    assert payment_module.is_configured() is False
    assert payment_module.channels_configured() == {"wechat": False, "alipay": False}

    for channel in ("wechat", "alipay"):
        response = client.post(
            "/api/subscription/orders",
            json={"plan": "monthly", "channel": channel},
            headers=_headers(token),
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "payment_not_configured"

    # 其余功能不受影响：/me、plans、书架照常。
    assert client.get("/api/subscription/me", headers=_headers(token)).status_code == 200
    plans = client.get("/api/subscription/plans", headers=_headers(token)).json()
    assert plans["paymentEnabled"] is False
    assert plans["channels"] == {"wechat": False, "alipay": False}
    assert client.get("/api/books", headers=_headers(token)).status_code == 200

    with _db() as connection:
        assert connection.execute("select count(*) c from orders").fetchone()["c"] == 0


def test_unknown_channel_rejected(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    response = client.post(
        "/api/subscription/orders",
        json={"plan": "monthly", "channel": "xunhupay"},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "payment_channel_invalid"


# ---------------------------------------------------------------------------
# 验收 1: 下单 → 金额快照 → 回调入账全链路（微信）
# ---------------------------------------------------------------------------


def test_wechat_full_order_pay_confirm_flow(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    order = _place_order(client, token, "monthly", "wechat")
    assert order["amountCents"] == 500
    assert order["status"] == "pending"
    assert order["channel"] == "wechat"
    assert order["payQrUrl"].startswith("weixin://wxpay/")
    assert order["payUrl"] is None
    assert order["expiresAt"] is not None

    with _db() as connection:
        row = connection.execute(
            "select * from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row is not None
        assert row["amount_cents"] == 500
        assert row["plan"] == "monthly"

    # 回调：微信回调金额单位即「分」，500 分 == 快照 500 cents。
    headers, body = _wechat_notify(cloud_env, order["outTradeNo"], 500)
    response = client.post(
        "/api/payment/notify/wechat", content=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["code"] == "SUCCESS"

    me = client.get("/api/subscription/me", headers=_headers(token)).json()
    assert me["subscribed"] is True
    assert me["status"] == "active"
    assert me["source"] == "wechat"
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
        assert row["transaction_id"].startswith("wx-tx-")

    # 原始报文留档。
    with _db() as connection:
        archives = connection.execute(
            "select result from payment_callbacks order by created_at"
        ).fetchall()
        assert any(row["result"] == "confirmed" for row in archives)


# ---------------------------------------------------------------------------
# 验收 1: 下单 → 金额快照 → 回调入账全链路（支付宝）
# ---------------------------------------------------------------------------


def test_alipay_full_order_pay_confirm_flow(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    order = _place_order(client, token, "monthly", "alipay")
    assert order["amountCents"] == 500
    assert order["channel"] == "alipay"
    assert order["payUrl"].startswith("https://openapi.example.com/gateway.do?")
    assert "sign=" in order["payUrl"]
    assert order["payQrUrl"] is None

    # 回调：total_amount 为元，5.00 元 == 快照 500 cents。
    form = _alipay_notify_form(cloud_env, order["outTradeNo"], "5.00")
    response = client.post("/api/payment/notify/alipay", data=form)
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


def test_alipay_wap_pay_on_mobile_ua(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    _place_order(client, token, "monthly", "alipay", mobile=True)
    _place_order(client, token, "monthly", "alipay", mobile=False)

    # 上一单在 TTL 内同档位同渠道被复用，第二个请求才是独立下单。
    assert fake_gateway["is_mobile_flags"][0] is True


def test_pending_order_reused_within_ttl_same_channel(
    cloud_env, email_spy, fake_gateway
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    first = _place_order(client, token, "monthly", "wechat")
    second = _place_order(client, token, "monthly", "wechat")
    assert second["outTradeNo"] == first["outTradeNo"]
    assert len(fake_gateway["created"]) == 1  # 网关只被调用一次


def test_pending_order_not_reused_across_channels(
    cloud_env, email_spy, fake_gateway
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    wechat = _place_order(client, token, "monthly", "wechat")
    alipay = _place_order(client, token, "monthly", "alipay")
    assert alipay["outTradeNo"] != wechat["outTradeNo"]
    assert len(fake_gateway["created"]) == 2


def test_create_order_unknown_plan_and_renew_gate(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    response = client.post(
        "/api/subscription/orders",
        json={"plan": "weekly", "channel": "wechat"},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "plan_not_found"

    # 试用用户不享 2.99：renew 档被拒。
    response = client.post(
        "/api/subscription/orders",
        json={"plan": "renew", "channel": "alipay"},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "renew_not_eligible"


def test_gateway_failure_writes_no_order(cloud_env, email_spy, monkeypatch):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    def boom(out_trade_no: str, amount_cents: int, time_expire):
        raise payment_module.PaymentError(
            "payment_gateway_error", "gateway down", status_code=502
        )

    monkeypatch.setattr(payment_module, "wechat_create_native_payment", boom)
    response = client.post(
        "/api/subscription/orders",
        json={"plan": "monthly", "channel": "wechat"},
        headers=_headers(token),
    )
    assert response.status_code == 502

    with _db() as connection:
        assert connection.execute("select count(*) c from orders").fetchone()["c"] == 0


# ---------------------------------------------------------------------------
# 验收 2: 三异常拒绝 + 幂等（微信）
# ---------------------------------------------------------------------------


def test_wechat_notify_bad_signature_rejected(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "wechat")

    headers, body = _wechat_notify(cloud_env, order["outTradeNo"], 500)
    headers["Wechatpay-Signature"] = base64.b64encode(b"tampered").decode("ascii")
    response = client.post(
        "/api/payment/notify/wechat", content=body, headers=headers
    )
    assert response.status_code == 401
    assert response.json()["code"] == "FAIL"

    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"
        archive = connection.execute(
            "select result from payment_callbacks order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "rejected_bad_signature"


def test_wechat_notify_unknown_platform_serial_rejected(
    cloud_env, email_spy, fake_gateway
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "wechat")

    headers, body = _wechat_notify(cloud_env, order["outTradeNo"], 500)
    headers["Wechatpay-Serial"] = "UNKNOWN-SERIAL-9999"
    response = client.post(
        "/api/payment/notify/wechat", content=body, headers=headers
    )
    assert response.status_code == 401


def test_wechat_notify_amount_mismatch_not_confirmed(
    cloud_env, email_spy, fake_gateway
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "wechat")  # snapshot 500 cents

    headers, body = _wechat_notify(cloud_env, order["outTradeNo"], 100)
    response = client.post(
        "/api/payment/notify/wechat", content=body, headers=headers
    )
    assert response.status_code == 400

    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"
        assert (
            connection.execute(
                "select count(*) c from subscriptions where source = 'wechat'"
            ).fetchone()["c"]
            == 0
        )
        archive = connection.execute(
            "select result from payment_callbacks order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "rejected_amount_mismatch"


def test_wechat_duplicate_notify_is_idempotent(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "yearly", "wechat")

    headers, body = _wechat_notify(cloud_env, order["outTradeNo"], 3000)
    first = client.post("/api/payment/notify/wechat", content=body, headers=headers)
    assert first.status_code == 200
    second = client.post("/api/payment/notify/wechat", content=body, headers=headers)
    assert second.status_code == 200

    with _db() as connection:
        rows = connection.execute(
            "select * from subscriptions where source = 'wechat'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["plan"] == "yearly"
        archives = connection.execute(
            "select result from payment_callbacks order by created_at"
        ).fetchall()
        assert [row["result"] for row in archives] == ["confirmed", "already_paid"]


def test_wechat_notify_non_success_state_archived_not_confirmed(
    cloud_env, email_spy, fake_gateway
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "wechat")

    # CLOSED：留档即 success（无需重试），订单不动。
    headers, body = _wechat_notify(cloud_env, order["outTradeNo"], 500, "CLOSED")
    response = client.post(
        "/api/payment/notify/wechat", content=body, headers=headers
    )
    assert response.status_code == 200
    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# 验收 2: 三异常拒绝 + 幂等（支付宝）
# ---------------------------------------------------------------------------


def test_alipay_notify_bad_signature_rejected(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "alipay")

    form = _alipay_notify_form(cloud_env, order["outTradeNo"], "5.00")
    form["total_amount"] = "1.00"  # tampered after signing
    response = client.post("/api/payment/notify/alipay", data=form)
    assert response.text == "fail"

    with _db() as connection:
        archive = connection.execute(
            "select result from payment_callbacks order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "rejected_bad_signature"


def test_alipay_notify_amount_mismatch_not_confirmed(
    cloud_env, email_spy, fake_gateway
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "alipay")  # 500 cents

    form = _alipay_notify_form(cloud_env, order["outTradeNo"], "1.00")
    response = client.post("/api/payment/notify/alipay", data=form)
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


def test_alipay_notify_unknown_order_fails(cloud_env, email_spy, fake_gateway):
    client = _client()

    form = _alipay_notify_form(cloud_env, "VLDOESNOTEXIST", "5.00")
    response = client.post("/api/payment/notify/alipay", data=form)
    assert response.text == "fail"
    with _db() as connection:
        archive = connection.execute(
            "select result from payment_callbacks order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "rejected_unknown_order"


def test_alipay_notify_app_id_mismatch_rejected(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "alipay")

    form = _alipay_notify_form(
        cloud_env, order["outTradeNo"], "5.00", app_id="2099000000000999"
    )
    response = client.post("/api/payment/notify/alipay", data=form)
    assert response.text == "fail"
    with _db() as connection:
        archive = connection.execute(
            "select result from payment_callbacks order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "rejected_app_id"


def test_alipay_notify_wait_buyer_pay_archived_not_confirmed(
    cloud_env, email_spy, fake_gateway
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "alipay")

    form = _alipay_notify_form(
        cloud_env, order["outTradeNo"], "5.00", trade_status="WAIT_BUYER_PAY"
    )
    response = client.post("/api/payment/notify/alipay", data=form)
    assert response.text == "success"  # 无需重试
    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# QA P2（2026-09-06）: 回调处理段抛异常 → 报文仍落档 + 应答失败
# ---------------------------------------------------------------------------


def test_alipay_notify_processing_exception_archived(
    cloud_env, email_spy, fake_gateway, monkeypatch
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "alipay")

    def boom(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(payment_module, "confirm_payment", boom)
    form = _alipay_notify_form(cloud_env, order["outTradeNo"], "5.00")
    response = client.post("/api/payment/notify/alipay", data=form)
    assert response.text == "fail"  # 不再 500 / 丢报文

    with _db() as connection:
        archive = connection.execute(
            "select result, payload_json from payment_callbacks"
            " order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "exception"
        assert order["outTradeNo"] in archive["payload_json"]  # 原始报文在档
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"


def test_wechat_notify_processing_exception_archived(
    cloud_env, email_spy, fake_gateway, monkeypatch
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "wechat")

    def boom(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(payment_module, "confirm_payment", boom)
    headers, body = _wechat_notify(cloud_env, order["outTradeNo"], 500)
    response = client.post(
        "/api/payment/notify/wechat", content=body, headers=headers
    )
    assert response.status_code == 500
    assert response.json()["code"] == "FAIL"

    with _db() as connection:
        archive = connection.execute(
            "select result from payment_callbacks order by created_at desc limit 1"
        ).fetchone()
        assert archive["result"] == "exception"


# ---------------------------------------------------------------------------
# 验收 3: 对账 — 补单 + 超时关单
# ---------------------------------------------------------------------------


def test_reconcile_confirms_wechat_order_after_lost_callback(
    cloud_env, email_spy, fake_gateway
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "halfyear", "wechat")

    # 回调丢失：微信查询接口返回已支付。
    fake_gateway["wechat_query_results"][order["outTradeNo"]] = {
        "trade_state": "SUCCESS",
        "amount_total": 2100,
        "transaction_id": "wx-tx-reconcile",
    }

    me = client.get("/api/subscription/me", headers=_headers(token)).json()
    assert me["subscribed"] is True
    assert me["plan"] == "halfyear"
    assert me["source"] == "wechat"

    with _db() as connection:
        row = connection.execute(
            "select status, transaction_id from orders where out_trade_no = ?",
            (order["outTradeNo"],),
        ).fetchone()
        assert row["status"] == "paid"
        assert row["transaction_id"] == "wx-tx-reconcile"


def test_reconcile_confirms_alipay_order_after_lost_callback(
    cloud_env, email_spy, fake_gateway
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "halfyear", "alipay")

    fake_gateway["alipay_query_results"][order["outTradeNo"]] = {
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "21.00",
        "trade_no": "ali-tx-reconcile",
    }

    me = client.get("/api/subscription/me", headers=_headers(token)).json()
    assert me["subscribed"] is True
    assert me["source"] == "alipay"


def test_reconcile_closes_overdue_pending(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "wechat")

    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with _db() as connection:
        connection.execute(
            "update orders set created_at = ? where out_trade_no = ?",
            (old, order["outTradeNo"]),
        )

    latest = client.get("/api/subscription/orders/latest", headers=_headers(token)).json()
    assert latest["order"]["status"] == "closed"


def test_reconcile_skips_channel_when_unconfigured(
    cloud_env, email_spy, fake_gateway, monkeypatch
):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "wechat")

    monkeypatch.delenv("WECHAT_APPID")
    result = payment_module.reconcile_pending_orders()
    assert result["skipped"] is False
    # 未配置渠道不查网关：查询 mock 无记录、订单保持 pending。
    assert order["outTradeNo"] not in fake_gateway["wechat_query_results"]
    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"


def test_reconcile_amount_mismatch_not_confirmed(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "wechat")

    fake_gateway["wechat_query_results"][order["outTradeNo"]] = {
        "trade_state": "SUCCESS",
        "amount_total": 1,  # 金额不符
        "transaction_id": "wx-tx-bad",
    }
    payment_module.reconcile_pending_orders()

    with _db() as connection:
        row = connection.execute(
            "select status from orders where out_trade_no = ?", (order["outTradeNo"],)
        ).fetchone()
        assert row["status"] == "pending"
        assert (
            connection.execute(
                "select count(*) c from subscriptions where source = 'wechat'"
            ).fetchone()["c"]
            == 0
        )


# ---------------------------------------------------------------------------
# 收银台: latest 订单读 + 取消支付
# ---------------------------------------------------------------------------


def test_cancel_order_flow_closes_at_gateway(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "alipay")

    canceled = client.post(
        f"/api/subscription/orders/{order['outTradeNo']}/cancel", headers=_headers(token)
    )
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "closed"
    assert fake_gateway["closed_at_gateway"] == [order["outTradeNo"]]

    # 取消后不可再取消。
    again = client.post(
        f"/api/subscription/orders/{order['outTradeNo']}/cancel", headers=_headers(token)
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "order_not_cancellable"

    # 已支付订单不可取消。
    order2 = _place_order(client, token, "monthly", "wechat")
    headers, body = _wechat_notify(cloud_env, order2["outTradeNo"], 500)
    client.post("/api/payment/notify/wechat", content=body, headers=headers)
    paid_cancel = client.post(
        f"/api/subscription/orders/{order2['outTradeNo']}/cancel", headers=_headers(token)
    )
    assert paid_cancel.status_code == 409


def test_cancel_survives_gateway_close_failure(cloud_env, email_spy, fake_gateway, monkeypatch):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    order = _place_order(client, token, "monthly", "wechat")

    def boom(out_trade_no: str) -> None:
        raise payment_module.PaymentError("payment_gateway_error", "down", 502)

    monkeypatch.setattr(payment_module, "wechat_close_order", boom)
    canceled = client.post(
        f"/api/subscription/orders/{order['outTradeNo']}/cancel", headers=_headers(token)
    )
    assert canceled.status_code == 200  # best-effort：本地关单仍成功
    assert canceled.json()["status"] == "closed"


def test_cancel_order_of_another_user_404(cloud_env, email_spy, fake_gateway):
    client = _client()
    token_a = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)
    token_b = _register_and_verify(client, "b@test.local", "pass-1234", email_spy)
    order = _place_order(client, token_a, "monthly", "wechat")

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
# V3-02: 续费累加规则 + 各档金额（支付宝回调驱动）
# ---------------------------------------------------------------------------


def _confirm_alipay(client: TestClient, keys: Keys, out_trade_no: str, fee: str) -> None:
    form = _alipay_notify_form(keys, out_trade_no, fee)
    response = client.post("/api/payment/notify/alipay", data=form)
    assert response.text == "success", response.text


def test_accumulation_while_active(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    first = _place_order(client, token, "monthly", "alipay")
    _confirm_alipay(client, cloud_env, first["outTradeNo"], "5.00")
    me1 = client.get("/api/subscription/me", headers=_headers(token)).json()
    first_expires = datetime.fromisoformat(me1["expiresAt"])

    # 有效期内再买半年：从原 expires_at 累加 180 天。
    second = _place_order(client, token, "halfyear", "alipay")
    _confirm_alipay(client, cloud_env, second["outTradeNo"], "21.00")
    me2 = client.get("/api/subscription/me", headers=_headers(token)).json()
    second_expires = datetime.fromisoformat(me2["expiresAt"])
    assert abs((second_expires - first_expires).total_seconds() - timedelta(days=180).total_seconds()) < 5


def test_accumulation_after_expiry_starts_now(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    first = _place_order(client, token, "monthly", "alipay")
    _confirm_alipay(client, cloud_env, first["outTradeNo"], "5.00")

    # 订阅到期（惰性过期）。
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with _db() as connection:
        connection.execute(
            "update subscriptions set expires_at = ? where source = 'alipay'",
            (past,),
        )

    before = datetime.now(timezone.utc)
    second = _place_order(client, token, "yearly", "alipay")
    _confirm_alipay(client, cloud_env, second["outTradeNo"], "30.00")
    me = client.get("/api/subscription/me", headers=_headers(token)).json()
    started = datetime.fromisoformat(me["startedAt"])
    expires = datetime.fromisoformat(me["expiresAt"])
    assert started >= before - timedelta(seconds=5)
    assert abs((expires - started).total_seconds() - timedelta(days=360).total_seconds()) < 5


def test_trial_does_not_accumulate(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    before = datetime.now(timezone.utc)
    order = _place_order(client, token, "monthly", "alipay")
    _confirm_alipay(client, cloud_env, order["outTradeNo"], "5.00")
    me = client.get("/api/subscription/me", headers=_headers(token)).json()
    started = datetime.fromisoformat(me["startedAt"])
    expires = datetime.fromisoformat(me["expiresAt"])
    # 试用剩余天数不结转：从支付时刻起算 30 天。
    assert started >= before - timedelta(seconds=5)
    assert abs((expires - started).total_seconds() - timedelta(days=30).total_seconds()) < 5


def test_renew_price_order_by_eligible_user(cloud_env, email_spy, fake_gateway):
    client = _client()
    token = _register_and_verify(client, "a@test.local", "pass-1234", email_spy)

    first = _place_order(client, token, "monthly", "alipay")
    _confirm_alipay(client, cloud_env, first["outTradeNo"], "5.00")

    # active 用户可下 renew 档（2.99 = 299 cents），且从原到期累加 30 天。
    me1 = client.get("/api/subscription/me", headers=_headers(token)).json()
    renew = _place_order(client, token, "renew", "alipay")
    assert renew["amountCents"] == 299
    _confirm_alipay(client, cloud_env, renew["outTradeNo"], "2.99")
    me2 = client.get("/api/subscription/me", headers=_headers(token)).json()
    added = (
        datetime.fromisoformat(me2["expiresAt"])
        - datetime.fromisoformat(me1["expiresAt"])
    ).total_seconds()
    assert abs(added - timedelta(days=30).total_seconds()) < 5
