"""Payment gateway clients + order lifecycle (v3 P0, official channels).

2026-09-06 拍板：放弃虎皮椒聚合通道，改接官方直连：
- **微信支付 APIv3 · Native（扫码）**：统一下单 /v3/pay/transactions/
  native（返回 code_url，前端渲染二维码）、支付回调（平台证书验签 +
  AES-256-GCM 解密 resource，平台证书自动轮换）、查询订单、关闭订单。
- **支付宝 · 电脑网站支付 alipay.trade.page.pay（+ 手机网站支付
  alipay.trade.wap.pay 按 User-Agent 自适应）**：跳转官方收银台、
  异步通知 RSA2 验签、查询 / 关单。网关 prod / sandbox 可切
  （支付宝开放平台沙箱支持联调）。

协议要点（官方文档 re-verified 2026-09-06）：
- 微信 APIv3 每个请求都要用**商户私钥**对
  ``METHOD\nPATH?query\nTIMESTAMP\nNONCE\nBODY\n`` 做 SHA256withRSA，
  放进 Authorization: WECHATPAY2-SHA256-RSA2048；回调则带
  Wechatpay-Timestamp/Nonce/Signature/Serial 四个头，用**微信支付平台
  证书公钥**验签（平台证书从 /v3/certificates 拉取、用 APIv3 key 做
  AES-256-GCM 解密，按 serial 缓存并定期/按需刷新——商户侧无需手工
  更换平台证书）。回调报文本体是 ``resource`` 密文，同样用 APIv3 key
  解密。
- 支付宝：公共参数（app_id/method/charset/sign_type/timestamp/
  version/notify_url/biz_content）按 key ASCII 升序拼 ``k=v&…``（不含
  sign 与 sign_type、不含空值），RSA2（SHA256withRSA）签名的 URL 直连
  GET 即为收银台跳转链接；异步通知按同一规则验签（支付宝公钥），校验
  app_id / 金额 / trade_status 后回 ``success`` 纯文本。

配置（env，未配置即降级——沿用 Brevo 的降级模式）：
- 微信：WECHAT_APPID / WECHAT_MCHID / WECHAT_APIV3_KEY /
  WECHAT_MCH_PRIVATE_KEY_PATH（apiclient_key.pem）/ WECHAT_MCH_CERT_SERIAL
- 支付宝：ALIPAY_APPID / ALIPAY_PRIVATE_KEY_PATH（应用私钥）/
  ALIPAY_PUBLIC_KEY_PATH（支付宝公钥）/ ALIPAY_GATEWAY（prod 或沙箱）
- 公共：PAYMENT_NOTIFY_URL（站点外链 base，域名/ICP 未定先占位，
  可分别用 WECHAT_NOTIFY_URL / ALIPAY_NOTIFY_URL 覆盖完整回调地址）；
  PAYMENT_ORDER_TTL_MINUTES（默认 15）；PAYMENT_ORDER_TITLE。

密钥未配置时：下单端点返回 503 payment_not_configured（明确到渠道），
其余功能不受影响。所有金额本地一律以「分」为单位，只在网关边界转换，
入账前与下单快照精确比对（金额不符不确认入账）。

RSA 签名 / AES-GCM 解密依赖 ``cryptography``（backend 唯一新依赖，
pyproject 已声明）。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import parse as url_parse
from urllib import request as url_request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509 import load_pem_x509_certificate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Channels & error codes
# ---------------------------------------------------------------------------

CHANNEL_WECHAT = "wechat"
CHANNEL_ALIPAY = "alipay"
CHANNELS = (CHANNEL_WECHAT, CHANNEL_ALIPAY)

DEFAULT_ORDER_TTL_MINUTES = 15
HTTP_TIMEOUT_SECONDS = 15
PLATFORM_CERT_REFRESH_SECONDS = 12 * 3600  # 平台证书缓存 12h

DEFAULT_WECHAT_GATEWAY = "https://api.mch.weixin.qq.com"
DEFAULT_ALIPAY_GATEWAY = "https://openapi.alipay.com/gateway.do"

PLAN_NOT_FOUND = "plan_not_found"
RENEW_NOT_ELIGIBLE = "renew_not_eligible"
PAYMENT_NOT_CONFIGURED = "payment_not_configured"
PAYMENT_CHANNEL_INVALID = "payment_channel_invalid"
GATEWAY_ERROR = "payment_gateway_error"
ORDER_NOT_FOUND = "order_not_found"
ORDER_NOT_CANCELLABLE = "order_not_cancellable"
SUPER_CONFLICT = "super_account"

_CHANNEL_LABELS = {CHANNEL_WECHAT: "微信支付", CHANNEL_ALIPAY: "支付宝"}


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
# Configuration (env-driven, per channel, all OPTIONAL)
# ---------------------------------------------------------------------------


def _wechat_appid() -> str:
    return os.environ.get("WECHAT_APPID", "").strip()


def _wechat_mchid() -> str:
    return os.environ.get("WECHAT_MCHID", "").strip()


def _wechat_apiv3_key() -> str:
    return os.environ.get("WECHAT_APIV3_KEY", "").strip()


def _wechat_private_key_path() -> str:
    return os.environ.get("WECHAT_MCH_PRIVATE_KEY_PATH", "").strip()


def _wechat_cert_serial() -> str:
    return os.environ.get("WECHAT_MCH_CERT_SERIAL", "").strip()


def _wechat_gateway() -> str:
    raw = os.environ.get("WECHAT_PAY_GATEWAY", DEFAULT_WECHAT_GATEWAY)
    return raw.strip().rstrip("/") or DEFAULT_WECHAT_GATEWAY


def _alipay_appid() -> str:
    return os.environ.get("ALIPAY_APPID", "").strip()


def _alipay_private_key_path() -> str:
    return os.environ.get("ALIPAY_PRIVATE_KEY_PATH", "").strip()


def _alipay_public_key_path() -> str:
    return os.environ.get("ALIPAY_PUBLIC_KEY_PATH", "").strip()


def _alipay_gateway() -> str:
    raw = os.environ.get("ALIPAY_GATEWAY", DEFAULT_ALIPAY_GATEWAY)
    return raw.strip().rstrip("/") or DEFAULT_ALIPAY_GATEWAY


def _notify_base() -> str:
    return os.environ.get("PAYMENT_NOTIFY_URL", "").strip().rstrip("/")


def _notify_url(channel: str) -> str:
    prefix = "WECHAT" if channel == CHANNEL_WECHAT else "ALIPAY"
    override = os.environ.get(f"{prefix}_NOTIFY_URL", "").strip()
    if override:
        return override
    base = _notify_base()
    return f"{base}/api/payment/notify/{channel}" if base else ""


def _order_title() -> str:
    return (
        os.environ.get("PAYMENT_ORDER_TITLE", "词汇学习订阅").strip()
        or "词汇学习订阅"
    )


def order_ttl_minutes() -> int:
    return _read_int_env("PAYMENT_ORDER_TTL_MINUTES", DEFAULT_ORDER_TTL_MINUTES)


def is_channel_configured(channel: str) -> bool:
    """True only when the given channel can actually place orders."""

    if channel == CHANNEL_WECHAT:
        return bool(
            _wechat_appid()
            and _wechat_mchid()
            and _wechat_apiv3_key()
            and _wechat_private_key_path()
            and _wechat_cert_serial()
            and os.path.isfile(_wechat_private_key_path())
            and _notify_url(CHANNEL_WECHAT)
        )
    if channel == CHANNEL_ALIPAY:
        return bool(
            _alipay_appid()
            and _alipay_private_key_path()
            and _alipay_public_key_path()
            and os.path.isfile(_alipay_private_key_path())
            and os.path.isfile(_alipay_public_key_path())
            and _notify_url(CHANNEL_ALIPAY)
        )
    return False


def is_configured() -> bool:
    """Any usable channel keeps the subscription checkout alive."""

    return any(is_channel_configured(channel) for channel in CHANNELS)


def channels_configured() -> dict[str, bool]:
    return {channel: is_channel_configured(channel) for channel in CHANNELS}


# ---------------------------------------------------------------------------
# RSA key loading (module-level cache; files are read once per process)
# ---------------------------------------------------------------------------

_KEY_CACHE: dict[str, Any] = {"loaded_at": 0.0, "entries": {}}
_KEY_CACHE_TTL_SECONDS = 300


def _cached_key(path: str, loader):
    now = time.monotonic()
    entry = _KEY_CACHE["entries"].get(path)
    if entry is not None and now - _KEY_CACHE["loaded_at"] < _KEY_CACHE_TTL_SECONDS:
        return entry
    entry = loader(path)
    _KEY_CACHE["entries"][path] = entry
    _KEY_CACHE["loaded_at"] = now
    return entry


def _load_private_key(path: str):
    with open(path, "rb") as handle:
        data = handle.read()
    return serialization.load_pem_private_key(data, password=None)


def _load_public_key(path: str):
    with open(path, "rb") as handle:
        data = handle.read().strip()
    if not data.startswith(b"-----BEGIN"):
        # 支付宝开放平台复制出来的公钥常是裸 base64（无 PEM 头）。
        body = data + b"=" * (-len(data) % 4)
        data = (
            b"-----BEGIN PUBLIC KEY-----\n"
            + b"\n".join(body[i : i + 64] for i in range(0, len(body), 64))
            + b"\n-----END PUBLIC KEY-----\n"
        )
    return serialization.load_pem_public_key(data)


def _wechat_mch_private_key():
    return _cached_key(_wechat_private_key_path(), _load_private_key)


def _alipay_private_key():
    return _cached_key(_alipay_private_key_path(), _load_private_key)


def _alipay_public_key():
    return _cached_key(_alipay_public_key_path(), _load_public_key)


# ---------------------------------------------------------------------------
# WeChat Pay APIv3 client
# ---------------------------------------------------------------------------


def _wechat_authorization(method: str, path: str, body: str) -> str:
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n"
    signature = _wechat_mch_private_key().sign(
        message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
    )
    fields = (
        f'mchid="{_wechat_mchid()}"',
        f'nonce_str="{nonce}"',
        f'signature="{base64.b64encode(signature).decode("ascii")}"',
        f'timestamp="{timestamp}"',
        f'serial_no="{_wechat_cert_serial()}"',
    )
    return f"WECHATPAY2-SHA256-RSA2048 {','.join(fields)}"


def _wechat_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    verify: bool = True,
) -> dict[str, Any]:
    body_text = (
        json.dumps(body, ensure_ascii=False, separators=(",", ":")) if body else ""
    )
    request = url_request.Request(
        f"{_wechat_gateway()}{path}",
        data=body_text.encode("utf-8") if body_text else None,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "vocab-cloud/1.0",
            "Authorization": _wechat_authorization(method, path, body_text),
        },
        method=method,
    )
    response = None
    try:
        with url_request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            text = resp.read().decode("utf-8")
            headers = dict(resp.headers.items())
            response = resp
    except url_request.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise PaymentError(
            GATEWAY_ERROR,
            f"微信支付接口返回 {error.code}：{detail[:200]}",
            status_code=502,
        ) from error
    if verify and response is not None:
        _wechat_verify_response(headers, text)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as error:
        raise PaymentError(
            GATEWAY_ERROR, f"微信支付返回了无法解析的响应：{text[:200]}", status_code=502
        ) from error
    if not isinstance(parsed, dict):
        raise PaymentError(
            GATEWAY_ERROR, f"微信支付返回了意外的响应结构：{text[:200]}", status_code=502
        )
    return parsed


_PLATFORM_CERTS: dict[str, Any] = {"keys": {}, "loaded_at": 0.0}


def _wechat_platform_certs(force: bool = False) -> dict[str, Any]:
    """serial → 平台证书公钥（自动轮换：12h 定期刷新 + 未命中强制刷新）。

    下载 /v3/certificates 本身无法验签（还没有平台证书，鸡生蛋问题，
    官方亦如此：该请求仅信任 TLS），其余响应一律用平台证书公钥验签。
    """

    now = time.monotonic()
    if (
        not force
        and _PLATFORM_CERTS["keys"]
        and now - _PLATFORM_CERTS["loaded_at"] < PLATFORM_CERT_REFRESH_SECONDS
    ):
        return _PLATFORM_CERTS["keys"]
    try:
        payload = _wechat_request("GET", "/v3/certificates", None, verify=False)
        keys: dict[str, Any] = {}
        for item in payload.get("data", []):
            serial = str(item.get("serial_no", ""))
            encrypted = item.get("encrypt_certificate") or {}
            pem = _wechat_decrypt_resource(
                {
                    "ciphertext": encrypted.get("ciphertext", ""),
                    "nonce": encrypted.get("nonce", ""),
                    "associated_data": encrypted.get("associated_data", ""),
                }
            )
            certificate = load_pem_x509_certificate(pem.encode("utf-8"))
            keys[serial] = certificate.public_key()
        if keys:
            _PLATFORM_CERTS["keys"] = keys
            _PLATFORM_CERTS["loaded_at"] = now
    except Exception:  # noqa: BLE001 — 证书刷新失败沿用旧缓存
        logger.warning("wechat platform cert refresh failed", exc_info=True)
    return _PLATFORM_CERTS["keys"]


def _wechat_platform_public_key(serial: str):
    keys = _wechat_platform_certs(force=False)
    if serial in keys:
        return keys[serial]
    keys = _wechat_platform_certs(force=True)
    return keys.get(serial)


def _wechat_verify_response(headers: dict[str, str], text: str) -> None:
    folded = {str(k).lower(): v for k, v in headers.items()}
    serial = folded.get("wechatpay-serial", "")
    timestamp = folded.get("wechatpay-timestamp", "")
    nonce = folded.get("wechatpay-nonce", "")
    signature = folded.get("wechatpay-signature", "")
    if not (serial and timestamp and nonce and signature):
        logger.warning("wechat response missing signature headers; unverified")
        return
    public_key = _wechat_platform_public_key(serial)
    if public_key is None:
        raise PaymentError(
            GATEWAY_ERROR,
            f"微信支付响应使用了未知平台证书（serial={serial}）",
            status_code=502,
        )
    message = f"{timestamp}\n{nonce}\n{text}\n"
    try:
        public_key.verify(
            base64.b64decode(signature),
            message.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError) as error:
        raise PaymentError(
            GATEWAY_ERROR, "微信支付响应验签失败", status_code=502
        ) from error


def _wechat_decrypt_resource(resource: dict[str, Any]) -> str:
    """AES-256-GCM 解密 APIv3 回调 / 证书报文（APIv3 key 即对称密钥）。"""

    key = _wechat_apiv3_key().encode("utf-8")
    nonce = str(resource.get("nonce", "")).encode("utf-8")
    associated = str(resource.get("associated_data", "") or "").encode("utf-8")
    ciphertext = base64.b64decode(str(resource.get("ciphertext", "")))
    return AESGCM(key).decrypt(nonce, ciphertext, associated).decode("utf-8")


def wechat_create_native_payment(
    out_trade_no: str, amount_cents: int, time_expire: datetime
) -> str:
    """POST /v3/pay/transactions/native — returns code_url（二维码内容）。"""

    body: dict[str, Any] = {
        "appid": _wechat_appid(),
        "mchid": _wechat_mchid(),
        "description": _order_title(),
        "out_trade_no": out_trade_no,
        "time_expire": time_expire.astimezone(
            timezone(timedelta(hours=8))
        ).isoformat(timespec="seconds"),
        "notify_url": _notify_url(CHANNEL_WECHAT),
        "amount": {"total": amount_cents, "currency": "CNY"},
    }
    result = _wechat_request("POST", "/v3/pay/transactions/native", body)
    code_url = str(result.get("code_url", ""))
    if not code_url:
        raise PaymentError(GATEWAY_ERROR, "微信支付未返回 code_url", status_code=502)
    return code_url


def wechat_query_order(out_trade_no: str) -> dict[str, Any] | None:
    """GET /v3/pay/transactions/out-trade-no/{no} — normalized status dict.

    Returns None when the gateway does not know the order
    (ORDER_NOT_EXIST — 尚未支付且可能已被关闭).
    """

    path = (
        f"/v3/pay/transactions/out-trade-no/{url_parse.quote(out_trade_no, safe='')}"
        f"?mchid={url_parse.quote(_wechat_mchid(), safe='')}"
    )
    try:
        result = _wechat_request("GET", path)
    except PaymentError as error:
        if "ORDER_NOT_EXIST" in error.message:
            return None
        raise
    trade_state = str(result.get("trade_state", ""))
    amount = result.get("amount") or {}
    return {
        "trade_state": trade_state,
        "amount_total": int(amount.get("total", 0) or 0),
        "transaction_id": result.get("transaction_id"),
        "payer_openid": (result.get("payer") or {}).get("openid"),
    }


def wechat_close_order(out_trade_no: str) -> None:
    """POST /v3/pay/transactions/out-trade-no/{no}/close（幂等，可重入）。"""

    path = (
        f"/v3/pay/transactions/out-trade-no/{url_parse.quote(out_trade_no, safe='')}"
        "/close"
    )
    _wechat_request("POST", path, {"mchid": _wechat_mchid()})


def verify_wechat_callback(
    headers: dict[str, str], body: str
) -> dict[str, Any] | None:
    """验签并解密微信支付回调，返回明文 resource dict；失败返回 None。"""

    # HTTP 头大小写不敏感（ASGI 侧拿到的常是小写 key），统一折叠后取值。
    folded = {str(k).lower(): v for k, v in headers.items()}
    serial = str(folded.get("wechatpay-serial", ""))
    timestamp = str(folded.get("wechatpay-timestamp", ""))
    nonce = str(folded.get("wechatpay-nonce", ""))
    signature = str(folded.get("wechatpay-signature", ""))
    if not (serial and timestamp and nonce and signature):
        return None
    public_key = _wechat_platform_public_key(serial)
    if public_key is None:
        logger.warning("wechat callback with unknown platform serial %s", serial)
        return None
    message = f"{timestamp}\n{nonce}\n{body}\n"
    try:
        public_key.verify(
            base64.b64decode(signature),
            message.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError):
        return None
    try:
        payload = json.loads(body)
        resource = payload.get("resource") or {}
        decrypted = _wechat_decrypt_resource(resource)
        parsed = json.loads(decrypted)
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001 — 报文异常一律视为验签失败
        logger.warning("wechat callback body could not be decrypted", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Alipay client (RSA2, page / wap pay, notify, query, close)
# ---------------------------------------------------------------------------


def _alipay_common_params(method: str, biz_content: dict[str, Any]) -> dict[str, str]:
    # 支付宝要求 timestamp 为北京时间 yyyy-MM-dd HH:mm:ss。
    now_sh = _now().astimezone(timezone(timedelta(hours=8)))
    params: dict[str, str] = {
        "app_id": _alipay_appid(),
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": now_sh.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "notify_url": _notify_url(CHANNEL_ALIPAY),
    }
    return_url = str(biz_content.pop("return_url", "") or "")
    if return_url:
        params["return_url"] = return_url
    params["biz_content"] = json.dumps(
        biz_content, ensure_ascii=False, separators=(",", ":")
    )
    return params


def _alipay_sign_content(params: dict[str, str]) -> str:
    items = sorted(
        (key, value)
        for key, value in params.items()
        if key not in ("sign", "sign_type") and value not in (None, "")
    )
    return "&".join(f"{key}={value}" for key, value in items)


def _alipay_sign(params: dict[str, str]) -> str:
    signature = _alipay_private_key().sign(
        _alipay_sign_content(params).encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def alipay_verify_signature(params: dict[str, str]) -> bool:
    """验签支付宝异步通知（支付宝公钥，RSA2）。缺 sign 一律 fail closed。"""

    supplied = str(params.get("sign", "") or "")
    if not supplied:
        return False
    try:
        _alipay_public_key().verify(
            base64.b64decode(supplied),
            _alipay_sign_content(params).encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError):
        return False
    return True


def alipay_create_payment(
    out_trade_no: str, amount_cents: int, time_expire: datetime, *, is_mobile: bool
) -> str:
    """alipay.trade.page.pay / wap.pay — returns the signed gateway URL."""

    expire_sh = time_expire.astimezone(timezone(timedelta(hours=8)))
    biz: dict[str, Any] = {
        "out_trade_no": out_trade_no,
        "total_amount": f"{amount_cents / 100:.2f}",
        "subject": _order_title(),
        "time_expire": expire_sh.strftime("%Y-%m-%d %H:%M:%S"),
        "product_code": "QUICK_WAP_WAY" if is_mobile else "FAST_INSTANT_TRADE_PAY",
    }
    return_url = os.environ.get("ALIPAY_RETURN_URL", "").strip()
    if return_url:
        biz["return_url"] = return_url
    method = "alipay.trade.wap.pay" if is_mobile else "alipay.trade.page.pay"
    params = _alipay_common_params(method, biz)
    params["sign"] = _alipay_sign(params)
    return f"{_alipay_gateway()}?{url_parse.urlencode(params)}"


def _alipay_gateway_post(method: str, biz_content: dict[str, Any]) -> dict[str, Any]:
    params = _alipay_common_params(method, biz_content)
    params["sign"] = _alipay_sign(params)
    request = url_request.Request(
        _alipay_gateway(),
        data=url_parse.urlencode(params).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with url_request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            text = resp.read().decode("utf-8")
    except url_request.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise PaymentError(
            GATEWAY_ERROR,
            f"支付宝网关返回 {error.code}：{detail[:200]}",
            status_code=502,
        ) from error
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as error:
        raise PaymentError(
            GATEWAY_ERROR, f"支付宝返回了无法解析的响应：{text[:200]}", status_code=502
        ) from error
    node_key = f"{method.replace('.', '_')}_response"
    node = parsed.get(node_key)
    if not isinstance(node, dict):
        raise PaymentError(
            GATEWAY_ERROR, f"支付宝返回了意外的响应结构：{text[:200]}", status_code=502
        )
    # 响应验签：sign 内容是响应节点按返回顺序的紧凑 JSON。
    sign = str(parsed.get("sign", "") or "")
    if sign:
        content = json.dumps(node, ensure_ascii=False, separators=(",", ":"))
        try:
            _alipay_public_key().verify(
                base64.b64decode(sign),
                content.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (InvalidSignature, ValueError) as error:
            raise PaymentError(
                GATEWAY_ERROR, "支付宝响应验签失败", status_code=502
            ) from error
    if str(node.get("code", "")) != "10000":
        raise PaymentError(
            GATEWAY_ERROR,
            f"支付宝接口失败（{node.get('code')}）："
            f"{node.get('sub_msg') or node.get('msg', '')}",
            status_code=502,
        )
    return node


def alipay_query_order(out_trade_no: str) -> dict[str, Any] | None:
    """alipay.trade.query — normalized status dict or None when unknown."""

    node = _alipay_gateway_post("alipay.trade.query", {"out_trade_no": out_trade_no})
    trade_status = str(node.get("trade_status", ""))
    if not trade_status:
        return None
    if trade_status == "TRADE_CLOSED":
        return {"trade_status": "TRADE_CLOSED"}
    return {
        "trade_status": trade_status,
        "total_amount": str(node.get("total_amount", "")),
        "trade_no": node.get("trade_no"),
    }


def alipay_close_order(out_trade_no: str) -> None:
    _alipay_gateway_post("alipay.trade.close", {"out_trade_no": out_trade_no})


# ---------------------------------------------------------------------------
# Order model helpers
# ---------------------------------------------------------------------------


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
    channel query API — V3-03 验收 4), so a user parked on the checkout
    page can recover even when the notify never arrived.
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
    """收银台「取消支付」(订单级语义)：本地关单 + 网关 best-effort 关单。"""

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
    _close_at_gateway_best_effort(row)
    return _order_to_view(row)


def _close_at_gateway_best_effort(row) -> None:
    """网关侧关单：失败只记日志（TTL 兜底会自动关单，不阻塞用户）。"""

    if row is None:
        return
    channel = str(row["channel"])
    out_trade_no = str(row["out_trade_no"])
    try:
        if channel == CHANNEL_WECHAT:
            wechat_close_order(out_trade_no)
        elif channel == CHANNEL_ALIPAY:
            alipay_close_order(out_trade_no)
    except Exception:  # noqa: BLE001 — best effort
        logger.warning(
            "gateway close failed for %s (%s), TTL will settle it",
            out_trade_no,
            channel,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# 下单
# ---------------------------------------------------------------------------


def create_order(
    user: dict[str, object],
    plan: str,
    channel: str,
    *,
    is_mobile: bool = False,
) -> dict[str, object]:
    """``POST /api/subscription/orders`` — snapshot the amount, place it.

    The backend computes 应收金额 from the user's subscription snapshot
    (V3-02 交互规则 4) and writes it into orders; the callback must
    match it exactly before anything is confirmed. Gateway failure →
    PaymentError and NO order row is written (不产生脏订单, V3-03 验收 5).
    """

    from app import subscription as subscription_module
    from app.db import connect

    if bool(user["is_super"]):
        raise PaymentError(SUPER_CONFLICT, "super 账号无需订阅", status_code=409)

    if channel not in CHANNELS:
        raise PaymentError(
            PAYMENT_CHANNEL_INVALID,
            f"未知支付渠道：{channel or '(空)'}",
            status_code=400,
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

    if not is_channel_configured(channel):
        raise PaymentError(
            PAYMENT_NOT_CONFIGURED,
            f"支付通道尚未开通：管理员还未配置{_CHANNEL_LABELS[channel]}密钥",
            status_code=503,
        )

    # 重复点击下单的幂等口径：TTL 内同档位 pending 订单直接复用
    # （渠道一致才复用——不同渠道各开各的单）。
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
        if (
            _now() - created < timedelta(minutes=order_ttl_minutes())
            and str(existing["channel"]) == channel
        ):
            return _order_to_view(existing)

    out_trade_no = f"VL{_now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:10]}"
    now = _now()
    time_expire = now + timedelta(minutes=order_ttl_minutes())
    if channel == CHANNEL_WECHAT:
        pay_qr_url = wechat_create_native_payment(out_trade_no, amount_cents, time_expire)
        pay_url = None
    else:
        pay_url = alipay_create_payment(
            out_trade_no, amount_cents, time_expire, is_mobile=is_mobile
        )
        pay_qr_url = None

    now_iso = _iso(now)
    order_id = uuid.uuid4().hex
    with connect() as connection:
        connection.execute(
            """
            insert into orders (id, out_trade_no, user_id, plan, amount_cents,
                                currency, status, channel, pay_url, pay_qr_url,
                                created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                out_trade_no,
                str(user["id"]),
                plan,
                amount_cents,
                currency,
                channel,
                pay_url,
                pay_qr_url,
                now_iso,
                now_iso,
            ),
        )
        row = connection.execute(
            "select * from orders where out_trade_no = ?", (out_trade_no,)
        ).fetchone()
    logger.info(
        "order %s created for user %s plan=%s channel=%s amount=%d cents",
        out_trade_no,
        user["id"],
        plan,
        channel,
        amount_cents,
    )
    return _order_to_view(row)


# ---------------------------------------------------------------------------
# 支付确认（回调 / 对账共用）
# ---------------------------------------------------------------------------


def confirm_payment(
    out_trade_no: str,
    *,
    amount_cents: int,
    source: str,
    transaction_id: str | None = None,
) -> str:
    """Confirm one order as paid (idempotent).

    Returns one of: ``confirmed`` / ``already_paid`` / ``unknown_order`` /
    ``not_pending`` / ``amount_mismatch``. Only ``confirmed`` and
    ``already_paid`` (幂等重放) may ever answer success to the gateway —
    and neither writes twice: the pending → paid transition + the
    subscription row both live inside one BEGIN IMMEDIATE transaction.

    金额不符不确认入账 (V3-02 验收 6): 各渠道回报金额统一折算成
    「分」后与下单快照精确比对。
    """

    from app import subscription as subscription_module
    from app.db import connect

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
        if int(amount_cents) != int(row["amount_cents"]):
            logger.error(
                "order %s amount mismatch: callback %d cents vs"
                " snapshot %d cents — NOT confirmed",
                out_trade_no,
                int(amount_cents),
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
        subscription_module.activate_subscription(
            connection,
            user_id=str(row["user_id"]),
            plan=str(row["plan"]),
            amount_cents=int(row["amount_cents"]),
            source=source,
            order_no=out_trade_no,
            now=_now(),
        )
    logger.info(
        "order %s confirmed paid via %s (%d cents), subscription activated",
        out_trade_no,
        source,
        amount_cents,
    )
    return "confirmed"


# ---------------------------------------------------------------------------
# Callback archive (原始报文留档 — 任何路径都不能丢)
# ---------------------------------------------------------------------------


def _archive_callback(
    out_trade_no: str | None, payload_text: str, result: str
) -> None:
    """Archive one callback verbatim; archive failures never propagate.

    QA P2（2026-09-06）：旧实现的 handle_notify 处理段一旦抛异常，
    回调报文既没入档也没应答，等于白丢。现在归档独立于处理结果，
    处理异常时也先落档再应答失败。
    """

    from app.db import connect

    try:
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
                    payload_text,
                    result,
                    _iso(_now()),
                ),
            )
    except Exception:  # noqa: BLE001 — 留档失败只记日志，不影响应答
        logger.exception(
            "payment callback archive failed (%s, %s)", out_trade_no, result
        )


def _extract_out_trade_no(payload: dict[str, Any]) -> str:
    return str(payload.get("out_trade_no") or payload.get("trade_order_id") or "")


# ---------------------------------------------------------------------------
# 微信支付回调 (POST /api/payment/notify/wechat)
# ---------------------------------------------------------------------------


def handle_wechat_notify(
    headers: dict[str, str], body: str
) -> tuple[dict[str, Any], int]:
    """WeChat Pay APIv3 callback.

    Success answers 200 + ``{"code": "SUCCESS"}``; anything else is a
    non-200 JSON failure so WeChat retries. Every payload is archived
    verbatim, **including** the ones that raise mid-processing (QA P2).
    """

    try:
        payload = verify_wechat_callback(headers, body)
        if payload is None:
            _archive_callback(None, body, "rejected_bad_signature")
            logger.warning("wechat notify rejected: bad signature")
            return {"code": "FAIL", "message": "签名校验失败"}, 401

        out_trade_no = _extract_out_trade_no(payload)
        trade_state = str(payload.get("trade_state", ""))
        amount = payload.get("amount") or {}
        reported_cents = int(amount.get("total", 0) or 0)

        if trade_state != "SUCCESS":
            # REFUND / CLOSED / PAYERROR 等 — 留档即可，无需重试。
            _archive_callback(
                out_trade_no, body, f"ignored_trade_state_{trade_state or 'missing'}"
            )
            return {"code": "SUCCESS", "message": "成功"}, 200

        result = confirm_payment(
            out_trade_no,
            amount_cents=reported_cents,
            source=CHANNEL_WECHAT,
            transaction_id=str(payload.get("transaction_id") or "") or None,
        )
        if result in ("confirmed", "already_paid"):
            # 幂等：重复通知不重复续期，直接 success 止住重试。
            _archive_callback(out_trade_no, body, result)
            return {"code": "SUCCESS", "message": "成功"}, 200
        _archive_callback(out_trade_no, body, f"rejected_{result}")
        logger.warning("wechat notify rejected: %s (%s)", result, out_trade_no)
        return {"code": "FAIL", "message": f"订单处理失败：{result}"}, 400
    except Exception:  # noqa: BLE001 — 处理异常也要落档并应答失败（QA P2）
        logger.exception("wechat notify processing crashed")
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            parsed = {}
        _archive_callback(_extract_out_trade_no(parsed), body, "exception")
        return {"code": "FAIL", "message": "处理异常"}, 500


# ---------------------------------------------------------------------------
# 支付宝回调 (POST /api/payment/notify/alipay)
# ---------------------------------------------------------------------------


def handle_alipay_notify(form: dict[str, str]) -> tuple[str, int]:
    """Alipay async notify (application/x-www-form-urlencoded).

    Answers plain text ``success`` only when the payload is fully
    processed or idempotently replayed; anything else answers ``fail``
    so Alipay retries. Every payload is archived verbatim — including
    the ones that raise mid-processing (QA P2).
    """

    body_text = json.dumps(dict(form), ensure_ascii=False, sort_keys=True)
    try:
        if not alipay_verify_signature(form):
            _archive_callback(None, body_text, "rejected_bad_signature")
            logger.warning("alipay notify rejected: bad signature")
            return "fail", 200

        if str(form.get("app_id", "")) != _alipay_appid():
            _archive_callback(
                str(form.get("out_trade_no", "")), body_text, "rejected_app_id"
            )
            return "fail", 200

        out_trade_no = _extract_out_trade_no(form)
        trade_status = str(form.get("trade_status", ""))
        if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            # WAIT_BUYER_PAY / TRADE_CLOSED 等 — 留档即可，无需重试。
            _archive_callback(
                out_trade_no, body_text, f"ignored_status_{trade_status or 'missing'}"
            )
            return "success", 200

        try:
            reported_cents = int(round(float(form.get("total_amount", "")) * 100))
        except (TypeError, ValueError):
            reported_cents = -1  # 金额不合法 → amount_mismatch 拒绝入账

        result = confirm_payment(
            out_trade_no,
            amount_cents=reported_cents,
            source=CHANNEL_ALIPAY,
            transaction_id=str(form.get("trade_no") or "") or None,
        )
        if result in ("confirmed", "already_paid"):
            _archive_callback(out_trade_no, body_text, result)
            return "success", 200
        _archive_callback(out_trade_no, body_text, f"rejected_{result}")
        logger.warning("alipay notify rejected: %s (%s)", result, out_trade_no)
        return "fail", 200
    except Exception:  # noqa: BLE001 — 处理异常也要落档并应答失败（QA P2）
        logger.exception("alipay notify processing crashed")
        _archive_callback(_extract_out_trade_no(form), body_text, "exception")
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


def reconcile_pending_orders(
    user_id: str | None = None, *, limit: int = 50
) -> dict[str, object]:
    """Query the channels for pending orders and reconcile them.

    - 漏单自动补单：channel says paid → confirm (amount-checked, idempotent);
    - 超时关单：pending 且超 TTL → closed（网关侧下单时已带 time_expire，
      到点自动关闭，本地标记兜底）;
    - Gateway/网络故障不会抛出（记录后跳过）— 对账是兜底，不能反过来
      拖垮业务路径。
    """

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
        channel = str(row["channel"])
        out_trade_no = str(row["out_trade_no"])
        if not is_channel_configured(channel):
            # 通道未配置（或密钥下线）→ 该渠道订单本轮不查，跳过。
            continue
        try:
            if channel == CHANNEL_WECHAT:
                result = wechat_query_order(out_trade_no)
                if result is None or str(result.get("trade_state")) != "SUCCESS":
                    continue
                reported_cents = int(result.get("amount_total") or 0)
                transaction_id = result.get("transaction_id")
            elif channel == CHANNEL_ALIPAY:
                result = alipay_query_order(out_trade_no)
                if result is None:
                    continue
                trade_status = str(result.get("trade_status", ""))
                if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
                    continue
                try:
                    reported_cents = int(
                        round(float(result.get("total_amount") or "0") * 100)
                    )
                except (TypeError, ValueError):
                    continue
                transaction_id = result.get("trade_no")
            else:
                continue
        except Exception:  # noqa: BLE001 — 单笔查询失败跳过该笔
            logger.warning(
                "reconcile: gateway query failed for %s (%s)",
                out_trade_no,
                channel,
                exc_info=True,
            )
            continue

        outcome = confirm_payment(
            out_trade_no,
            amount_cents=reported_cents,
            source=channel,
            transaction_id=transaction_id,
        )
        if outcome in ("confirmed", "already_paid"):
            confirmed += 1
        elif outcome == "amount_mismatch":
            logger.error(
                "reconcile: amount mismatch for %s — order NOT confirmed",
                out_trade_no,
            )
    return {"skipped": False, "confirmed": confirmed, "closed": closed, "checked": checked}


def reconcile_user_pending_orders(user_id: str) -> dict[str, object]:
    """Reconciliation scoped to one user (called from /me — 兜底补单)."""

    return reconcile_pending_orders(user_id)
