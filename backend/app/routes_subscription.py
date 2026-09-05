"""Subscription API routes (v2 cloud edition, batch 3 / C-09).

Four endpoints, all behind the session guard (C-09 acceptance 5:
unauthenticated calls answer 401):
- GET  /api/subscription/plan       — configuration-driven price
- POST /api/subscription/mock-order — 30 days, effective immediately
- GET  /api/subscription/me         — lazy-expiring status read
- POST /api/subscription/cancel     — mock cancel

No emails anywhere in this flow (2026-09-04 拍板: 订阅全程仅 UI 展示).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import subscription
from app.auth import AuthContext, require_user_strict
from app.models import SubscriptionPlanResponse, SubscriptionStatusResponse
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


@router.get("/subscription/plan", response_model=SubscriptionPlanResponse)
def get_plan(
    context: AuthContext = Depends(require_user_strict),
) -> SubscriptionPlanResponse:
    return SubscriptionPlanResponse(**subscription.get_plan())  # type: ignore[arg-type]


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


@router.get("/subscription/me", response_model=SubscriptionStatusResponse)
def subscription_me(
    context: AuthContext = Depends(require_user_strict),
) -> SubscriptionStatusResponse:
    user = _user_or_401(context)
    return SubscriptionStatusResponse(**subscription.get_subscription_view(user))


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
