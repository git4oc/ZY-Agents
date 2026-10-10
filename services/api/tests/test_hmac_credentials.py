"""HMAC 网关凭据测试（``kernel/inbound/credentials.py``，``03`` §4.2 / ``S-15``）。

**不变量**（纯函数，无需 Mongo/网络）：

- 域分离：模型 token 用 ``sha256(secret)``，工具 token 用 ``sha256("tool:"+secret)``。
  即便 model token 泄露，也**不可能**拿去调用工具端点（``03`` §4.2）。
- aud 双校验：verify 时同时比对 aud 与签名。
- 过期拒绝：exp < now → GatewayTokenError。
- 格式错误 / 签名不符 → GatewayTokenError。
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone

import tests._zytest_stubs  # noqa: F401

from app.core.errors import GatewayTokenError
from app.kernel.inbound.credentials import (
    AUD_MODEL,
    AUD_TOOL,
    _derive_key,
    issue,
    verify,
)

MODEL_SECRET = "model-secret-abc"
TOOL_SECRET = "tool-secret-xyz"


def _now() -> datetime:
    """取实时时钟——verify() 用真实时钟比对 exp，测试必须相对当下签发。"""
    return datetime.now(timezone.utc)


class HmacDomainSeparationTest(unittest.TestCase):
    def test_key_derivation_is_separated(self):
        """模型/工具密钥必须由中国不同素材派生（sha256 vs sha256('tool:'+..)）。"""
        model_key = _derive_key(MODEL_SECRET, tool=False)
        tool_key = _derive_key(MODEL_SECRET, tool=True)
        self.assertNotEqual(model_key, tool_key)
        # 工具密钥来自 "tool:"+secret
        self.assertEqual(tool_key, _derive_key("tool:" + MODEL_SECRET, tool=False))

    def test_model_token_cannot_be_used_on_tool_gateway(self):
        """用工具 secret 去校验模型签发的 token，必须失败（域分离，``S-15``）。"""
        token = issue(
            aud="model",
            secret=MODEL_SECRET,
            org_unit_id="ou-1",
            user_id="u1",
            profile_version="rp-x",
            runtime_id="rt-1",
            issued_at=_now(),
        )
        # 1) 签名密钥域不同 → 直接失败
        with self.assertRaises(GatewayTokenError):
            verify(token, expect_aud=AUD_MODEL, secret=TOOL_SECRET)
        # 2) 即便用对密钥，aud 不匹配也要失败（模型 token 不能调工具端点）
        with self.assertRaises(GatewayTokenError):
            verify(token, expect_aud=AUD_TOOL, secret=MODEL_SECRET)

    def test_tool_token_roundtrip_with_names_and_scopes(self):
        """工具 token 携带 tool_names/scopes 边界（``03`` §4.2）。"""
        secret = "shared"
        token = issue(
            aud="tool",
            secret=secret,
            org_unit_id="ou-9",
            user_id="u9",
            profile_version="rp-y",
            runtime_id="rt-9",
            tool_names=frozenset(["http_fetch", "shell"]),
            scopes=frozenset(["read"]),
            issued_at=_now(),
        )
        claims = verify(token, expect_aud=AUD_TOOL, secret=secret)
        self.assertEqual(claims.aud, AUD_TOOL)
        self.assertEqual(claims.org_unit_id, "ou-9")
        self.assertEqual(claims.user_id, "u9")
        self.assertEqual(claims.profile_version, "rp-y")
        self.assertEqual(claims.runtime_id, "rt-9")
        self.assertEqual(claims.tool_names, frozenset(["http_fetch", "shell"]))
        self.assertEqual(claims.scopes, frozenset(["read"]))

    def test_issued_at_and_expiry(self):
        """ttl 注入后 exp = iat + ttl（``_DEFAULT_TTL_SECONDS=900``）。"""
        secret = "s1"
        iat = _now()
        token = issue(
            aud="model",
            secret=secret,
            org_unit_id="ou-1",
            user_id="u1",
            profile_version="rp-x",
            runtime_id="rt-1",
            ttl_seconds=60,
            issued_at=iat,
        )
        claims = verify(token, expect_aud=AUD_MODEL, secret=secret)
        # issue()/verify() 以「整秒」timestamp 存取，比较需截断到秒
        iat_sec = iat.replace(microsecond=0)
        self.assertEqual(claims.issued_at, iat_sec)
        self.assertEqual(claims.expires_at, iat_sec + timedelta(seconds=60))

    def test_expired_token_rejected(self):
        """exp 已过 → 拒绝（``S-15``）。"""
        secret = "s-exp"
        # 用「已过去的签发时刻」+ 很短 ttl → 相对真实时钟必然已过期
        iat = _now() - timedelta(seconds=120)
        token = issue(
            aud="model",
            secret=secret,
            org_unit_id="ou-1",
            user_id="u1",
            profile_version="rp-x",
            runtime_id="rt-1",
            ttl_seconds=30,
            issued_at=iat,  # 签发于 120s 前，ttl 仅 30s → 已过期 90s
        )
        with self.assertRaises(GatewayTokenError):
            verify(token, expect_aud=AUD_MODEL, secret=secret)

    def test_bad_signature_rejected(self):
        """篡改签名 → 拒绝（constant-time compare）。"""
        secret = "s-sig"
        token = issue(
            aud="model",
            secret=secret,
            org_unit_id="ou-1",
            user_id="u1",
            profile_version="rp-x",
            runtime_id="rt-1",
            issued_at=_now(),
        )
        head, pay, sig = token.split(".")
        tampered = f"{head}.{pay}.AAAAAA"
        with self.assertRaises(GatewayTokenError):
            verify(tampered, expect_aud=AUD_MODEL, secret=secret)

    def test_malformed_token_rejected(self):
        with self.assertRaises(GatewayTokenError):
            verify("not-a-token", expect_aud=AUD_MODEL, secret="x")

    def test_payload_tamper_rejected(self):
        """改动载荷（如换 user_id）必然导致签名失效。"""
        secret = "s-tamper"
        token = issue(
            aud="model",
            secret=secret,
            org_unit_id="ou-1",
            user_id="u1",
            profile_version="rp-x",
            runtime_id="rt-1",
            issued_at=_now(),
        )
        # 解码并替换 sub，再用错误密钥重签 → 校验必须失败
        import base64
        import hmac
        import hashlib
        import json

        head_b64, pay_b64, _ = token.split(".")
        payload = json.loads(
            base64.urlsafe_b64decode(pay_b64 + "=" * (-len(pay_b64) % 4))
        )
        payload["sub"] = "attacker"
        new_pay = base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        signing_input = f"{head_b64}.{new_pay}"
        # 攻击者不知道真实密钥，随便用一个 key 签
        fake_key = b"attacker-key"
        new_sig = base64.urlsafe_b64encode(
            hmac.new(fake_key, signing_input.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        forged = f"{signing_input}.{new_sig}"
        with self.assertRaises(GatewayTokenError):
            verify(forged, expect_aud=AUD_MODEL, secret=secret)
        # 且用真实密钥校验原 token 仍成功（证明攻击者无法改动载荷）
        claims = verify(token, expect_aud=AUD_MODEL, secret=secret)
        self.assertEqual(claims.user_id, "u1")


if __name__ == "__main__":
    unittest.main()
