"""Transactional email via the Brevo API (v2 cloud edition, batch 1).

Brevo is the user-confirmed channel (2026-09-04): free tier 300
emails/day covers registration verification + password reset. The
dependency is isolated per the batch-1 spec:

- ``BREVO_API_KEY`` / ``BREVO_SENDER_EMAIL`` are read from the
  environment on every send;
- when either is missing, ``EmailNotConfiguredError`` is raised and the
  auth endpoints degrade to a clear 503 — the app still boots, super
  login still works, nothing crashes.

Sends go through stdlib ``urllib`` (no new dependency) with a 15s
timeout. Only HTML transactional templates defined here exist —
subscription notifications intentionally do NOT send email (user
decision: UI-only display).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
REQUEST_TIMEOUT_SECONDS = 15


class EmailError(Exception):
    """Base class for email delivery failures."""


class EmailNotConfiguredError(EmailError):
    """Raised when BREVO_API_KEY / BREVO_SENDER_EMAIL are not set."""


def _public_base_url() -> str:
    return os.environ.get("VOCAB_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def is_configured() -> bool:
    return bool(os.environ.get("BREVO_API_KEY")) and bool(
        os.environ.get("BREVO_SENDER_EMAIL")
    )


def _send(to: str, subject: str, html: str) -> None:
    api_key = os.environ.get("BREVO_API_KEY", "")
    sender = os.environ.get("BREVO_SENDER_EMAIL", "")
    if not api_key or not sender:
        raise EmailNotConfiguredError(
            "Email service is not configured (BREVO_API_KEY / BREVO_SENDER_EMAIL)"
        )
    payload = json.dumps(
        {
            "sender": {"name": "VocabularyLearning", "email": sender},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        BREVO_API_URL,
        data=payload,
        headers={
            "api-key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            response.read()
    except urllib.error.HTTPError as error:
        body = error.read(500).decode("utf-8", "replace") if error.fp else ""
        raise EmailError(f"Brevo API error {error.code}: {body}") from error
    except (urllib.error.URLError, OSError) as error:
        raise EmailError(f"Brevo API unreachable: {error}") from error


def _wrap(title: str, body_html: str) -> str:
    return f"""
<div style="font-family:Inter,Segoe UI,system-ui,sans-serif;max-width:520px;
            margin:0 auto;padding:32px 24px;color:#2f2a24;">
  <p style="letter-spacing:.12em;font-size:12px;font-weight:800;
            color:#7b5f86;margin:0 0 12px;">VOCABULARYLEARNING</p>
  <h1 style="font-size:22px;margin:0 0 16px;color:#2c2822;">{title}</h1>
  {body_html}
  <p style="font-size:12px;color:#8a8175;margin-top:32px;">
    这封邮件由 VocabularyLearning 发送，链接 1 小时内有效。
  </p>
</div>
"""


def send_verification_email(to: str, token: str) -> None:
    link = f"{_public_base_url()}/#/verify-email?token={token}"
    _send(
        to,
        "激活你的 VocabularyLearning 账号",
        _wrap(
            "激活你的账号",
            f"""
            <p style="margin:0 0 20px;color:#6a6257;">
              感谢注册。点击下面的链接激活账号（1 小时内有效）：
            </p>
            <p style="margin:0 0 24px;">
              <a href="{link}"
                 style="display:inline-block;background:#6f8b79;color:#fffdf7;
                        font-weight:800;padding:14px 18px;border-radius:8px;
                        text-decoration:none;">激活账号</a>
            </p>
            <p style="font-size:13px;color:#8a8175;margin:0;">
              按钮无法点击时，请复制此链接到浏览器打开：<br>{link}
            </p>
            """,
        ),
    )


def send_password_reset_email(to: str, token: str) -> None:
    link = f"{_public_base_url()}/#/reset-password?token={token}"
    _send(
        to,
        "重置你的 VocabularyLearning 密码",
        _wrap(
            "重置密码",
            f"""
            <p style="margin:0 0 20px;color:#6a6257;">
              我们收到了重置密码的请求。点击下面的链接设置新密码
              （1 小时内有效，仅可使用一次）：
            </p>
            <p style="margin:0 0 24px;">
              <a href="{link}"
                 style="display:inline-block;background:#6f8b79;color:#fffdf7;
                        font-weight:800;padding:14px 18px;border-radius:8px;
                        text-decoration:none;">设置新密码</a>
            </p>
            <p style="font-size:13px;color:#8a8175;margin:0;">
              如果这不是你的操作，请忽略本邮件，密码不会变化。<br>
              按钮无法点击时，请复制此链接到浏览器打开：<br>{link}
            </p>
            """,
        ),
    )
