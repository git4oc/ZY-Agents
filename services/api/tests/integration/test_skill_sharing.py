"""技能分享集成测试（链接分享 / 直分享 / 收件箱 / 接受 duplicate 语义 / 吊销）。

守护的真实缺陷面：

- 后端缺失 ``skill-share-inbox`` 路由导致前端 404 刷屏（本套件防回归）。
- accept 的 org 共享 duplicate 语义：同 key 已存在（即源技能本体）→ 200
  ``duplicate=True``，不得再 403（曾因 existing 命中即 403 拒绝）。
- 吊销后 token 预览/安装 → 404。
"""

from __future__ import annotations

import unittest

from tests.integration.base import (
    IntegrationTestCase,
    admin_token,
    http,
    make_skill_zip,
    member_token,
    mongo_db,
    multipart_zip,
    unique_suffix,
)

INSTALL_PATH = "/api/v1/admin/skills/install"


class SkillSharingTest(IntegrationTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin = admin_token()
        cls.member = member_token()

    def setUp(self) -> None:
        super().setUp()
        # 每用例独立技能（unique key）：zip 安装同时建 skills + skill_versions（快照依赖）
        self.skill_key = f"itest-share-{unique_suffix()}"
        st, body = multipart_zip(INSTALL_PATH, self.admin, make_skill_zip(self.skill_key), f"{self.skill_key}.zip")
        self.assertEqual(st, 200, f"前置技能安装失败: {st} {body}")
        self.skill_id = str(body["id"])
        self._cleanup_oids: list[tuple[str, dict]] = []

    def tearDown(self) -> None:
        super().tearDown()
        try:
            db = mongo_db()
            for coll, query in reversed(self._cleanup_oids):
                getattr(db, coll).delete_many(query)
            # deliveries 无 skill_key 冗余字段 → 经 share_id 关联清理
            share_ids = [d.get("share_id") for d in db.skill_shares.find({"skill_key": self.skill_key})]
            if share_ids:
                db.skill_share_deliveries.delete_many({"share_id": {"$in": share_ids}})
            db.skill_shares.delete_many({"skill_key": self.skill_key})
            db.skill_versions.delete_many({"skill_id": self.skill_id})
        except Exception:
            pass

    def _member_id(self) -> str:
        """成员目录搜出 member 的 userId（顺带覆盖 skill-share-directory）。"""
        st, page = http("GET", "/api/v1/skill-share-directory?keyword=member", self.admin)
        self.assertEqual(st, 200, f"成员目录失败: {st} {page}")
        for item in page.get("items") or []:
            if item.get("username") == "member":
                return str(item["userId"])
        self.fail("成员目录未搜到 member")

    def _direct_share(self) -> str:
        """admin 直分享给 member，返回 deliveryId。"""
        st, body = http(
            "POST", f"/api/v1/skills/{self.skill_id}/share-with-users", self.admin,
            {"recipientUserIds": [self._member_id()]},
        )
        self.assertEqual(st, 200, f"直分享失败: {st} {body}")
        self.assertEqual(body.get("recipientCount"), 1)
        st, inbox = http("GET", "/api/v1/skill-share-inbox", self.member)
        self.assertEqual(st, 200)
        for item in inbox.get("items") or []:
            if item.get("name") == self.skill_key:
                return str(item["deliveryId"])
        self.fail("member 收件箱未出现刚分享的技能")

    # ---- 用例 ----

    def test_create_link_share_and_preview(self):
        """创建分享链接 → token 预览：名称/版本与快照一致。"""
        st, share = http("POST", f"/api/v1/skills/{self.skill_id}/shares", self.admin, {"expiresInDays": 7})
        self.assertEqual(st, 200, f"创建分享失败: {st} {share}")
        token = str(share.get("token") or "")
        self.assertTrue(token, f"分享响应缺 token: {share}")

        st, preview = http("GET", f"/api/v1/skill-shares/{token}", self.member)
        self.assertEqual(st, 200, f"token 预览失败: {st} {preview}")
        self.assertEqual(preview.get("name"), self.skill_key)
        self.assertEqual(preview.get("version"), "1.0.0")

    def test_direct_share_inbox_and_count(self):
        """直分享 → member 收件箱出现投递、徽标计数 pendingCount ≥ 1。"""
        delivery_id = self._direct_share()
        self.assertTrue(delivery_id)
        st, count = http("GET", "/api/v1/skill-share-inbox/count", self.member)
        self.assertEqual(st, 200)
        self.assertGreaterEqual(count.get("pendingCount", 0), 1)

    def test_accept_duplicate_semantics(self):
        """accept：org 共享同 key 已存在（源技能本体）→ 200 duplicate=True（不得 403）。"""
        delivery_id = self._direct_share()
        st, body = http(
            "POST", f"/api/v1/skill-share-inbox/{delivery_id}/accept", self.member, {}
        )
        self.assertEqual(st, 200, f"accept 失败（曾回归为 403）: {st} {body}")
        self.assertIn("duplicate", body, f"accept 响应缺 duplicate 字段: {body}")
        # member 与 admin 同 org → 源技能已存在 → duplicate=True
        self.assertTrue(body.get("duplicate"))

    def test_decline(self):
        """decline → 200。"""
        delivery_id = self._direct_share()
        st, body = http("POST", f"/api/v1/skill-share-inbox/{delivery_id}/decline", self.member, {})
        self.assertEqual(st, 200, f"decline 失败: {st} {body}")

    def test_revoke_share_invalidates_token(self):
        """吊销分享 → token 预览 404（安装同步被拒）。"""
        st, share = http("POST", f"/api/v1/skills/{self.skill_id}/shares", self.admin, {"expiresInDays": 7})
        self.assertEqual(st, 200)
        share_id = str(share.get("shareId") or "")
        token = str(share.get("token") or "")
        self.assertTrue(share_id and token)

        st, _body = http("DELETE", f"/api/v1/skills/{self.skill_id}/shares/{share_id}", self.admin)
        self.assertEqual(st, 200)
        st, _ = http("GET", f"/api/v1/skill-shares/{token}", self.member)
        self.assertEqual(st, 404, "吊销后 token 预览应 404")


if __name__ == "__main__":
    unittest.main()
