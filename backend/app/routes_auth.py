"""Auth API routes for the v2 cloud edition (batch 1).

Flow per the 2026-09-04 decisions:
- register: create the (unverified) account, then send the Brevo
  verification email. If the email cannot be sent the account is
  rolled back so a retry re-runs the whole flow cleanly (503).
- login: 401 on bad credentials, 403 ``email_not_verified`` when the
  account exists but was never activated, 200 + session token
  otherwise. super (pre-provisioned, pre-verified) needs no email.
- verify-email / reset tokens: 1h expiry, single use, failures map
  to 410 so the frontend can uniformly show 「链接已失效或已使用」.
- resend endpoints: 60s cooldown + max 3/min per address (429).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app import auth, emailing
from app.auth import AuthContext, RateLimitedError, require_user_strict
from app.models import (
    ChangePasswordRequest,
    EmailOnlyRequest,
    EmailStatusResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    TokenEmailResponse,
    UserResponse,
)

router = APIRouter()


def _check_rate(email: str) -> None:
    try:
        auth.check_send_rate(email)
    except RateLimitedError as error:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "发送过于频繁，请稍后再试",
                "retryAfter": error.retry_after_seconds,
            },
        ) from error


def _registration_email(email: str) -> str:
    normalized = auth.normalize_email(email)
    if not auth.is_valid_email(normalized):
        raise HTTPException(status_code=400, detail="Invalid email address")
    return normalized


@router.post("/auth/register", status_code=201, response_model=RegisterResponse)
def register(request: RegisterRequest) -> RegisterResponse:
    email = _registration_email(request.email)
    policy_error = auth.password_policy_error(request.password)
    if policy_error is not None:
        raise HTTPException(status_code=400, detail=policy_error)
    _check_rate(email)
    if auth.find_user_by_email(email) is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "email_taken", "message": "该邮箱已注册"},
        )
    created = auth.create_user(email, request.password)
    try:
        token = auth.issue_email_token(str(created["id"]), "verify_email")
        emailing.send_verification_email(email, token)
    except emailing.EmailError as error:
        # Roll the half-created account back so the user can retry the
        # whole flow instead of being stuck unverified with no email.
        _delete_user(str(created["id"]))
        raise HTTPException(
            status_code=503,
            detail={
                "code": "email_send_failed",
                "message": f"验证邮件发送失败（{error}），请稍后重试",
            },
        ) from error
    auth.record_send(email)
    return RegisterResponse(email=email, message="账号已创建，请查收验证邮件")


def _delete_user(user_id: str) -> None:
    from app.db import connect

    with connect() as connection:
        connection.execute("delete from sessions where user_id = ?", (user_id,))
        connection.execute("delete from email_tokens where user_id = ?", (user_id,))
        connection.execute("delete from users where id = ?", (user_id,))


@router.post("/auth/login", response_model=LoginResponse)
def login(request: LoginRequest) -> LoginResponse:
    email = auth.normalize_email(request.email)
    user = auth.find_user_by_email(email)
    if user is None or not auth.verify_password(
        request.password, str(user["password_hash"])
    ):
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    if not user["email_verified"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "email_not_verified",
                "message": "该邮箱尚未激活，请查收验证邮件",
            },
        )
    token = auth.create_session(str(user["id"]))
    return LoginResponse(
        token=token,
        user=UserResponse(
            id=str(user["id"]),
            email=str(user["email"]),
            emailVerified=bool(user["email_verified"]),
            isSuper=bool(user["is_super"]),
        ),
    )


@router.post("/auth/logout")
def logout(
    request: Request, context: AuthContext = Depends(require_user_strict)
) -> dict[str, bool]:
    header = request.headers.get("authorization", "")
    raw_token = header.partition(" ")[2].strip()
    auth.revoke_session(raw_token)
    return {"ok": True}


@router.get("/auth/me", response_model=UserResponse)
def me(context: AuthContext = Depends(require_user_strict)) -> UserResponse:
    user = auth.find_user_by_id(str(context.user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return UserResponse(
        id=str(user["id"]),
        email=str(user["email"]),
        emailVerified=bool(user["email_verified"]),
        isSuper=bool(user["is_super"]),
    )


@router.post("/auth/change-password")
def change_password(
    request: ChangePasswordRequest,
    context: AuthContext = Depends(require_user_strict),
) -> dict[str, bool]:
    user = auth.find_user_by_id(str(context.user_id))
    if user is None or not auth.verify_password(
        request.currentPassword, str(user["password_hash"])
    ):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    policy_error = auth.password_policy_error(request.newPassword)
    if policy_error is not None:
        raise HTTPException(status_code=400, detail=policy_error)
    auth.update_user_password(str(user["id"]), request.newPassword)
    auth.revoke_all_sessions(str(user["id"]))
    return {"ok": True}


@router.get("/auth/verify-email", response_model=TokenEmailResponse)
def verify_email(token: str) -> TokenEmailResponse:
    try:
        user = auth.consume_email_token(token, "verify_email")
    except auth.EmailTokenError:
        raise HTTPException(
            status_code=410,
            detail={"code": "token_invalid", "message": "链接已失效或已使用"},
        )
    auth.mark_user_verified(str(user["id"]))
    return TokenEmailResponse(email=str(user["email"]))


@router.get("/auth/email-status", response_model=EmailStatusResponse)
def email_status(email: str) -> EmailStatusResponse:
    user = auth.find_user_by_email(email)
    return EmailStatusResponse(verified=bool(user and user["email_verified"]))


@router.post("/auth/resend-verification")
def resend_verification(request: EmailOnlyRequest) -> dict[str, bool]:
    email = auth.normalize_email(request.email)
    _check_rate(email)
    user = auth.find_user_by_email(email)
    if user is None or user["email_verified"]:
        # Do not leak account existence; respond like a no-op send.
        auth.record_send(email)
        return {"ok": True}
    try:
        token = auth.issue_email_token(str(user["id"]), "verify_email")
        emailing.send_verification_email(email, token)
    except emailing.EmailError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "email_send_failed",
                "message": f"重发失败（{error}），请稍后再试",
            },
        ) from error
    auth.record_send(email)
    return {"ok": True}


@router.post("/auth/forgot-password")
def forgot_password(request: EmailOnlyRequest) -> dict[str, bool]:
    email = auth.normalize_email(request.email)
    _check_rate(email)
    user = auth.find_user_by_email(email)
    if user is None:
        # Always 200: never leak whether an address is registered.
        auth.record_send(email)
        return {"ok": True}
    try:
        token = auth.issue_email_token(str(user["id"]), "reset_password")
        emailing.send_password_reset_email(email, token)
    except emailing.EmailError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "email_send_failed",
                "message": f"重置邮件发送失败（{error}），请稍后再试",
            },
        ) from error
    auth.record_send(email)
    return {"ok": True}


@router.get("/auth/reset-token-info", response_model=TokenEmailResponse)
def reset_token_info(token: str) -> TokenEmailResponse:
    user = auth.peek_email_token(token, "reset_password")
    if user is None:
        raise HTTPException(
            status_code=410,
            detail={"code": "token_invalid", "message": "链接已失效或已使用"},
        )
    return TokenEmailResponse(email=str(user["email"]))


@router.post("/auth/reset-password")
def reset_password(request: ResetPasswordRequest) -> dict[str, bool]:
    policy_error = auth.password_policy_error(request.newPassword)
    if policy_error is not None:
        raise HTTPException(status_code=400, detail=policy_error)
    try:
        user = auth.consume_email_token(request.token, "reset_password")
    except auth.EmailTokenError:
        raise HTTPException(
            status_code=410,
            detail={"code": "token_invalid", "message": "链接已失效或已使用"},
        )
    auth.update_user_password(str(user["id"]), request.newPassword)
    auth.revoke_all_sessions(str(user["id"]))
    return {"ok": True}
