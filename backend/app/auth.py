"""Account & session authentication for the v2 cloud edition (batch 1).

Covers C-01 (users + email/password auth), C-02 (session tokens +
request guard), C-04 (super account) and the email loop storage
(verify/reset tokens with 1h expiry and single-use semantics).

Design decisions:
- Server-side opaque session tokens instead of JWT: revocable on
  logout/password reset, no extra dependency, and the single-process
  SQLite deployment makes statelessness pointless. Tokens are stored
  as SHA-256 hashes so a leaked database file does not leak live
  sessions.
- Passwords: stdlib ``hashlib.scrypt`` (n=2^14, r=8, p=1) with a
  random 16-byte salt and constant-time verification. No new
  dependency.
- Batch 2 removed the ``VOCAB_REQUIRE_AUTH=0`` legacy fallback: the
  request guard is always on, and every study-API read/write is scoped
  to the authenticated user (C-05/C-06/C-07). The legacy v1.1 test
  suites authenticate through a test session instead (see
  backend/tests/conftest.py).
- Password policy (batch 2, P2 fix): minimum 8 characters AND at least
  one letter and one digit, enforced on register / reset / change.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SESSION_TTL_DAYS = 30
EMAIL_TOKEN_TTL_SECONDS = 3600  # 1 hour, per the 2026-09-04 spec.

# Resend throttling shared by register / resend-verification /
# forgot-password (all Brevo-bound per the designer's C-05 spec):
# 60s cooldown between sends to one address, at most 3 per minute.
RESEND_COOLDOWN_SECONDS = 60
RESEND_WINDOW_SECONDS = 60
RESEND_MAX_PER_WINDOW = 3

_SUPER_EMAIL_DEFAULT = "super@vocab.local"
_SUPER_PASSWORD_DEFAULT = "vocab-super-2026"

MIN_PASSWORD_LENGTH = 8

PASSWORD_POLICY_MESSAGE = (
    "Password must be at least 8 characters and contain both letters and digits"
)


def password_policy_error(password: str) -> str | None:
    """Return the policy violation message, or None when acceptable.

    Batch 2 (P2 fix, decision record D3): on top of the batch-1 minimum
    length, passwords must mix letters and digits. Enforced server-side
    on every password-setting path (register / reset / change).
    """

    if len(password) < MIN_PASSWORD_LENGTH:
        return PASSWORD_POLICY_MESSAGE
    if not any(char.isalpha() for char in password):
        return PASSWORD_POLICY_MESSAGE
    if not any(char.isdigit() for char in password):
        return PASSWORD_POLICY_MESSAGE
    return None


@dataclass(frozen=True)
class AuthContext:
    """Identity attached to a request when the guard is active."""

    user_id: str | None
    email: str | None
    is_super: bool

    @property
    def authenticated(self) -> bool:
        return self.user_id is not None


ANONYMOUS = AuthContext(user_id=None, email=None, is_super=False)


class RateLimitedError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many email requests; retry later")
        self.retry_after_seconds = retry_after_seconds


class EmailTokenError(Exception):
    """Raised when a verify/reset token is invalid, expired or used.

    All three cases map to HTTP 410 per the v2 spec — the frontend
    treats them uniformly as 「链接已失效或已使用」.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = stored.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    actual = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1
    )
    return hmac.compare_digest(actual, expected)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def create_user(
    email: str, password: str, *, is_super: bool = False, verified: bool = False
) -> dict[str, object]:

    from app.db import connect

    user_id = uuid.uuid4().hex
    now = _iso(_now())
    with connect() as connection:
        connection.execute(
            """
            insert into users (id, email, password_hash, email_verified, is_super,
                               created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                normalize_email(email),
                hash_password(password),
                1 if verified else 0,
                1 if is_super else 0,
                now,
                now,
            ),
        )
    return {"id": user_id, "email": normalize_email(email)}


def find_user_by_email(email: str) -> dict[str, object] | None:
    from app.db import connect
    with connect() as connection:
        row = connection.execute(
            "select * from users where email = ?", (normalize_email(email),)
        ).fetchone()
    return dict(row) if row else None


def find_user_by_id(user_id: str) -> dict[str, object] | None:
    from app.db import connect
    with connect() as connection:
        row = connection.execute(
            "select * from users where id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def mark_user_verified(user_id: str) -> None:
    from app.db import connect
    with connect() as connection:
        connection.execute(
            "update users set email_verified = 1, updated_at = ? where id = ?",
            (_iso(_now()), user_id),
        )


def update_user_password(user_id: str, new_password: str) -> None:
    from app.db import connect
    with connect() as connection:
        connection.execute(
            "update users set password_hash = ?, updated_at = ? where id = ?",
            (hash_password(new_password), _iso(_now()), user_id),
        )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def create_session(user_id: str) -> str:
    from app.db import connect
    raw_token = secrets.token_urlsafe(32)
    now = _now()
    with connect() as connection:
        connection.execute(
            """
            insert into sessions (id, user_id, token_hash, created_at, expires_at)
            values (?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                user_id,
                _hash_token(raw_token),
                _iso(now),
                _iso(now + timedelta(days=SESSION_TTL_DAYS)),
            ),
        )
    return raw_token


def resolve_session(raw_token: str) -> dict[str, object] | None:
    from app.db import connect
    with connect() as connection:
        row = connection.execute(
            """
            select u.*, s.expires_at as session_expires_at
            from sessions s join users u on u.id = s.user_id
            where s.token_hash = ?
            """,
            (_hash_token(raw_token),),
        ).fetchone()
    if row is None:
        return None
    user = dict(row)
    if user.pop("session_expires_at", None) <= _iso(_now()):
        return None
    return user


def revoke_session(raw_token: str) -> None:
    from app.db import connect
    with connect() as connection:
        connection.execute(
            "delete from sessions where token_hash = ?", (_hash_token(raw_token),)
        )


def revoke_all_sessions(user_id: str) -> None:
    from app.db import connect
    with connect() as connection:
        connection.execute("delete from sessions where user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# Email tokens (verify / reset)
# ---------------------------------------------------------------------------


def issue_email_token(user_id: str, purpose: str) -> str:
    from app.db import connect
    if purpose not in ("verify_email", "reset_password"):
        raise ValueError(f"unsupported email token purpose: {purpose}")
    raw_token = secrets.token_urlsafe(32)
    now = _now()
    with connect() as connection:
        connection.execute(
            """
            insert into email_tokens (id, user_id, token_hash, purpose,
                                      expires_at, used_at, created_at)
            values (?, ?, ?, ?, ?, null, ?)
            """,
            (
                uuid.uuid4().hex,
                user_id,
                _hash_token(raw_token),
                purpose,
                _iso(now + timedelta(seconds=EMAIL_TOKEN_TTL_SECONDS)),
                _iso(now),
            ),
        )
    return raw_token


def consume_email_token(raw_token: str, purpose: str) -> dict[str, object]:
    """Validate + burn an email token and return its user row."""

    from app.db import connect

    now = _now()
    with connect() as connection:
        row = connection.execute(
            "select * from email_tokens where token_hash = ? and purpose = ?",
            (_hash_token(raw_token), purpose),
        ).fetchone()
        if row is None:
            raise EmailTokenError("invalid")
        record = dict(row)
        if record["used_at"] is not None:
            raise EmailTokenError("used")
        if record["expires_at"] <= _iso(now):
            raise EmailTokenError("expired")
        connection.execute(
            "update email_tokens set used_at = ? where id = ?",
            (_iso(now), record["id"]),
        )
    user = find_user_by_id(str(record["user_id"]))
    if user is None:
        raise EmailTokenError("invalid")
    return user


def peek_email_token(raw_token: str, purpose: str) -> dict[str, object] | None:
    """Return the user row behind a token without burning it.

    Used by ``GET /api/auth/reset-token-info`` so the reset page can
    render 「为 xxx@example.com 设置新密码」 before the user submits.
    """

    from app.db import connect

    now = _now()
    with connect() as connection:
        row = connection.execute(
            "select * from email_tokens where token_hash = ? and purpose = ?",
            (_hash_token(raw_token), purpose),
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    if record["used_at"] is not None or record["expires_at"] <= _iso(now):
        return None
    return find_user_by_id(str(record["user_id"]))


# ---------------------------------------------------------------------------
# Resend rate limiting (in-memory; single-process uvicorn deployment)
# ---------------------------------------------------------------------------


@dataclass
class _RateState:
    last_sent: float = 0.0
    recent: list[float] = field(default_factory=list)


_rate_states: dict[str, _RateState] = {}


def clear_rate_limiter() -> None:
    """Reset throttle state (used by tests to isolate cases)."""

    _rate_states.clear()


def check_send_rate(email: str) -> None:
    """Raise RateLimitedError if this address must wait."""

    key = normalize_email(email)
    state = _rate_states.setdefault(key, _RateState())
    now = time.monotonic()
    state.recent = [stamp for stamp in state.recent
                    if now - stamp < RESEND_WINDOW_SECONDS]
    if state.recent and now - state.last_sent < RESEND_COOLDOWN_SECONDS:
        raise RateLimitedError(
            int(RESEND_COOLDOWN_SECONDS - (now - state.last_sent)) + 1
        )
    if len(state.recent) >= RESEND_MAX_PER_WINDOW:
        raise RateLimitedError(
            int(RESEND_WINDOW_SECONDS - (now - state.recent[0])) + 1
        )


def record_send(email: str) -> None:
    key = normalize_email(email)
    state = _rate_states.setdefault(key, _RateState())
    now = time.monotonic()
    state.last_sent = now
    state.recent.append(now)


# ---------------------------------------------------------------------------
# Super account provisioning (C-04)
# ---------------------------------------------------------------------------


def super_credentials() -> tuple[str, str]:
    return (
        normalize_email(os.environ.get("VOCAB_SUPER_EMAIL", _SUPER_EMAIL_DEFAULT)),
        os.environ.get("VOCAB_SUPER_PASSWORD", _SUPER_PASSWORD_DEFAULT),
    )


def ensure_super_account(connection) -> None:
    """Idempotently provision the super account.

    Called from ``app.db.migrate`` on every connect. INSERT OR IGNORE
    keeps an existing row untouched (a rotated password survives
    restarts); credentials come from ``VOCAB_SUPER_EMAIL`` /
    ``VOCAB_SUPER_PASSWORD`` when provided. Super bypasses the email
    verification loop entirely.
    """

    email, password = super_credentials()
    now = _iso(_now())
    connection.execute(
        """
        insert or ignore into users
            (id, email, password_hash, email_verified, is_super, created_at, updated_at)
        values (?, ?, ?, 1, 1, ?, ?)
        """,
        (f"super-{email}", email, hash_password(password), now, now),
    )


# ---------------------------------------------------------------------------
# Request guard (C-02)
# ---------------------------------------------------------------------------


def require_user(request: Request) -> AuthContext:
    """FastAPI dependency guarding the study API in the cloud edition.

    Batch 2 removed the ``VOCAB_REQUIRE_AUTH=0`` fallback: a valid
    ``Authorization: Bearer <session-token>`` header is always required.
    """

    header = request.headers.get("authorization", "")
    scheme, _, raw_token = header.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise HTTPException(status_code=401, detail="Authentication required")
    user = resolve_session(raw_token.strip())
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return AuthContext(
        user_id=str(user["id"]), email=str(user["email"]),
        is_super=bool(user["is_super"]),
    )


# Batch 2: with the disabled-guard fallback gone, require_user_strict is
# simply require_user. Kept as an alias so the auth routes read the same
# as in batch 1.
require_user_strict = require_user
