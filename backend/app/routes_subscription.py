"""Subscription & payment API routes (v3 commercial edition, P0).

All subscription endpoints sit behind the session guard; the xunhupay
callback ``POST /api/payment/notify`` is intentionally PUBLIC (the
gateway posts it server-to-server and cannot carry a session) and
authenticates via the payload hash signature instead (V3-03 验收 2:
签名校验失败一律拒绝).

- GET  /api/subscription/plans             — four tiers + eligibility
- GET  /api/subscription/me                 — lazy-expiring status read
- POST /api/subscription/orders             — 下单 (amount snapshotted)
- GET  /api/subscription/orders/latest      — checkout polling + 补单兜底
- POST /api/subscription/orders/{no}/cancel — 收银台「取消支付」(订单级)
- PUT  /api/subscription/reminder           — 续费提醒开关 (默认开)
- POST /api/subscription/mock-order          — super-only test stub (410)
- POST /api/subscription/cancel             — super-only test stub (410)
- POST /api/payment/notify                  — xunhupay callback (public)

No emails anywhere in this flow (v2 拍板 unchanged).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app import payment, subscription
from app.auth import AuthContext, require_user_strict
from app.models import (
    CreateOrderRequest,
    LatestOrderResponse,
    OrderResponse,
    RenewReminderRequest,
    SubscriptionPlansResponse,
    SubscriptionStatusResponse,
)
from app.payment import PaymentError
from app.subscription import SubscriptionError

router = APIRouter()


def _user_or_401(context: AuthContext) -> dict[str, object]:
    from app import auth

    user = auth.find_user_by_id(str(context.user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


def _subscription_http_error(error: SubscriptionError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    )


def _payment_http_error(error: PaymentError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    )


@router.get("/subscription/plans", response_model=SubscriptionPlansResponse)
def get_plans(
    context: AuthContext = Depends(require_user_strict),
) -> SubscriptionPlansResponse:
    user = _user_or_401(context)
    return SubscriptionPlansResponse(**subscription.get_plans(user))  # type: ignore[arg-type]


@router.get("/subscription/me", response_model=SubscriptionStatusResponse)
def subscription_me(
    context: AuthContext = Depends(require_user_strict),
) -> SubscriptionStatusResponse:
    user = _user_or_401(context)
    return SubscriptionStatusResponse(**subscription.get_subscription_view(user))


@router.post(
    "/subscription/orders",
    response_model=OrderResponse,
    status_code=201,
)
def create_order(
    request: CreateOrderRequest,
    context: Annotated[AuthContext, Depends(require_user_strict)],
) -> OrderResponse:
    user = _user_or_401(context)
    try:
        view = payment.create_order(user, request.plan)
    except PaymentError as error:
        raise _payment_http_error(error) from error
    return OrderResponse(**view)


@router.get("/subscription/orders/latest", response_model=LatestOrderResponse)
def latest_order(
    context: Annotated[AuthContext, Depends(require_user_strict)],
) -> LatestOrderResponse:
    user = _user_or_401(context)
    return LatestOrderResponse(**payment.get_latest_order(user))


@router.post(
    "/subscription/orders/{out_trade_no}/cancel", response_model=OrderResponse
)
def cancel_order(
    out_trade_no: str,
    context: Annotated[AuthContext, Depends(require_user_strict)],
) -> OrderResponse:
    user = _user_or_401(context)
    try:
        view = payment.cancel_order(user, out_trade_no)
    except PaymentError as error:
        raise _payment_http_error(error) from error
    return OrderResponse(**view)


@router.put("/subscription/reminder", response_model=SubscriptionStatusResponse)
def set_renew_reminder(
    request: RenewReminderRequest,
    context: Annotated[AuthContext, Depends(require_user_strict)],
) -> SubscriptionStatusResponse:
    user = _user_or_401(context)
    return SubscriptionStatusResponse(
        **subscription.set_renew_reminder(user, request.enabled)
    )


@router.post("/subscription/mock-order", response_model=SubscriptionStatusResponse)
def mock_order(
    context: AuthContext = Depends(require_user_strict),
) -> SubscriptionStatusResponse:
    user = _user_or_401(context)
    try:
        view = subscription.create_mock_order(user)
    except SubscriptionError as error:
        raise _subscription_http_error(error) from error
    return SubscriptionStatusResponse(**view)


@router.post("/subscription/cancel", response_model=SubscriptionStatusResponse)
def cancel_subscription(
    context: AuthContext = Depends(require_user_strict),
) -> SubscriptionStatusResponse:
    user = _user_or_401(context)
    try:
        view = subscription.cancel_subscription(user)
    except SubscriptionError as error:
        raise _subscription_http_error(error) from error
    return SubscriptionStatusResponse(**view)


@router.post("/payment/notify", response_class=PlainTextResponse)
async def payment_notify(request: Request) -> PlainTextResponse:
    # xunhupay posts an application/x-www-form-urlencoded body — every
    # field comes in as a form scalar; unknown extra fields are accepted
    # (they participate in the signature and are archived verbatim).
    # FastAPI's `dict[str, Form()]` annotation does not bind form scalars
    # into a dict (verified: payload stays None), so the body is parsed
    # manually.
    raw = await request.form()
    payload = {key: value for key, value in raw.items() if isinstance(value, str)}
    text, status = payment.handle_notify(payload)
    return PlainTextResponse(text, status_code=status, media_type="text/plain")
