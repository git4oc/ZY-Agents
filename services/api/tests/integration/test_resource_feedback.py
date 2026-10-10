"""资源反馈集成测试（评论 / 回复 / 点赞 / 通知）。

守护的真实缺陷面：

- 后端缺失 ``resource-feedback`` 路由导致前端评价/点赞弹窗 404 报错（本套件防回归）。
- 评论删除权限：仅作者可删（非作者 403），删除连同子评论。
- 通知语义：评论/点赞通知资源 owner 与被回复人；自己操作自己资源不通知。
"""

from __future__ import annotations

import unittest

from tests.integration.base import (
    IntegrationTestCase,
    admin_token,
    http,
    member_token,
    mongo_db,
    unique_suffix,
)
from tests.integration.test_knowledge_flow import multipart_md


class ResourceFeedbackTest(IntegrationTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin = admin_token()
        cls.member = member_token()

    def setUp(self) -> None:
        super().setUp()
        # 通用资源 id（org 隔离 + uuid 后缀，用例间零干扰；通知用例用真实文档）
        self.resource = f"res-{unique_suffix()}"
        self._cleanup_oids: list[tuple[str, str]] = []  # (集合名, 查询 JSON) 直接清理

    def tearDown(self) -> None:
        super().tearDown()
        try:
            db = mongo_db()
            for coll, query in reversed(self._cleanup_oids):
                getattr(db, coll).delete_many(query)
        except Exception:
            pass

    def _track(self, coll: str, query: dict) -> None:
        self._cleanup_oids.append((coll, query))

    # ---- 评论 ----

    def test_comment_and_thread(self):
        """加评论 → 线程 commentCount=1、内容一致、mine=True。"""
        st, c1 = http(
            "POST", f"/api/v1/resource-feedback/personal_knowledge/{self.resource}/comments",
            self.member, {"content": "第一条评论"},
        )
        self.assertEqual(st, 200, f"加评论失败: {st} {c1}")
        self.assertEqual(c1.get("content"), "第一条评论")
        self.assertTrue(c1.get("mine"))
        self._track("resource_feedback", {"resource_id": self.resource})

        st, page = http("GET", f"/api/v1/resource-feedback/personal_knowledge/{self.resource}", self.member)
        self.assertEqual(st, 200)
        self.assertEqual(page.get("commentCount"), 1)
        self.assertEqual(len(page.get("items") or []), 1)
        self.assertEqual((page.get("items") or [])[0].get("content"), "第一条评论")

    def test_reply_derives_root_and_replyto(self):
        """回复：rootId 指向根评论、replyTo.userId 指向被回复人。"""
        st, root = http(
            "POST", f"/api/v1/resource-feedback/personal_knowledge/{self.resource}/comments",
            self.member, {"content": "根评论"},
        )
        self.assertEqual(st, 200)
        self._track("resource_feedback", {"resource_id": self.resource})

        st, reply = http(
            "POST", f"/api/v1/resource-feedback/personal_knowledge/{self.resource}/comments",
            self.admin, {"content": "管理员回复", "parentId": root["id"]},
        )
        self.assertEqual(st, 200, f"回复失败: {st} {reply}")
        self.assertEqual(reply.get("rootId"), root["id"], "rootId 应指向根评论")
        self.assertEqual(
            (reply.get("replyTo") or {}).get("userId"),
            (root.get("author") or {}).get("userId"),
            "replyTo.userId 应指向被回复人",
        )

    def test_delete_comment_only_author(self):
        """删除权限：非作者 403、作者 200。"""
        st, c = http(
            "POST", f"/api/v1/resource-feedback/personal_knowledge/{self.resource}/comments",
            self.member, {"content": "待删评论"},
        )
        self.assertEqual(st, 200)
        self._track("resource_feedback", {"resource_id": self.resource})
        comment_id = c["id"]

        st, body = http("DELETE", f"/api/v1/resource-feedback/comments/{comment_id}", self.admin)
        self.assertEqual(st, 403, f"非作者删除应 403: {st} {body}")
        st, body = http("DELETE", f"/api/v1/resource-feedback/comments/{comment_id}", self.member)
        self.assertEqual(st, 200, f"作者删除失败: {st} {body}")

        st, page = http("GET", f"/api/v1/resource-feedback/personal_knowledge/{self.resource}", self.member)
        self.assertEqual(page.get("commentCount"), 0, "删除后评论数应为 0")

    def test_empty_content_rejected(self):
        """空白评论拒绝（403，明确报错不假成功）。"""
        st, _body = http(
            "POST", f"/api/v1/resource-feedback/personal_knowledge/{self.resource}/comments",
            self.member, {"content": "   "},
        )
        self.assertEqual(st, 403)

    # ---- 点赞 ----

    def test_resource_like_toggle(self):
        """资源点赞 toggle：like → likes=1 → 再 like → likes=0。"""
        path = f"/api/v1/resource-feedback/personal_knowledge/{self.resource}/like"
        self._track("resource_feedback_likes", {"resource_id": self.resource})
        st, r = http("POST", path, self.member)
        self.assertEqual(st, 200, f"点赞失败: {st} {r}")
        self.assertTrue(r.get("likedByMe"))
        self.assertEqual(r.get("likes"), 1)
        st, r = http("POST", path, self.member)
        self.assertEqual(st, 200)
        self.assertFalse(r.get("likedByMe"))
        self.assertEqual(r.get("likes"), 0)

    def test_comment_like_toggle(self):
        """评论点赞 toggle。"""
        st, c = http(
            "POST", f"/api/v1/resource-feedback/personal_knowledge/{self.resource}/comments",
            self.member, {"content": "被赞评论"},
        )
        self.assertEqual(st, 200)
        self._track("resource_feedback", {"resource_id": self.resource})
        path = f"/api/v1/resource-feedback/comments/{c['id']}/like"
        st, r = http("POST", path, self.admin)
        self.assertEqual(st, 200)
        self.assertTrue(r.get("likedByMe"))
        self.assertEqual(r.get("likes"), 1)
        st, r = http("POST", path, self.admin)
        self.assertEqual(st, 200)
        self.assertFalse(r.get("likedByMe"))
        self.assertEqual(r.get("likes"), 0)

    # ---- 通知 ----

    def test_notification_to_owner_on_comment(self):
        """member 评论 admin 的知识文档 → admin 收通知；admin 自评不通知自己。"""
        from tests.integration.test_knowledge_flow import unique_suffix as _suffix

        st, up = multipart_md(self.admin, _suffix())
        self.assertEqual(st, 200, f"前置上传失败: {st} {up}")
        doc_id = str(up["id"])
        self.register_cleanup("DELETE", f"/api/v1/knowledge/documents/{doc_id}", self.admin)
        self._track("resource_feedback", {"resource_id": doc_id})
        self._track("resource_feedback_notifications", {"resource_id": doc_id})
        self._track("resource_feedback_likes", {"resource_id": doc_id})

        st, c = http(
            "POST", f"/api/v1/resource-feedback/personal_knowledge/{doc_id}/comments",
            self.member, {"content": "访客评论"},
        )
        self.assertEqual(st, 200)

        st, page = http("GET", "/api/v1/resource-feedback-notifications", self.admin)
        self.assertEqual(st, 200)
        matched = [i for i in page.get("items") or [] if i.get("resourceId") == doc_id]
        self.assertTrue(matched, "owner 未收到评论通知")
        self.assertGreaterEqual(page.get("unreadCount", 0), 1)

        # admin 自评自己的资源 → 不新增通知
        before = page.get("unreadCount")
        st, _ = http(
            "POST", f"/api/v1/resource-feedback/personal_knowledge/{doc_id}/comments",
            self.admin, {"content": "自评"},
        )
        self.assertEqual(st, 200)
        st, page2 = http("GET", "/api/v1/resource-feedback-notifications", self.admin)
        self.assertEqual(page2.get("unreadCount"), before, "自评不应新增通知")


if __name__ == "__main__":
    unittest.main()
