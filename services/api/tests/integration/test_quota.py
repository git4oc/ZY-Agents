"""配额集成测试（quota 端点形状 / 429 预检 + 审计落库）。

守护的真实缺陷面：

- 超额预检必须在流式响应构造**之前**拒绝（``S-44``：429 JSON，不能截断流）。
- 拒绝必须落审计（``S-45``：拒绝也是必须落审计的写事件，error_code=quota_exceeded）。
- 默认月配额未启用（=0 不限）时整块 429 用例 skip，不误报。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests.integration.base import (
    IntegrationTestCase,
    admin_token,
    gateway_claims_id,
    gateway_secret,
    http,
    member_token,
    mongo_db,
)


def _period() -> str:
    """当前配额周期 YYYY-MM（与 ``governance/quota.py`` 同口径）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m")


class QuotaEndpointTest(IntegrationTestCase):
    """管理端配额端点形状 + 权限。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin = admin_token()
        cls.member = member_token()

    def test_quota_endpoint_shape(self):
        """admin 读配额 → 200 且含三计数字段（未用时全 0）。"""
        st, body = http("GET", "/api/v1/admin/quota", self.admin)
        self.assertEqual(st, 200, f"quota 端点失败: {st} {body}")
        for key in ("model_tokens_prompt", "model_tokens_completion", "tool_calls"):
            self.assertIn(key, body, f"quota 响应缺 {key}: {body}")

    def test_quota_endpoint_member_not_allowed(self):
        """member 读管理端配额 → 非 200（权限守卫）。"""
        st, _body = http("GET", "/api/v1/admin/quota", self.member)
        self.assertNotEqual(st, 200, "member 竟然可读管理端配额")


class QuotaPrecheck429Test(IntegrationTestCase):
    """超额预检 429 + 审计落库（internal HMAC 路径，须依赖真实网关 secret 与种子用户）。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from app.settings import get_settings

        cls.quota_monthly = int(get_settings().default_model_token_quota_monthly or 0)
        if cls.quota_monthly <= 0:
            raise unittest.SkipTest("默认月配额未启用（=0 不限），429 路径不可达")
        cls.secret = gateway_secret("model")  # model 端点用独立的模型网关 secret（域分离）
        if not cls.secret:
            raise unittest.SkipTest("services/api/.env 无 kernel_tool_gateway_secret")

    def setUp(self) -> None:
        super().setUp()
        self.org_id, self.user_id = gateway_claims_id("admin")
        self.period = _period()
        self._key = {"org_unit_id": self.org_id, "user_id": self.user_id, "period": self.period}
        db = mongo_db()
        self._original = db.quota_usage.find_one(self._key)

    def tearDown(self) -> None:
        super().tearDown()
        try:
            db = mongo_db()
            if self._original is not None:
                db.quota_usage.replace_one({"_id": self._original["_id"]}, self._original)
            else:
                db.quota_usage.delete_one(self._key)
            db.audit_logs.delete_many({"session_id": "itest-quota-429", "action": "model.generate"})
        except Exception:
            pass

    def test_precheck_429_with_audit(self):
        """超额 → 429 JSON + audit_logs 落 denied（不静默放行）。"""
        db = mongo_db()
        db.quota_usage.update_one(
            self._key,
            {"$set": {"model_tokens_prompt": 10**9, "model_tokens_completion": 0, "tool_calls": 0}},
            upsert=True,
        )

        from app.kernel.inbound import credentials

        gw_token = credentials.issue(
            aud="model",
            secret=self.secret,
            org_unit_id=self.org_id,
            user_id=self.user_id,
            profile_version="",
            runtime_id="itest-quota",
        )
        st, body = http(
            "POST", "/internal/kernel/model/generate", gw_token,
            {
                "session_id": "itest-quota-429",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        self.assertEqual(st, 429, f"超额预检应 429: {st} {body}")
        row = db.audit_logs.find_one(
            {"session_id": "itest-quota-429", "action": "model.generate", "result": "denied"}
        )
        self.assertIsNotNone(row, "超额拒绝未落审计")
        if row is not None:
            self.assertEqual(row.get("error_code"), "quota_exceeded")


if __name__ == "__main__":
    unittest.main()
