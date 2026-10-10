"""知识库全链路集成测试（上传 → ready → 列表 → ask → owner 隔离 → 删除）。

守护的真实缺陷面：

- #9 后台索引异步化后，status 终态必须是 ``ready``（前端 ``knowledgeStatus.ts``
  曾只认 ``indexed`` 导致列表全部显示"学习中"——本套件锁定后端终值 + 前端双映射契约）。
- #4 ask 端点对同步检索签名（``S-32`` 冻结）必须走线程池——曾经 ``await``
  同步 ``retriever.search`` 抛 ``TypeError: object list can't be used in 'await'`` → 500。
- owner 隔离：``member`` 不可见 ``admin`` 的个人知识（owner_scoped 查询 → 404）。
"""

from __future__ import annotations

import json
import time
import unittest
import urllib.error
import urllib.request

from tests.integration.base import (
    IntegrationTestCase,
    admin_token,
    http,
    member_token,
    unique_suffix,
)

BASE_URL = "http://127.0.0.1:8000"

DOC_TEXT = """# 集成测试文档 {suffix}

本平台的知识库检索采用 BM25 与向量两路召回、RRF 融合、重排精排的三段式管道。

关键词 itest-anchor-{suffix} 用于列表与问答断言，可溯源、可清理。
"""


def multipart_md(token: str, suffix: str) -> tuple[int, dict]:
    """上传一篇 .md 文档（multipart，浏览器同款表单形态）。"""
    boundary = "----itestkdboundary"
    content = DOC_TEXT.format(suffix=suffix).encode("utf-8")
    parts = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="itest-{suffix}.md"\r\nContent-Type: text/markdown\r\n\r\n'.encode()
        + content
        + f'\r\n--{boundary}--\r\n'.encode()
    )
    req = urllib.request.Request(BASE_URL + "/api/v1/knowledge/documents", method="POST", data=parts)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}


class KnowledgeFlowTest(IntegrationTestCase):
    """上传 → 索引 → 列表 → ask → owner 隔离 → 删除。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin = admin_token()
        cls.member = member_token()

    # ---- 助手 ----

    def _upload(self) -> tuple[str, str]:
        """admin 上传一篇独立文档（uuid 隔离 + 清理），返回 (doc_id, suffix)。"""
        suffix = unique_suffix()
        st, body = multipart_md(self.admin, suffix)
        self.assertEqual(st, 200, f"上传失败: {st} {body}")
        doc_id = str(body.get("id") or "")
        self.assertTrue(doc_id, f"上传响应缺 id: {body}")
        self.assertEqual(body.get("status"), "processing", "上传应立即返回 processing（不阻塞）")
        self.register_cleanup("DELETE", f"/api/v1/knowledge/documents/{doc_id}", self.admin)
        return doc_id, suffix

    def _wait_status(self, doc_id: str, timeout_s: int = 20) -> str:
        """轮询文档状态直至 ready/failed（≤timeout_s 秒），超时返回 timeout。"""
        for _ in range(timeout_s):
            time.sleep(1)
            st, doc = http("GET", f"/api/v1/knowledge/documents/{doc_id}", self.admin)
            if st == 200 and doc.get("status") in ("ready", "failed"):
                return str(doc.get("status"))
        return "timeout"

    # ---- 用例 ----

    def test_upload_processing_then_ready(self):
        """#9：上传立即 processing，后台索引 ≤20s 置 ready（终态锁定）。"""
        doc_id, _ = self._upload()
        status = self._wait_status(doc_id)
        self.assertEqual(status, "ready", f"索引终态非 ready: {status}")

    def test_list_contains_uploaded_doc(self):
        """列表（view=mine + 关键词）能检索到刚上传的文档。

        关键词匹配的是**标题**（= 文件名），不是正文——正文锚点搜不到属预期。
        """
        doc_id, suffix = self._upload()
        self.assertEqual(self._wait_status(doc_id), "ready")
        st, page = http(
            "GET", f"/api/v1/knowledge/documents?view=mine&keyword=itest-{suffix}", self.admin
        )
        self.assertEqual(st, 200)
        ids = [item.get("id") for item in page.get("items") or []]
        self.assertIn(doc_id, ids, f"列表未包含刚上传的文档: {ids}")

    def test_ask_returns_answer_and_citations(self):
        """#4：ask → 200 且 answer 非空 + citations chunk 级出处。

        模型提供商不可达时 soft-skip（检索链路由 citations 断言，生成侧属外部依赖）。
        """
        doc_id, suffix = self._upload()
        self.assertEqual(self._wait_status(doc_id), "ready")
        st, body = http(
            "POST", f"/api/v1/knowledge/documents/{doc_id}/ask", self.admin,
            {"query": f"itest-anchor-{suffix} 采用什么检索管道？"},
        )
        if st == 403 and "问答生成失败" in str(body.get("message") or body):
            self.skipTest("模型提供商不可达（检索链路正常，生成侧外部依赖跳过）")
        self.assertEqual(st, 200, f"ask 失败: {st} {body}")
        self.assertTrue(str(body.get("answer") or ""), "answer 为空")
        self.assertGreater(len(body.get("citations") or []), 0, "citations 为空")

    def test_owner_isolation_member_cannot_see_admin_doc(self):
        """owner 隔离：admin 的个人知识对 member 不可见（详情 404 + 列表不含）。"""
        doc_id, suffix = self._upload()
        st, body = http("GET", f"/api/v1/knowledge/documents/{doc_id}", self.member)
        self.assertEqual(st, 404, f"member 竟然可见 admin 的个人文档: {st} {body}")
        st, page = http(
            "GET", f"/api/v1/knowledge/documents?view=mine&keyword=itest-anchor-{suffix}", self.member
        )
        self.assertEqual(st, 200)
        self.assertEqual(page.get("total"), 0, "member 列表不应出现 admin 的文档")

    def test_delete_document(self):
        """删除 → 200 → 详情 404（原件 + chunks + 记录全链路清理）。"""
        doc_id, _ = self._upload()
        self.assertEqual(self._wait_status(doc_id), "ready")
        st, _body = http("DELETE", f"/api/v1/knowledge/documents/{doc_id}", self.admin)
        self.assertEqual(st, 200)
        st, _ = http("GET", f"/api/v1/knowledge/documents/{doc_id}", self.admin)
        self.assertEqual(st, 404, "删除后详情应 404")


if __name__ == "__main__":
    unittest.main()
