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
import logging
import os
import urllib.error
import urllib.request

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_SENDERS_URL = "https://api.brevo.com/v3/senders"
REQUEST_TIMEOUT_SECONDS = 15
SENDER_SELF_CHECK_TIMEOUT_SECONDS = 10

logger = logging.getLogger(__name__)


class EmailError(Exception):
    """Base class for email delivery failures."""


class EmailNotConfiguredError(EmailError):
    """Raised when BREVO_API_KEY / BREVO_SENDER_EMAIL are not set."""


def _public_base_url() -> str:
    # C-01a: emails now carry a 6-digit code instead of links, so the
    # public base URL is no longer embedded anywhere — kept for any
    # future template that needs absolute URLs.
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
            status = response.getcode()
            body = response.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        body = error.read(500).decode("utf-8", "replace") if error.fp else ""
        logger.error(
            "Brevo send rejected: status=%s to=%s body=%s", error.code, to, body
        )
        raise EmailError(f"Brevo API error {error.code}: {body}") from error
    except (urllib.error.URLError, OSError) as error:
        logger.error("Brevo send unreachable: to=%s error=%s", to, error)
        raise EmailError(f"Brevo API unreachable: {error}") from error
    # A 2xx from Brevo only means the request was *accepted*: delivery is
    # decided asynchronously (e.g. an unvalidated sender is rejected after
    # the fact with a 201 on our side and a "sender not valid" event on
    # theirs). Log status + messageId so ops can correlate with the Brevo
    # event log when a user reports a missing email.
    message_id = None
    try:
        message_id = json.loads(body).get("messageId")
    except (json.JSONDecodeError, AttributeError):
        logger.warning(
            "Brevo send accepted but response body was not JSON: status=%s to=%s body=%r",
            status,
            to,
            body[:200],
        )
    logger.info(
        "Brevo send accepted: status=%s messageId=%s to=%s", status, message_id, to
    )


def verify_sender_configuration() -> None:
    """Startup self-check: warn when the configured sender is not validated.

    Brevo accepts sends with an unvalidated sender (HTTP 201) and then
    silently rejects them asynchronously, so registration emails vanish
    without any application-side signal. This check calls
    ``GET /v3/senders`` and logs a WARNING when ``BREVO_SENDER_EMAIL`` is
    not among the validated senders — the app must still boot and every
    failure mode here (no key, timeout, HTTP error, bad payload) only
    logs, never raises.
    """
    api_key = os.environ.get("BREVO_API_KEY", "")
    sender = os.environ.get("BREVO_SENDER_EMAIL", "")
    if not api_key or not sender:
        logger.info(
            "Brevo sender self-check skipped: BREVO_API_KEY / "
            "BREVO_SENDER_EMAIL not configured"
        )
        return
    request = urllib.request.Request(
        BREVO_SENDERS_URL,
        headers={"api-key": api_key, "accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(  # noqa: S310
            request, timeout=SENDER_SELF_CHECK_TIMEOUT_SECONDS
        ) as response:
            body = response.read(65536).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        logger.warning(
            "Brevo sender self-check could not reach the API "
            "(email delivery cannot be verified): %s",
            error,
        )
        return
    try:
        payload = json.loads(body)
        senders = payload.get("senders") or []
        validated = {
            str(entry.get("email", "")).lower()
            for entry in senders
            if isinstance(entry, dict)
        }
    except (json.JSONDecodeError, AttributeError):
        logger.warning(
            "Brevo sender self-check got an unexpected payload "
            "(email delivery cannot be verified)"
        )
        return
    if sender.lower() in validated:
        logger.info(
            "Brevo sender self-check OK: %s is a validated sender", sender
        )
    else:
        logger.warning(
            "BREVO_SENDER_EMAIL %s is NOT a validated Brevo sender: Brevo "
            "will still answer 201 to send requests but will silently drop "
            "the emails. Validate the sender at https://app.brevo.com/senders",
            sender,
        )


def _wrap(title: str, body_html: str) -> str:
    return f"""
<div style="font-family:Inter,Segoe UI,system-ui,sans-serif;max-width:520px;
            margin:0 auto;padding:32px 24px;color:#2f2a24;">
  <p style="letter-spacing:.12em;font-size:12px;font-weight:800;
            color:#7b5f86;margin:0 0 12px;">VOCABULARYLEARNING</p>
  <h1 style="font-size:22px;margin:0 0 16px;color:#2c2822;">{title}</h1>
  {body_html}
  <p style="font-size:12px;color:#8a8175;margin-top:32px;">
    这封邮件由 VocabularyLearning 发送，验证码 10 分钟内有效。
    请勿转发本邮件，以免验证码泄露。
  </p>
</div>
"""


def _code_block(code: str) -> str:
    return (
        '<p style="margin:0 0 24px;font-size:32px;font-weight:800;'
        'letter-spacing:.35em;color:#2c2822;'
        'background:#f4f1ea;border-radius:8px;padding:18px 20px;'
        'text-align:center;">' + code + "</p>"
    )


def send_verification_email(to: str, code: str) -> None:
    _send(
        to,
        "你的 VocabularyLearning 激活验证码",
        _wrap(
            "激活你的账号",
            f"""
            <p style="margin:0 0 20px;color:#6a6257;">
              感谢注册。请在网站的验证码输入框中输入以下 6 位数字验证码
              完成激活（10 分钟内有效）：
            </p>
            {_code_block(code)}
            <p style="font-size:13px;color:#8a8175;margin:0;">
              如果验证码已过期或输入错误次数过多，请回到页面重新获取。
            </p>
            """,
        ),
    )


def send_password_reset_email(to: str, code: str) -> None:
    _send(
        to,
        "你的 VocabularyLearning 密码重置验证码",
        _wrap(
            "重置密码",
            f"""
            <p style="margin:0 0 20px;color:#6a6257;">
              我们收到了重置密码的请求。请在网站的重置页面输入以下
              6 位数字验证码并设置新密码（10 分钟内有效）：
            </p>
            {_code_block(code)}
            <p style="font-size:13px;color:#8a8175;margin:0;">
              如果这不是你的操作，请忽略本邮件，密码不会变化。
            </p>
            """,
        ),
    )
