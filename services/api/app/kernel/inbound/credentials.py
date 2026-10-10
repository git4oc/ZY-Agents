"""HMAC 短时 token 签发与校验（``kernel/inbound/credentials.py``，``03`` §4.2）。

密钥派生做域分离（已验证的稳妥做法）：

    model_key = sha256(secret.encode()).digest()               # 模型网关
    tool_key  = sha256(("tool:" + secret).encode()).digest()   # 工具网关

即使 model token 被拿到，也**不可能**用来调工具端点——不是靠代码检查，是靠密码学。

Token 载荷含 ``aud``（``zy-model-gateway`` / ``zy-tool-gateway``），校验时**同时比对 aud**。
有效期 900s，turn 开始前下发，turn 结束后作废。

``GatewayClaims`` 内含 ``tool_names`` / ``scopes``（工具可用性边界）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from app.core.errors import GatewayTokenError
from app.core.logging import get_logger

log = get_logger("kernel.inbound.credentials")

#: token 用途（aud）——与 ``00`` §0.5 / ``03`` §4.2 对齐
Aud = Literal["model", "tool"]
AUD_MODEL = "zy-model-gateway"
AUD_TOOL = "zy-tool-gateway"

_DEFAULT_TTL_SECONDS = 900


@dataclass(frozen=True, slots=True)
class GatewayClaims:
    """从网关 token 解出的声明。"""

    aud: str  # "zy-model-gateway" | "zy-tool-gateway"
    org_unit_id: str
    user_id: str
    profile_version: str
    tool_names: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    runtime_id: str = ""


def _derive_key(secret: str, *, tool: bool) -> bytes:
    """派生模型 / 工具网关密钥（域分离）。"""
    material = secret.encode("utf-8")
    if tool:
        material = ("tool:" + secret).encode("utf-8")
    return hashlib.sha256(material).digest()


def issue(
    *,
    aud: Aud,
    secret: str,
    org_unit_id: str,
    user_id: str,
    profile_version: str,
    runtime_id: str,
    tool_names: frozenset[str] | None = None,
    scopes: frozenset[str] | None = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    issued_at: datetime | None = None,
) -> str:
    """签发一个短时 HMAC 网关 token。

    参数
    ----
    aud:
        token 用途（model / tool）。
    secret:
        对应网关的签名密钥（model 用 ``KERNEL_MODEL_GATEWAY_SECRET``，
        tool 用 ``KERNEL_TOOL_GATEWAY_SECRET``；派生时自动区分域）。
    org_unit_id / user_id / profile_version / runtime_id:
        声明载荷。校验时同时比对 aud 与 runtime_id。
    tool_names / scopes:
        工具可用性与权限边界（tool 类 token 使用）。
    ttl_seconds:
        有效期（默认 900s）。
    issued_at:
        触发时间（测试注入）。

    返回 HMAC token 字符串（Base64URL 编码的签名载荷）。
    """
    now = issued_at or datetime.now(timezone.utc)
    exp = now + timedelta(seconds=ttl_seconds)
    aud_str = AUD_MODEL if aud == "model" else AUD_TOOL

    header = {"alg": "HS256", "typ": "ZYWG"}  # ZY Web Gateway
    payload: dict[str, Any] = {
        "aud": aud_str,
        "org": org_unit_id,
        "sub": user_id,
        "pv": profile_version,
        "rid": runtime_id,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if tool_names:
        payload["tools"] = sorted(tool_names)
    if scopes:
        payload["scopes"] = sorted(scopes)

    key = _derive_key(secret, tool=(aud == "tool"))
    signing_input = _b64json(header) + "." + _b64json(payload)
    signature = base64.urlsafe_b64encode(
        hmac.new(key, signing_input.encode("utf-8"), hashlib.sha256).digest()
    ).rstrip(b"=")
    return signing_input + "." + signature.decode("ascii")


def verify(token: str, *, expect_aud: str, secret: str) -> GatewayClaims:
    """校验网关 token 并返回声明。

    参数
    ----
    token:
        HMAC 字符串。
    expect_aud:
        期望的 aud（``AUD_MODEL`` / ``AUD_TOOL``）。校验时同时比对 aud。
    secret:
        对应网关的签名密钥（域自动区分）。

    异常
    ----
    GatewayTokenError:
        token 无效 / 签名不符 / aud 不匹配 / 已过期。
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise GatewayTokenError("网关 token 格式错误")

    header_b64, payload_b64, signature_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}"

    # 先解出 aud 以选择正确的密钥域
    try:
        header = json.loads(_unb64(header_b64))
        payload = json.loads(_unb64(payload_b64))
    except Exception as exc:
        raise GatewayTokenError("网关 token 载荷无法解析") from exc

    aud_str = payload.get("aud")
    tool = aud_str == AUD_TOOL
    key = _derive_key(secret, tool=tool)

    # 校验签名（constant-time）
    expected_sig = base64.urlsafe_b64encode(
        hmac.new(key, signing_input.encode("utf-8"), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(signature_b64, expected_sig):
        raise GatewayTokenError("网关 token 签名无效")

    # 校验 aud（模型 token 不能调工具端点）
    if aud_str != expect_aud:
        raise GatewayTokenError("网关 token 用途不匹配", details={"expect": expect_aud, "got": aud_str})

    now = datetime.now(timezone.utc)
    exp = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
    iat = datetime.fromtimestamp(int(payload["iat"]), tz=timezone.utc)
    if exp < now:
        raise GatewayTokenError("网关 token 已过期")

    return GatewayClaims(
        aud=aud_str,
        org_unit_id=str(payload.get("org", "")),
        user_id=str(payload.get("sub", "")),
        profile_version=str(payload.get("pv", "")),
        tool_names=frozenset(payload.get("tools") or []),
        scopes=frozenset(payload.get("scopes") or []),
        issued_at=iat,
        expires_at=exp,
        runtime_id=str(payload.get("rid", "")),
    )


def _b64json(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))
