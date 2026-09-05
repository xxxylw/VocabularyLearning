"""Mock subscription service (v2 cloud edition, batch 3 / C-09).

One ``subscriptions`` row == one subscription period. The read path
answers "is this user subscribed" from the *latest* row's
``status`` + ``expires_at`` only, so the payment source stays
decoupled: swapping in a real payment channel later only changes the
write side (``source`` = wechat/alipay), never the read side or the UI.

Decisions (batch-3 spec, PM 交付评论):
- price lives in configuration (``VOCAB_SUB_PRICE_CENTS`` /
  ``VOCAB_SUB_CURRENCY``), currently 0.1 CNY/month — moving to 4.99
  is a config change, zero code;
- ``POST mock-order`` activates immediately for 30 days (UTC); while
  a subscription is active a repeat order is idempotent — it returns
  the current row and writes nothing;
- expiry is judged lazily on the read path (``GET me`` flips an
  overdue active row to ``expired``);
- ``POST cancel`` is the mock cancel: the latest active row is marked
  ``canceled`` (auto_renew=0) and the user is no longer subscribed —
  re-ordering afterwards creates a fresh row;
- super accounts never get subscription rows: ``/me`` synthesizes a
  permanent view (plan=super, expires_at=null) and ``mock-order``
  answers 409 so the privileged path never pollutes the data;
- no emails anywhere in the subscription flow (user decision
  2026-09-04: UI-only display).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

MOCK_ORDER_PERIOD_DAYS = 30
DEFAULT_PRICE_CENTS = 10  # 0.1 CNY
DEFAULT_CURRENCY = "CNY"
PLAN_ID = "monthly"

SUPER_CONFLICT = "super_account"
NO_ACTIVE_SUBSCRIPTION = "no_active_subscription"


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


def get_plan() -> dict[str, object]:
    """The purchasable plan, fully driven by configuration."""

    return {
        "plan": PLAN_ID,
        "priceCents": _read_int_env("VOCAB_SUB_PRICE_CENTS", DEFAULT_PRICE_CENTS),
        "currency": os.environ.get("VOCAB_SUB_CURRENCY", DEFAULT_CURRENCY).strip()
        or DEFAULT_CURRENCY,
        "period": "month",
    }


def _latest_row(connection, user_id: str):
    return connection.execute(
        """
        select * from subscriptions where user_id = ?
        order by started_at desc, created_at desc limit 1
        """,
        (user_id,),
    ).fetchone()


def _lazy_expire(connection, row) -> None:
    """Flip an overdue active row to ``expired`` (read-path judgment)."""

    if row is None:
        return
    if row["status"] == "active" and str(row["expires_at"]) <= _iso(_now()):
        connection.execute(
            "update subscriptions set status = 'expired', updated_at = ? where id = ?",
            (_iso(_now()), row["id"]),
        )


def _row_to_view(row) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "subscribed": row["status"] == "active",
        "plan": str(row["plan"]),
        "status": str(row["status"]),
        "startedAt": str(row["started_at"]),
        "expiresAt": str(row["expires_at"]),
        "autoRenew": bool(row["auto_renew"]),
        "source": str(row["source"]),
    }


def _refresh(connection, row):
    if row is None:
        return None
    return connection.execute(
        "select * from subscriptions where id = ?", (row["id"],)
    ).fetchone()


def get_subscription_view(user: dict[str, object]) -> dict[str, object]:
    """``GET /api/subscription/me`` — the user's current subscription.

    super accounts get a synthesized permanent view and never touch the
    subscriptions table.
    """

    if bool(user["is_super"]):
        return {
            "subscribed": True,
            "plan": "super",
            "status": "active",
            "startedAt": None,
            "expiresAt": None,
            "autoRenew": None,
            "source": None,
        }

    from app.db import connect

    with connect() as connection:
        row = _latest_row(connection, str(user["id"]))
        _lazy_expire(connection, row)
        row = _refresh(connection, row)
    view = _row_to_view(row)
    if view is None:
        return {
            "subscribed": False,
            "plan": None,
            "status": None,
            "startedAt": None,
            "expiresAt": None,
            "autoRenew": None,
            "source": None,
        }
    return view


def create_mock_order(user: dict[str, object]) -> dict[str, object]:
    """``POST /api/subscription/mock-order`` — 30 days, effective now.

    Idempotent while active: a repeat order returns the current row
    without writing. super answers 409 (and never writes a row).
    """

    if bool(user["is_super"]):
        raise SubscriptionError(
            SUPER_CONFLICT, "super 账号无需订阅", status_code=409
        )

    from app.db import connect

    now = _now()
    now_iso = _iso(now)
    expires_iso = _iso(now + timedelta(days=MOCK_ORDER_PERIOD_DAYS))
    with connect() as connection:
        # BEGIN IMMEDIATE acquires the sqlite write lock up front so the
        # read → active-check → INSERT sequence below cannot race a
        # concurrent mock order for the same user (QA batch-3 finding;
        # same pattern as auth.consume_email_code / QA C-01a): without
        # it two threads can both read "no active row" and both INSERT,
        # leaving duplicate active subscriptions.
        connection.execute("BEGIN IMMEDIATE")
        row = _latest_row(connection, str(user["id"]))
        _lazy_expire(connection, row)
        row = _refresh(connection, row)
        if row is not None and row["status"] == "active":
            # 有效期内重复下单：幂等返回当前订阅，不新建行。
            view = _row_to_view(row)
            assert view is not None
            return view

        plan = get_plan()
        connection.execute(
            """
            insert into subscriptions (id, user_id, plan, status, price_cents,
                                       currency, source, started_at, expires_at,
                                       auto_renew, created_at, updated_at)
            values (?, ?, ?, 'active', ?, ?, 'mock', ?, ?, 0, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                str(user["id"]),
                str(plan["plan"]),
                int(plan["priceCents"]),
                str(plan["currency"]),
                now_iso,
                expires_iso,
                now_iso,
                now_iso,
            ),
        )
    return {
        "subscribed": True,
        "plan": str(plan["plan"]),
        "status": "active",
        "startedAt": now_iso,
        "expiresAt": expires_iso,
        "autoRenew": False,
        "source": "mock",
    }


def cancel_subscription(user: dict[str, object]) -> dict[str, object]:
    """``POST /api/subscription/cancel`` — the mock cancel.

    Marks the latest active row ``canceled`` (auto_renew=0); the user is
    immediately no longer subscribed. Re-ordering later just creates a
    new row.
    """

    if bool(user["is_super"]):
        raise SubscriptionError(
            SUPER_CONFLICT, "super 账号没有可取消的订阅", status_code=409
        )

    from app.db import connect

    with connect() as connection:
        # BEGIN IMMEDIATE acquires the sqlite write lock up front so the
        # read → active-check → UPDATE sequence below cannot race a
        # concurrent cancel (QA batch-3 finding, P3; same pattern as
        # create_mock_order above).
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
