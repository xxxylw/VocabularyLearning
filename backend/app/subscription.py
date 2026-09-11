"""Subscription service (v3 commercial edition, P0).

One ``subscriptions`` row == one subscription period. The read path
answers "is this user entitled" from the *latest* row's ``status`` +
``expires_at`` only, so the payment source stays decoupled from the
read side — v3 only changed the write side (trial rows at register,
paid rows from the official payment callback via app.payment (微信支付
Native / 支付宝官方收银台, 2026-09-06 拍板替代虎皮椒聚合通道).

v3 decisions (spec 2026-09-06, all three 拍板 confirmed):
- registration writes a 7-day trial row **inside the register
  transaction** (V3-01: source=trial, plan=trial_7d, status=trialing,
  price_cents=0, expires_at=注册+7d UTC; super never gets one);
- entitlement = latest row status in (trialing, active) and not past
  expires_at (lazily expired on read);
- four price tiers, all configuration-driven (V3-02):
  月付 5 元 / 续费优惠 2.99 元 (仅 30 天档, 有效期内或到期 7 天宽限内
  手动续费, 逾期回标价) / 半年 21 元 (原价 30 元, 7 折, 180 天) /
  一年 30 元 (原价 60 元, 5 折, 360 天);
- renewal accumulation: paid-and-unexpired → new expires_at = 原
  expires_at + 所购档时长; otherwise 自支付成功时刻起算 (trial 不结转);
- 手动续费模式：不存在「取消订阅」语义 — mock 下单/取消都降级为
  super 专属测试桩 (V3-08), 普通用户一律 410;
- no emails anywhere in the subscription flow (v2 拍板 unchanged).

Timezone rule (V3-01): all judgment in UTC; display layer localizes.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

MOCK_ORDER_PERIOD_DAYS = 30
DEFAULT_CURRENCY = "CNY"

# v3 tier ids (stable contract with the frontend price cards and the
# orders table). ``renew`` is the 2.99 manual-renewal tier — purchasable
# only while the backend judges the user eligible (V3-02: 后端判定优惠
# 资格，不信任前端).
PLAN_MONTHLY = "monthly"
PLAN_RENEW = "renew"
PLAN_HALFYEAR = "halfyear"
PLAN_YEARLY = "yearly"

TRIAL_PLAN_ID = "trial_7d"

# sources that count as a *paid* subscription for renewal-eligibility /
# accumulation purposes. trial rows never do (V3-02 边界态: 试用行不算
# 订阅行 — 试用用户首购不享 2.99).
PAID_SOURCES = ("alipay", "wechat", "mock")

SUPER_CONFLICT = "super_account"
NO_ACTIVE_SUBSCRIPTION = "no_active_subscription"
SUBSCRIPTION_EXPIRED = "subscription_expired"
MOCK_DISABLED = "mock_disabled"

RENEW_REMINDER_KEY = "subscription_renew_reminder"


class SubscriptionError(Exception):
    """Domain error carrying an HTTP-ready code."""

    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
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


def _currency() -> str:
    return (
        os.environ.get("VOCAB_SUB_CURRENCY", DEFAULT_CURRENCY).strip()
        or DEFAULT_CURRENCY
    )


# ---------------------------------------------------------------------------
# Configuration (V3-02: 改价不发版 — all four tiers + durations + trial
# and grace windows live in environment configuration)
# ---------------------------------------------------------------------------


def trial_days() -> int:
    return _read_int_env("VOCAB_TRIAL_DAYS", 7)


def renew_grace_days() -> int:
    return _read_int_env("VOCAB_RENEW_GRACE_DAYS", 7)


def plans_config() -> dict[str, dict[str, object]]:
    """The four purchasable tiers, fully driven by configuration."""

    return {
        PLAN_MONTHLY: {
            "plan": PLAN_MONTHLY,
            "label": "单月",
            "priceCents": _read_int_env("VOCAB_PRICE_MONTHLY_CENTS", 500),
            "durationDays": _read_int_env("VOCAB_DURATION_MONTHLY_DAYS", 30),
        },
        PLAN_RENEW: {
            "plan": PLAN_RENEW,
            "label": "续费优惠",
            "priceCents": _read_int_env("VOCAB_PRICE_RENEW_CENTS", 299),
            "durationDays": _read_int_env("VOCAB_DURATION_RENEW_DAYS", 30),
        },
        PLAN_HALFYEAR: {
            "plan": PLAN_HALFYEAR,
            "label": "半年",
            "priceCents": _read_int_env("VOCAB_PRICE_HALFYEAR_CENTS", 2100),
            "durationDays": _read_int_env("VOCAB_DURATION_HALFYEAR_DAYS", 180),
        },
        PLAN_YEARLY: {
            "plan": PLAN_YEARLY,
            "label": "一年",
            "priceCents": _read_int_env("VOCAB_PRICE_YEARLY_CENTS", 3000),
            "durationDays": _read_int_env("VOCAB_DURATION_YEARLY_DAYS", 360),
        },
    }


def plan_duration_days(plan_id: str) -> int:
    config = plans_config().get(plan_id)
    if config is None:
        raise KeyError(plan_id)
    return int(config["durationDays"])


def get_plans(user: dict[str, object] | None = None) -> dict[str, object]:
    """``GET /api/subscription/plans`` — the four tiers + eligibility.

    The renew tier is annotated server-side (``renewEligible``) so the
    frontend can hide/disable it for ineligible users, but the backend
    re-checks at 下单 anyway (V3-02: 后端判定，不信任前端).
    """

    from app import payment

    tiers = list(plans_config().values())
    for tier in tiers:
        tier["currency"] = _currency()
    renew_eligible = False
    if user is not None and not bool(user.get("is_super")):
        from app.db import connect

        with connect() as connection:
            renew_eligible = is_renew_eligible(connection, str(user["id"]), _now())
    return {
        "plans": tiers,
        "currency": _currency(),
        "trialDays": trial_days(),
        "renewGraceDays": renew_grace_days(),
        "renewEligible": renew_eligible,
        "paymentEnabled": payment.is_configured(),
        "channels": payment.channels_configured(),
    }


# ---------------------------------------------------------------------------
# Trial (V3-01)
# ---------------------------------------------------------------------------


def insert_trial_row(connection, user_id: str, now_iso: str | None = None) -> None:
    """Write the 7-day trial row **on the caller's open transaction**.

    Called from auth.create_user inside the register transaction so a
    failure rolls the whole registration back (V3-01 边界态: 不允许
    出现「有账号无试用行」的中间态). Never called for super accounts.
    """

    moment = _now()
    now_iso = now_iso or _iso(moment)
    expires_iso = _iso(moment + timedelta(days=trial_days()))
    connection.execute(
        """
        insert into subscriptions (id, user_id, plan, status, price_cents,
                                   currency, source, started_at, expires_at,
                                   auto_renew, created_at, updated_at)
        values (?, ?, ?, 'trialing', 0, ?, 'trial', ?, ?, 0, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            user_id,
            TRIAL_PLAN_ID,
            _currency(),
            now_iso,
            expires_iso,
            now_iso,
            now_iso,
        ),
    )


# ---------------------------------------------------------------------------
# Latest-row read path (unchanged 口径: only the latest row answers)
# ---------------------------------------------------------------------------


def _latest_row(connection, user_id: str):
    return connection.execute(
        """
        select * from subscriptions where user_id = ?
        order by started_at desc, created_at desc limit 1
        """,
        (user_id,),
    ).fetchone()


def _lazy_expire(connection, row) -> None:
    """Flip an overdue active/trialing row to ``expired`` (read-path).

    v3: trialing rows expire exactly like active ones — 到期降级只读
    (V3-01 产品定调 3).
    """

    if row is None:
        return
    if row["status"] in ("active", "trialing") and str(row["expires_at"]) <= _iso(_now()):
        connection.execute(
            "update subscriptions set status = 'expired', updated_at = ? where id = ?",
            (_iso(_now()), row["id"]),
        )


def _refresh(connection, row):
    if row is None:
        return None
    return connection.execute(
        "select * from subscriptions where id = ?", (row["id"],)
    ).fetchone()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _has_active_paid_subscription(row) -> bool:
    """已充值灰态判定 (2026-09-11 拍板 DP-1).

    「充值过」= 最新行 source ∈ (alipay, wechat, mock) 且 status=active
    且未到期，服务端 UTC 判定；trialing / expired / canceled / trial
    行一律不算。mock 行计入（super 回归测试手段）。到期恢复可点由
    读路径的 _lazy_expire + 本函数的 expires_at 双保险共同保证。
    """

    if row is None:
        return False
    if str(row["source"]) not in PAID_SOURCES:
        return False
    if str(row["status"]) != "active":
        return False
    try:
        return _parse_iso(str(row["expires_at"])) > _now()
    except (ValueError, TypeError):
        return False


def is_renew_eligible(connection, user_id: str, now: datetime | None = None) -> bool:
    """Backend 判定 2.99 续费优惠资格 (V3-02 交互规则 3).

    基准 = 当前最新订阅行的 expires_at + 7 天宽限；试用行不算订阅行
    （试用用户首购不享 2.99）。canceled 行（v2 mock 清退遗留）不作为
    宽限期基准 — 只按标价购买。
    """

    now = now or _now()
    row = _latest_row(connection, user_id)
    _lazy_expire(connection, row)
    row = _refresh(connection, row)
    if row is None:
        return False
    if str(row["source"]) == "trial":
        return False
    if str(row["status"]) not in ("active", "expired"):
        return False
    grace_end = _parse_iso(str(row["expires_at"])) + timedelta(days=renew_grace_days())
    return now <= grace_end


def get_renew_reminder(connection, user_id: str) -> bool:
    """续费提醒开关 (V3-02 附则: 默认开, 只控制提醒不影响权益)."""

    row = connection.execute(
        "select value from user_settings where user_id = ? and key = ?",
        (user_id, RENEW_REMINDER_KEY),
    ).fetchone()
    if row is None:
        return True
    return row["value"] != "0"


def _row_to_view(
    row, *, renew_eligible: bool = False, reminder: bool = True
) -> dict[str, object] | None:
    if row is None:
        return None
    status = str(row["status"])
    subscribed = status in ("active", "trialing")
    view: dict[str, object] = {
        "subscribed": subscribed,
        "plan": str(row["plan"]),
        "status": status,
        "startedAt": str(row["started_at"]),
        "expiresAt": str(row["expires_at"]),
        "autoRenew": bool(row["auto_renew"]),
        "source": str(row["source"]),
        "trialDaysLeft": None,
        "readOnly": not subscribed,
        "renewEligible": renew_eligible,
        "renewDeadline": None,
        "renewReminder": reminder,
        "hasActivePaidSubscription": _has_active_paid_subscription(row),
    }
    if status == "trialing":
        remaining = _parse_iso(str(row["expires_at"])) - _now()
        # 向上取整：刚注册的 7 天试用（差几秒不足整 7 天）应显示 7 而非 6。
        import math

        view["trialDaysLeft"] = max(0, math.ceil(remaining.total_seconds() / 86400))
    if renew_eligible:
        deadline = _parse_iso(str(row["expires_at"])) + timedelta(days=renew_grace_days())
        view["renewDeadline"] = _iso(deadline)
    return view


_EMPTY_VIEW: dict[str, object] = {
    "subscribed": False,
    "plan": None,
    "status": None,
    "startedAt": None,
    "expiresAt": None,
    "autoRenew": None,
    "source": None,
    "trialDaysLeft": None,
    "readOnly": True,
    "renewEligible": False,
    "renewDeadline": None,
    "renewReminder": True,
    "hasActivePaidSubscription": False,
}


def get_subscription_view(user: dict[str, object]) -> dict[str, object]:
    """``GET /api/subscription/me`` — the user's current entitlement.

    super accounts get a synthesized permanent view and never touch the
    subscriptions table. For everyone else: (1) when not entitled, the
    payment module first reconciles the user's pending orders against
    the gateway (V3-03 验收 4: 回调丢失时 me 可主动查询补单), then
    (2) the latest row (lazily expired) answers.
    """

    if bool(user["is_super"]):
        # super 合成视图：无订阅行，灰态判定恒 False（按钮保持可点态，
        # 2026-09-11 拍板第 6 条），入口常驻可进充值界面。
        return {
            "subscribed": True,
            "plan": "super",
            "status": "active",
            "startedAt": None,
            "expiresAt": None,
            "autoRenew": None,
            "source": None,
            "trialDaysLeft": None,
            "readOnly": False,
            "renewEligible": False,
            "renewDeadline": None,
            "renewReminder": None,
            "hasActivePaidSubscription": False,
        }

    user_id = str(user["id"])

    # 回调丢失兜底: pending 订单先对账（网关未配置时是 no-op），再读行。
    from app import payment

    try:
        payment.reconcile_user_pending_orders(user_id)
    except Exception:  # noqa: BLE001 — 补单失败不能拖垮 /me 读路径
        pass

    from app.db import connect

    with connect() as connection:
        row = _latest_row(connection, user_id)
        _lazy_expire(connection, row)
        row = _refresh(connection, row)
        if row is None:
            return _EMPTY_VIEW
        renew_eligible = is_renew_eligible(connection, user_id, _now())
        reminder = get_renew_reminder(connection, user_id)
        view = _row_to_view(row, renew_eligible=renew_eligible, reminder=reminder)
    return view if view is not None else _EMPTY_VIEW


# ---------------------------------------------------------------------------
# Entitlement gate (V3-01: 只读模式 — 学习动作后端拦截)
# ---------------------------------------------------------------------------


def require_study_entitlement(user: dict[str, object]) -> None:
    """Raise 403 SubscriptionError when the user is not entitled.

    Applied to the learning-action endpoints (学新 / 复习 / 拼写背后的
    卡片写入). Read-only endpoints (书架 / 进度 / 统计) never call this.
    """

    if bool(user.get("is_super")):
        return
    view = get_subscription_view(user)
    if not bool(view["subscribed"]):
        raise SubscriptionError(
            SUBSCRIPTION_EXPIRED,
            "订阅已到期，学习功能已锁定；续费后立即恢复",
            status_code=403,
        )


# ---------------------------------------------------------------------------
# Paid-subscription activation (called by app.payment on confirm)
# ---------------------------------------------------------------------------


def activate_subscription(
    connection,
    *,
    user_id: str,
    plan: str,
    amount_cents: int,
    source: str,
    order_no: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Insert the paid row on the caller's open BEGIN IMMEDIATE transaction.

    累加规则 (V3-02 交互规则 2): 当前订阅未到期 → 新 expires_at = 原
    expires_at + 所购档时长；已到期或试用行 → 自支付成功时刻起算（试用
    剩余天数不结转，V3-01 交互规则 2）。
    """

    now = now or _now()
    now_iso = _iso(now)
    row = _latest_row(connection, user_id)
    _lazy_expire(connection, row)
    row = _refresh(connection, row)
    base = now
    if (
        row is not None
        and str(row["status"]) == "active"
        and str(row["source"]) in PAID_SOURCES
        and _parse_iso(str(row["expires_at"])) > now
    ):
        base = _parse_iso(str(row["expires_at"]))
    expires_iso = _iso(base + timedelta(days=plan_duration_days(plan)))
    subscription_id = uuid.uuid4().hex
    connection.execute(
        """
        insert into subscriptions (id, user_id, plan, status, price_cents,
                                   currency, source, started_at, expires_at,
                                   auto_renew, order_no, created_at, updated_at)
        values (?, ?, ?, 'active', ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            subscription_id,
            user_id,
            plan,
            amount_cents,
            _currency(),
            source,
            now_iso,
            expires_iso,
            order_no,
            now_iso,
            now_iso,
        ),
    )
    view = _row_to_view(
        connection.execute(
            "select * from subscriptions where id = ?", (subscription_id,)
        ).fetchone()
    )
    assert view is not None
    return view


# ---------------------------------------------------------------------------
# super-only mock stubs (V3-08: mock-order/cancel 对普通用户 410)
# ---------------------------------------------------------------------------


def create_mock_order(user: dict[str, object]) -> dict[str, object]:
    """``POST /api/subscription/mock-order`` — super-only test stub.

    v2 activated a 30-day mock subscription for anyone; v3 closes it for
    regular users (410) and keeps it as the super regression stub so
    subscription logic can still be exercised without a real gateway.
    """

    if not bool(user["is_super"]):
        raise SubscriptionError(
            MOCK_DISABLED, "模拟订阅已下线，请使用真实支付", status_code=410
        )

    from app.db import connect

    now = _now()
    now_iso = _iso(now)
    expires_iso = _iso(now + timedelta(days=MOCK_ORDER_PERIOD_DAYS))
    with connect() as connection:
        # BEGIN IMMEDIATE: same concurrency pattern as v2 (QA batch-3).
        connection.execute("BEGIN IMMEDIATE")
        row = _latest_row(connection, str(user["id"]))
        _lazy_expire(connection, row)
        row = _refresh(connection, row)
        if row is not None and row["status"] == "active":
            view = _row_to_view(row)
            assert view is not None
            return view

        connection.execute(
            """
            insert into subscriptions (id, user_id, plan, status, price_cents,
                                       currency, source, started_at, expires_at,
                                       auto_renew, remark, created_at, updated_at)
            values (?, ?, ?, 'active', 0, ?, 'mock', ?, ?, 0, 'super 测试桩', ?, ?)
            """,
            (
                uuid.uuid4().hex,
                str(user["id"]),
                PLAN_MONTHLY,
                _currency(),
                now_iso,
                expires_iso,
                now_iso,
                now_iso,
            ),
        )
    return {
        "subscribed": True,
        "plan": PLAN_MONTHLY,
        "status": "active",
        "startedAt": now_iso,
        "expiresAt": expires_iso,
        "autoRenew": False,
        "source": "mock",
        "trialDaysLeft": None,
        "readOnly": False,
        "renewEligible": False,
        "renewDeadline": None,
        "renewReminder": None,
    }


def cancel_subscription(user: dict[str, object]) -> dict[str, object]:
    """``POST /api/subscription/cancel`` — super-only mock stub.

    v3 移除「取消订阅」语义 (V3-02 附则: 手动续费模式下没有可取消的
    扣款授权)。The endpoint stays as a super test stub only; regular
    users get 410 so no client can ever 取消 a paid subscription.
    """

    if not bool(user["is_super"]):
        raise SubscriptionError(
            MOCK_DISABLED, "订阅不支持取消；已支付的有效期将履约到期", status_code=410
        )

    from app.db import connect

    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _latest_row(connection, str(user["id"]))
        _lazy_expire(connection, row)
        row = _refresh(connection, row)
        if row is None or row["status"] != "active":
            raise SubscriptionError(
                NO_ACTIVE_SUBSCRIPTION, "当前没有生效中的订阅", status_code=409
            )
        connection.execute(
            """
            update subscriptions
            set status = 'canceled', auto_renew = 0, updated_at = ?
            where id = ?
            """,
            (_iso(_now()), row["id"]),
        )
        updated = _refresh(connection, row)
    view = _row_to_view(updated)
    assert view is not None
    return view


def set_renew_reminder(user: dict[str, object], enabled: bool) -> dict[str, object]:
    """``PUT /api/subscription/reminder`` — 续费提醒开关 (默认开)."""

    from app.db import connect

    with connect() as connection:
        connection.execute(
            "insert or replace into user_settings (user_id, key, value)"
            " values (?, ?, ?)",
            (str(user["id"]), RENEW_REMINDER_KEY, "1" if enabled else "0"),
        )
    return get_subscription_view(user)
