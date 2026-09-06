"""虎皮椒 (xunhupay) payment gateway client + order lifecycle (v3 P0).

Protocol notes (official doc https://www.xunhupay.com/doc/api/pay.html,
re-verified 2026-09-06):
- 下单: POST {gateway}/payment/do.html, form-encoded. Required fields:
  version=1.1, appid, trade_order_id, total_fee (元, decimal), title,
  time (unix seconds), notify_url, nonce_str, hash. Response JSON:
  errcode==0 + url (H5 jump link) + url_qrcode (QR image, PC 用).
- 查询: POST {gateway}/payment/query.html with appid, out_trade_order
  (商户订单号), time, nonce_str, hash.
- 回调: POST form to notify_url; reply the plain text ``success`` or the
  gateway retries 6 times. Fields include trade_order_id, total_fee,
  transaction_id, open_order_id, status (OD 已支付 / CD 已退款 / RD 退款
  中 / UD 退款失败).
- 签名: non-empty params sorted by key ASCII, joined ``k=v&…``, append
  appsecret directly, MD5 → 32-hex lowercase. The ``hash`` field itself
  never participates; verification must tolerate unknown extra fields.

Configuration is env-driven and OPTIONAL on purpose (task 拍板: the user
has not registered for xunhupay keys yet): without XUNHUPAY_APPID /
XUNHUPAY_APPSECRET / XUNHUPAY_NOTIFY_URL the payment module enters an
explicit "not configured" state — 下单 answers 503 payment_not_configured
and nothing else is affected. Keys到位后仅配环境变量即可启用.

All amounts are cents locally; total_fee (元) conversions happen ONLY at
the gateway boundary and are compared back against the snapshotted
amount on confirm (金额不符不确认入账).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib import parse as url_parse
from urllib import request as url_request

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY = "https://api.xunhupay.com"
DEFAULT_ORDER_TTL_MINUTES = 15
HTTP_TIMEOUT_SECONDS = 15

PLAN_NOT_FOUND = "plan_not_found"
RENEW_NOT_ELIGIBLE = "renew_not_eligible"
PAYMENT_NOT_CONFIGURED = "payment_not_configured"
GATEWAY_ERROR = "payment_gateway_error"
ORDER_NOT_FOUND = "order_not_found"
ORDER_NOT_CANCELLABLE = "order_not_cancellable"
SUPER_CONFLICT = "super_account"


class PaymentError(Exception):
    """Domain error carrying an HTTP-ready code."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Gateway configuration
# ---------------------------------------------------------------------------


def _appid() -> str:
    return os.environ.get("XUNHUPAY_APPID", "").strip()


def _appsecret() -> str:
    return os.environ.get("XUNHUPAY_APPSECRET", "").strip()


def _notify_url() -> str:
    return os.environ.get("XUNHUPAY_NOTIFY_URL", "").strip()


def _return_url() -> str:
    return os.environ.get("XUNHUPAY_RETURN_URL", "").strip()


def _gateway() -> str:
    return (
        os.environ.get("XUNHUPAY_GATEWAY", DEFAULT_GATEWAY).strip().rstrip("/")
        or DEFAULT_GATEWAY
    )


def _order_title() -> str:
    return os.environ.get("XUNHUPAY_ORDER_TITLE", "词汇学习订阅").strip() or "词汇学习订阅"


def order_ttl_minutes() -> int:
    return _read_int_env("XUNHUPAY_ORDER_TTL_MINUTES", DEFAULT_ORDER_TTL_MINUTES)


def is_configured() -> bool:
    """True only when the gateway can actually place orders.

    未配置时支付模块进入明确报错的未启用态（下单 503 payment_not_
    configured），其余功能不受影响。
    """

    return bool(_appid() and _appsecret() and _notify_url())


# ---------------------------------------------------------------------------
# Signature (虎皮椒 hash algorithm)
# ---------------------------------------------------------------------------


def _sign(params: dict[str, str]) -> str:
    items = sorted(
        (key, value)
        for key, value in params.items()
        if key != "hash" and value not in (None, "")
    )
    string_a = "&".join(f"{key}={value}" for key, value in items)
    return hashlib.md5(f"{string_a}{_appsecret()}".encode("utf-8")).hexdigest()


def verify_signature(params: dict[str, str]) -> bool:
    """Verify a gateway payload's hash (回调验签).

    Unknown extra fields participate in the signature (虎皮椒 may add
    fields), the ``hash`` field itself never does. Missing/empty hash →
    fail closed.
    """

    supplied = (params.get("hash") or "").strip().lower()
    if not supplied:
        return False
    return hmac.compare_digest(_sign(params), supplied)


# ---------------------------------------------------------------------------
# Gateway HTTP calls
# ---------------------------------------------------------------------------


def _post_gateway(path: str, params: dict[str, str]) -> dict[str, str]:
    body = url_parse.urlencode(params).encode("utf-8")
    request = url_request.Request(
        f"{_gateway()}{path}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with url_request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        text = response.read().decode("utf-8")
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as error:
        raise PaymentError(
            GATEWAY_ERROR, f"支付网关返回了无法解析的响应：{text[:200]}", status_code=502
        ) from error
    if not isinstance(parsed, dict):
        raise PaymentError(
            GATEWAY_ERROR, f"支付网关返回了意外的响应结构：{text[:200]}", status_code=502
        )
    return {str(key): str(value) for key, value in parsed.items()}


def gateway_create_payment(out_trade_no: str, total_fee_yuan: str) -> dict[str, str]:
    """POST /payment/do.html — returns at least url / url_qrcode."""

    params: dict[str, str] = {
        "version": "1.1",
        "appid": _appid(),
        "trade_order_id": out_trade_no,
        "total_fee": total_fee_yuan,
        "title": _order_title(),
        "time": str(int(time.time())),
        "notify_url": _notify_url(),
        "nonce_str": uuid.uuid4().hex,
    }
    if _return_url():
        params["return_url"] = _return_url()
    params["hash"] = _sign(params)
    result = _post_gateway("/payment/do.html", params)
    errcode = result.get("errcode", "")
    if errcode not in ("", "0"):
        raise PaymentError(
            GATEWAY_ERROR,
            f"支付网关下单失败（{errcode}）：{result.get('errmsg', '')}",
            status_code=502,
        )
    if not result.get("url") and not result.get("url_qrcode"):
        raise PaymentError(
            GATEWAY_ERROR, "支付网关未返回支付链接", status_code=502
        )
    return result


def gateway_query_order(out_trade_no: str) -> dict[str, str] | None:
    """POST /payment/query.html — returns the gateway order status, or
    None when the gateway no longer knows the order (查询无结果)."""

    params: dict[str, str] = {
        "appid": _appid(),
        "out_trade_order": out_trade_no,
        "time": str(int(time.time())),
        "nonce_str": uuid.uuid4().hex,
    }
    params["hash"] = _sign(params)
    result = _post_gateway("/payment/query.html", params)
    errcode = result.get("errcode", "")
    if errcode not in ("", "0"):
        logger.warning("xunhupay query failed for %s: %s", out_trade_no, result)
        return None
    return result


# ---------------------------------------------------------------------------
# Order model helpers
# ---------------------------------------------------------------------------


def _yuan_from_cents(amount_cents: int) -> str:
    return f"{amount_cents / 100:.2f}"


def _order_to_view(row) -> dict[str, object]:
    created = datetime.fromisoformat(str(row["created_at"]))
    return {
        "outTradeNo": str(row["out_trade_no"]),
        "plan": str(row["plan"]),
        "amountCents": int(row["amount_cents"]),
        "currency": str(row["currency"]),
        "status": str(row["status"]),
        "channel": str(row["channel"]),
        "payUrl": row["pay_url"],
        "payQrUrl": row["pay_qr_url"],
        "createdAt": str(row["created_at"]),
        "paidAt": row["paid_at"],
        # 收银台倒计时基准：下单时刻 + TTL（超时自动关单）。
        "expiresAt": _iso(created + timedelta(minutes=order_ttl_minutes())),
    }


def get_latest_order(user: dict[str, object]) -> dict[str, object]:
    """``GET /api/subscription/orders/latest`` — newest order + view.

    Pending orders get a reconcile attempt first (回调丢失补单 via the
    query API — V3-03 验收 4), so a user parked on the checkout page
    can recover even when the notify never arrived.
    """

    from app import subscription as subscription_module
    from app.db import connect

    user_id = str(user["id"])
    if not bool(user["is_super"]):
        try:
            reconcile_user_pending_orders(user_id)
        except Exception:  # noqa: BLE001 — 补单失败不能拖垮读路径
            pass
    with connect() as connection:
        row = connection.execute(
            "select * from orders where user_id = ?"
            " order by created_at desc, id desc limit 1",
            (user_id,),
        ).fetchone()
    order_view = _order_to_view(row) if row is not None else None
    return {
        "order": order_view,
        "subscription": subscription_module.get_subscription_view(user),
    }


def cancel_order(user: dict[str, object], out_trade_no: str) -> dict[str, object]:
    """收银台「取消支付」(订单级语义, V3-02 附则允许的仅存「取消」之一)."""

    from app.db import connect

    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "select * from orders where out_trade_no = ?", (out_trade_no,)
        ).fetchone()
        if row is None or str(row["user_id"]) != str(user["id"]):
            raise PaymentError(ORDER_NOT_FOUND, "订单不存在", status_code=404)
        if str(row["status"]) != "pending":
            raise PaymentError(
                ORDER_NOT_CANCELLABLE, "当前订单状态不可取消", status_code=409
            )
        connection.execute(
            "update orders set status = 'closed', updated_at = ?"
            " where out_trade_no = ?",
            (_iso(_now()), out_trade_no),
        )
        row = connection.execute(
            "select * from orders where out_trade_no = ?", (out_trade_no,)
        ).fetchone()
    return _order_to_view(row)


# ---------------------------------------------------------------------------
# 下单
# ---------------------------------------------------------------------------


def create_order(user: dict[str, object], plan: str) -> dict[str, object]:
    """``POST /api/subscription/orders`` — snapshot the amount, place it.

    The backend computes 应收金额 from the user's subscription snapshot
    (V3-02 交互规则 4) and writes it into orders; the callback must
    match it exactly before anything is confirmed. Gateway failure →
    PaymentError and NO order row is written (不产生脏订单, V3-03 验收 5).
    """

    from app import subscription as subscription_module
    from app.db import connect

    if bool(user["is_super"]):
        raise PaymentError(
            SUPER_CONFLICT, "super 账号无需订阅", status_code=409
        )

    config = subscription_module.plans_config()
    if plan not in config:
        raise PaymentError(PLAN_NOT_FOUND, "未知的订阅档位", status_code=400)
    amount_cents = int(config[plan]["priceCents"])
    currency = "CNY"

    if plan == subscription_module.PLAN_RENEW:
        with connect() as connection:
            eligible = subscription_module.is_renew_eligible(
                connection, str(user["id"]), _now()
            )
        if not eligible:
            raise PaymentError(
                RENEW_NOT_ELIGIBLE,
                "当前不满足续费优惠价条件，请按标价档购买",
                status_code=400,
            )

    if not is_configured():
        raise PaymentError(
            PAYMENT_NOT_CONFIGURED,
            "支付通道尚未开通：管理员还未配置支付网关密钥",
            status_code=503,
        )

    # 重复点击下单的幂等口径：TTL 内同档位的 pending 订单直接复用
    # （v2 已验证的幂等思路在 v3 支付场景的对应实现）。
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            select * from orders
            where user_id = ? and plan = ? and status = 'pending'
            order by created_at desc limit 1
            """,
            (str(user["id"]), plan),
        ).fetchone()
    if existing is not None:
        created = datetime.fromisoformat(str(existing["created_at"]))
        if _now() - created < timedelta(minutes=order_ttl_minutes()):
            return _order_to_view(existing)

    out_trade_no = f"VL{_now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:10]}"
    result = gateway_create_payment(out_trade_no, _yuan_from_cents(amount_cents))

    now_iso = _iso(_now())
    order_id = uuid.uuid4().hex
    with connect() as connection:
        connection.execute(
            """
            insert into orders (id, out_trade_no, user_id, plan, amount_cents,
                                currency, status, channel, pay_url, pay_qr_url,
                                created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, 'pending', 'xunhupay', ?, ?, ?, ?)
            """,
            (
                order_id,
                out_trade_no,
                str(user["id"]),
                plan,
                amount_cents,
                currency,
                result.get("url"),
                result.get("url_qrcode"),
                now_iso,
                now_iso,
            ),
        )
        row = connection.execute(
            "select * from orders where out_trade_no = ?", (out_trade_no,)
        ).fetchone()
    logger.info(
        "order %s created for user %s plan=%s amount=%d cents",
        out_trade_no,
        user["id"],
        plan,
        amount_cents,
    )
    return _order_to_view(row)


# ---------------------------------------------------------------------------
# 支付确认（回调 / 对账共用）
# ---------------------------------------------------------------------------


def confirm_payment(
    out_trade_no: str,
    *,
    total_fee_yuan: str,
    transaction_id: str | None = None,
    open_order_id: str | None = None,
) -> str:
    """Confirm one order as paid (idempotent).

    Returns one of: ``confirmed`` / ``already_paid`` / ``unknown_order`` /
    ``not_pending`` / ``amount_mismatch``. Only ``confirmed`` and
    ``already_paid`` (幂等重放) may ever answer success to the gateway —
    and neither writes twice: the pending → paid transition + the
    subscription row both live inside one BEGIN IMMEDIATE transaction.

    金额不符不确认入账 (V3-02 验收 6): the gateway reports total_fee in
    元; it must round-trip to the snapshotted amount_cents exactly.
    """

    from app import subscription as subscription_module
    from app.db import connect

    try:
        reported_cents = int(round(float(total_fee_yuan) * 100))
    except (TypeError, ValueError):
        return "amount_mismatch"

    with connect() as connection:
        # BEGIN IMMEDIATE: the read → status-check → confirm sequence must
        # not race a concurrent notification (同一模式 as v2 mock order).
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "select * from orders where out_trade_no = ?", (out_trade_no,)
        ).fetchone()
        if row is None:
            return "unknown_order"
        if str(row["status"]) == "paid":
            return "already_paid"
        if str(row["status"]) != "pending":
            return "not_pending"
        if reported_cents != int(row["amount_cents"]):
            logger.error(
                "order %s amount mismatch: callback %s yuan (%d cents) vs"
                " snapshot %d cents — NOT confirmed",
                out_trade_no,
                total_fee_yuan,
                reported_cents,
                int(row["amount_cents"]),
            )
            return "amount_mismatch"

        now_iso = _iso(_now())
        connection.execute(
            """
            update orders
            set status = 'paid', paid_at = ?, updated_at = ?,
                transaction_id = ?
            where out_trade_no = ?
            """,
            (now_iso, now_iso, transaction_id, out_trade_no),
        )
        # 虎皮椒聚合通道：回调报文不含支付渠道字段，收款主体为支付宝
        # 个人余额，故 paid 行 source 记 'alipay'（wechat 预留，见 V3-08）。
        subscription_module.activate_subscription(
            connection,
            user_id=str(row["user_id"]),
            plan=str(row["plan"]),
            amount_cents=int(row["amount_cents"]),
            source="alipay",
            order_no=out_trade_no,
            now=_now(),
        )
    logger.info(
        "order %s confirmed paid (%s yuan), subscription activated",
        out_trade_no,
        total_fee_yuan,
    )
    return "confirmed"


def handle_notify(form: dict[str, str]) -> tuple[str, int]:
    """``POST /api/payment/notify`` — the gateway callback.

    Every payload is archived verbatim in payment_callbacks (原始报文
    留档). Answers plain text ``success`` only when the payload is fully
    processed or idempotently replayed; anything else answers ``fail``
    so the gateway retries (it gives up after 6 attempts).
    """

    from app.db import connect

    out_trade_no = form.get("trade_order_id", "")
    status = form.get("status", "")

    def _archive(result: str) -> None:
        with connect() as connection:
            connection.execute(
                """
                insert into payment_callbacks (id, out_trade_no, payload_json,
                                               result, created_at)
                values (?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    out_trade_no or None,
                    json.dumps(dict(form), ensure_ascii=False, sort_keys=True),
                    result,
                    _iso(_now()),
                ),
            )

    if not verify_signature(form):
        _archive("rejected_bad_signature")
        logger.warning("payment notify rejected: bad signature (%s)", out_trade_no)
        return "fail", 200

    if status != "OD":
        # CD 已退款 / RD 退款中 / UD 退款失败 — 留档即可，不需要重试。
        _archive(f"ignored_status_{status or 'missing'}")
        return "success", 200

    result = confirm_payment(
        out_trade_no,
        total_fee_yuan=form.get("total_fee", ""),
        transaction_id=form.get("transaction_id"),
        open_order_id=form.get("open_order_id"),
    )
    if result in ("confirmed", "already_paid"):
        # 幂等：重复通知不重复续期，直接 success 止住重试。
        _archive(result)
        return "success", 200
    _archive(f"rejected_{result}")
    logger.warning("payment notify rejected: %s (%s)", result, out_trade_no)
    return "fail", 200


# ---------------------------------------------------------------------------
# 对账 (V3-03 验收 4)
# ---------------------------------------------------------------------------


def _close_overdue_pending(connection) -> int:
    """收银台超时自动关单：pending 且创建已超 TTL 的订单置 closed."""

    cutoff = _iso(_now() - timedelta(minutes=order_ttl_minutes()))
    cursor = connection.execute(
        "update orders set status = 'closed', updated_at = ?"
        " where status = 'pending' and created_at < ?",
        (_iso(_now()), cutoff),
    )
    return cursor.rowcount


def reconcile_pending_orders(user_id: str | None = None, *, limit: int = 50) -> dict[str, object]:
    """Query the gateway for pending orders and reconcile them.

    - 漏单自动补单：gateway says OD → confirm (amount-checked, idempotent);
    - 超时关单：pending 且超 TTL → closed;
    - 通道未配置 → skipped (no-op). Gateway/网络故障不会抛出（记录后跳过）
      — 对账是兜底，不能反过来拖垮业务路径。
    """

    if not is_configured():
        return {"skipped": True, "confirmed": 0, "closed": 0, "checked": 0}

    from app.db import connect

    # 先做超时关单（含全量与按用户两种调用形态）。
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        closed = _close_overdue_pending(connection)

    with connect() as connection:
        if user_id is None:
            rows = connection.execute(
                "select * from orders where status = 'pending'"
                " order by created_at desc limit ?",
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                "select * from orders where status = 'pending' and user_id = ?"
                " order by created_at desc limit ?",
                (user_id, limit),
            ).fetchall()

    confirmed = 0
    checked = 0
    for row in rows:
        checked += 1
        try:
            result = gateway_query_order(str(row["out_trade_no"]))
        except Exception:  # noqa: BLE001 — 单笔查询失败跳过该笔
            logger.warning(
                "reconcile: gateway query failed for %s",
                row["out_trade_no"],
                exc_info=True,
            )
            continue
        if result is None:
            continue
        # 查询结果同样验签（若网关返回 hash）。
        if result.get("hash") and not verify_signature(result):
            logger.warning(
                "reconcile: query response signature mismatch for %s",
                row["out_trade_no"],
            )
            continue
        if result.get("status") == "OD":
            outcome = confirm_payment(
                str(row["out_trade_no"]),
                total_fee_yuan=result.get("total_fee", ""),
                transaction_id=result.get("transaction_id"),
                open_order_id=result.get("open_order_id"),
            )
            if outcome in ("confirmed", "already_paid"):
                confirmed += 1
            elif outcome == "amount_mismatch":
                logger.error(
                    "reconcile: amount mismatch for %s — order NOT confirmed",
                    row["out_trade_no"],
                )
    return {"skipped": False, "confirmed": confirmed, "closed": closed, "checked": checked}


def reconcile_user_pending_orders(user_id: str) -> dict[str, object]:
    """Reconciliation scoped to one user (called from /me — 兜底补单)."""

    if not is_configured():
        return {"skipped": True, "confirmed": 0, "closed": 0, "checked": 0}
    return reconcile_pending_orders(user_id)
