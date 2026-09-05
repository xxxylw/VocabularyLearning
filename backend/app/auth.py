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
# C-01a (2026-09-05 拍板): email verification moved from 1-hour links
# to 6-digit codes — 10 minutes validity, voided after 5 wrong attempts.
EMAIL_CODE_TTL_SECONDS = 600
EMAIL_CODE_MAX_ATTEMPTS = 5

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


class EmailCodeError(Exception):
    """Raised when a 6-digit email code cannot be consumed (C-01a).

    ``reason`` is one of:
    - ``missing``      — no active code for this address+purpose
                         (never requested, already used, or voided by
                         a resend);
    - ``expired``      — the active code is past its 10-minute TTL;
    - ``max_attempts`` — the code was submitted wrongly 5 times and is
                         now void;
    - ``invalid``      — wrong code, attempts not yet exhausted. The
                         exception carries ``remaining`` so the endpoint
                         can tell the user how many tries are left.
    """

    def __init__(self, reason: str, remaining: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.remaining = remaining


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
# Email verification codes (verify / reset) — C-01a
#
# The 2026-09-05 decision replaced 1-hour single-use links with 6-digit
# numeric codes sent in the email body. Storage reuses the ``email_tokens``
# table ("同一存储扩展承载" per the PM spec): ``token_hash`` holds a salted
# scrypt hash of the code (never plaintext), the new ``attempts`` column
# counts wrong submissions, and issuing a new code immediately voids any
# previous active row for the same user+purpose — one mailbox has at most
# one live code at a time.
# ---------------------------------------------------------------------------


def issue_email_code(user_id: str, purpose: str) -> str:
    """Create a new 6-digit code, voiding any previous active one."""

    from app.db import connect

    if purpose not in ("verify_email", "reset_password"):
        raise ValueError(f"unsupported email code purpose: {purpose}")
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    with connect() as connection:
        # 单一有效验证码: a resend must kill the old code immediately.
        connection.execute(
            """
            update email_tokens set used_at = ?
            where user_id = ? and purpose = ? and used_at is null
            """,
            (_iso(now), user_id, purpose),
        )
        connection.execute(
            """
            insert into email_tokens (id, user_id, token_hash, purpose,
                                      expires_at, used_at, created_at, attempts)
            values (?, ?, ?, ?, ?, null, ?, 0)
            """,
            (
                uuid.uuid4().hex,
                user_id,
                hash_password(code),  # salted scrypt — 库内无明文
                purpose,
                _iso(now + timedelta(seconds=EMAIL_CODE_TTL_SECONDS)),
                _iso(now),
            ),
        )
    return code


def consume_email_code(
    email: str, code: str, purpose: str
) -> dict[str, object]:
    """Validate a submitted code and burn it on success; return the user row.

    Semantics (C-01a):
    - the newest active row (``used_at is null``) is THE live code;
    - a submitted code that matches an older, already-voided row answers
      ``missing`` (the code was superseded by a resend) instead of
      silently burning attempts on the live one;
    - wrong codes increment ``attempts`` on the live row and persist
      even though we raise — the raise happens AFTER the ``with`` block
      so sqlite's context manager commits, not rolls back.
    """

    from app.db import connect

    user = find_user_by_email(email)
    if user is None:
        # Same error as "no code": never leak which addresses exist.
        raise EmailCodeError("missing")
    now = _now()
    window_start = _iso(now - timedelta(seconds=EMAIL_CODE_TTL_SECONDS))
    error: EmailCodeError | None = None
    with connect() as connection:
        rows = connection.execute(
            """
            select * from email_tokens
            where user_id = ? and purpose = ?
              and created_at > ?
            order by created_at desc
            limit 20
            """,
            (str(user["id"]), purpose, window_start),
        ).fetchall()
        active = next((row for row in rows if row["used_at"] is None), None)
        if active is None:
            error = EmailCodeError("missing")
        elif str(active["expires_at"]) <= _iso(now):
            error = EmailCodeError("expired")
        elif int(active["attempts"] or 0) >= EMAIL_CODE_MAX_ATTEMPTS:
            error = EmailCodeError("max_attempts")
        elif verify_password(code, str(active["token_hash"])):
            connection.execute(
                "update email_tokens set used_at = ? where id = ?",
                (_iso(now), active["id"]),
            )
        else:
            # Not the live code. If it matches a superseded row the user
            # is typing an old email's code — tell them it's dead.
            stale_match = any(
                verify_password(code, str(row["token_hash"]))
                for row in rows
                if row["used_at"] is not None
            )
            if stale_match:
                error = EmailCodeError("missing")
            else:
                attempts = int(active["attempts"] or 0) + 1
                connection.execute(
                    "update email_tokens set attempts = ? where id = ?",
                    (attempts, active["id"]),
                )
                if attempts >= EMAIL_CODE_MAX_ATTEMPTS:
                    # 第 5 次错误：作废该码，必须重新获取
                    connection.execute(
                        "update email_tokens set used_at = ? where id = ?",
                        (_iso(now), active["id"]),
                    )
                    error = EmailCodeError("max_attempts")
                else:
                    error = EmailCodeError(
                        "invalid", remaining=EMAIL_CODE_MAX_ATTEMPTS - attempts
                    )
    if error is not None:
        raise error
    return user


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
