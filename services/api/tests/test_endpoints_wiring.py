"""knowledge / artifacts / admin 写端点测试。

覆盖：

1. **knowledge 端点**：文档元信息映射、chunk 详情、预览流、检索（``ctx`` 必传
   ``SearchFilters.user_context`` —— S-33 fail-closed）。
2. **artifacts 下载端点**：签名三步校验委托 ``share.verify_download``；跨会话产物被拒。
3. **admin 写端点**：4 个资源的 POST/PATCH **每条都触发 Profile 失效**（用计数断言，
   漏一条缓存就不刷新——这是本轮最容易漏的硬要求）。
"""

from __future__ import annotations

import asyncio
import types
import unittest
from typing import Any

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用

from app.core.errors import Forbidden, NotFound
from app.identity.context import RequestContext


# ==========================================================================
# 测试替身
# ==========================================================================


class _FakeInsertResult:
    def __init__(self, inserted_id: str) -> None:
        self.inserted_id = inserted_id


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def sort(self, *a: Any, **k: Any) -> "_FakeCursor":
        return self

    def limit(self, n: int | None) -> "_FakeCursor":
        return _FakeCursor(self._rows[:n] if n else self._rows)

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._rows


class FakeCollection:
    def __init__(self, name: str = "coll") -> None:
        self.name = name
        self.docs: list[dict[str, Any]] = []
        self._seq = 0

    def _match(self, doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for k, v in query.items():
            if k not in doc:
                return False
            actual = doc.get(k)
            if isinstance(v, dict) and "$in" in v:
                if actual not in v["$in"]:
                    return False
            elif actual != v:
                return False
        return True

    async def find_one(self, query: dict[str, Any] | None = None, *a: Any, **k: Any):
        for d in self.docs:
            if self._match(d, query or {}):
                return d
        return None

    def find(self, query: dict[str, Any] | None = None) -> _FakeCursor:
        return _FakeCursor([d for d in self.docs if self._match(d, query or {})])

    async def insert_one(self, doc: dict[str, Any]) -> _FakeInsertResult:
        self._seq += 1
        stored = dict(doc)
        stored.setdefault("_id", f"{self.name}-{self._seq}")
        self.docs.append(stored)
        return _FakeInsertResult(stored["_id"])

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *a: Any, **k: Any):
        doc = await self.find_one(query)
        if doc is not None and "$set" in update:
            doc.update(update["$set"])
        return types.SimpleNamespace(matched_count=1 if doc else 0, modified_count=1 if doc else 0)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return len([d for d in self.docs if self._match(d, query)])


class FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name))


def _ctx(roles: frozenset[str] = frozenset({"admin"})) -> RequestContext:
    return RequestContext(user_id="u-1", org_unit_id="ou-1", roles=roles)


class _MongoPatch:
    """把 ``app.core.mongo.get_collection`` 临时换成内存假库。"""

    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def __enter__(self) -> FakeDB:
        import app.core.mongo as mongo_mod

        self._mod = mongo_mod
        self._orig = mongo_mod.get_collection
        mongo_mod.get_collection = lambda name: self.db[name]  # type: ignore[assignment]
        return self.db

    def __exit__(self, *exc: Any) -> None:
        self._mod.get_collection = self._orig  # type: ignore[assignment]


# ==========================================================================
# 1. knowledge 端点
# ==========================================================================


class KnowledgeEndpointsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = FakeDB()
        self.ctx = _ctx()
        import app.api.endpoints.knowledge as kp

        self.kp = kp

    def test_document_meta_mapping(self):
        """文档元信息按前端 KnowledgeSourceDocument 字段映射。"""
        self.db["knowledge_documents"].docs.append(
            {
                "_id": "doc-1",
                "org_unit_id": "ou-1",
                "title": "运维手册",
                "original_filename": "ops.pdf",
                "mime_type": "application/pdf",
                "chunk_count": 12,
                "raw_text": "内容",
            }
        )
        with _MongoPatch(self.db):
            out = asyncio.run(self.kp.get_document("doc-1", ctx=self.ctx))
        self.assertEqual(out["id"], "doc-1")
        self.assertEqual(out["name"], "运维手册")
        self.assertEqual(out["originalFilename"], "ops.pdf")
        self.assertEqual(out["fileExt"], "pdf")
        self.assertEqual(out["chunkCount"], 12)
        self.assertTrue(out["canDownload"])

    def test_document_not_found_404(self):
        """文档不存在 → NotFound（不返回空 200）。"""
        with _MongoPatch(self.db):
            with self.assertRaises(NotFound):
                asyncio.run(self.kp.get_document("nope", ctx=self.ctx))

    def test_chunk_detail_mapping(self):
        """chunk 详情映射到前端 KnowledgeSourceChunk 字段。"""
        self.db["knowledge_documents"].docs.append(
            {"_id": "doc-1", "org_unit_id": "ou-1", "title": "t"}
        )
        self.db["knowledge_chunks"].docs.append(
            {
                "_id": "ck-1",
                "org_unit_id": "ou-1",
                "document_id": "doc-1",
                "seq": 3,
                "text": "正文片段",
                "heading_path": ["第一章", "1.1"],
                "page_from": 7,
            }
        )
        with _MongoPatch(self.db):
            out = asyncio.run(self.kp.get_chunk("doc-1", "ck-1", ctx=self.ctx))
        self.assertEqual(out["chunkId"], "ck-1")
        self.assertEqual(out["documentId"], "doc-1")
        self.assertEqual(out["ordinal"], 3)
        self.assertEqual(out["titlePath"], ["第一章", "1.1"])
        self.assertEqual(out["pageNo"], 7)

    def test_preview_returns_stream(self):
        """预览端点返回流式响应（前端用 blob 消费），不是 JSON。"""
        self.db["knowledge_documents"].docs.append(
            {"_id": "doc-1", "org_unit_id": "ou-1", "raw_text": "预览正文"}
        )
        with _MongoPatch(self.db):
            resp = asyncio.run(self.kp.get_document_preview("doc-1", ctx=self.ctx))
        # StreamingResponse 有 body_iterator，且不是 JSONResponse
        self.assertTrue(hasattr(resp, "body_iterator"))
        self.assertNotEqual(type(resp).__name__, "JSONResponse")

    def test_search_passes_user_context_fail_closed(self):
        """检索必须把 ctx 传进 SearchFilters.user_context（S-33 ACL 后过滤）。"""
        captured: dict[str, Any] = {}

        import app.knowledge.retriever as retriever_mod

        orig = retriever_mod.search

        def _fake_search(query: str, *, filters: Any, top_k: int) -> list[Any]:
            captured["query"] = query
            captured["filters"] = filters
            captured["top_k"] = top_k
            return []

        retriever_mod.search = _fake_search  # type: ignore[assignment]
        try:
            with _MongoPatch(self.db):
                out = asyncio.run(
                    self.kp.search_knowledge(
                        self.kp.SearchRequest(query="重启步骤", top_k=3), ctx=self.ctx
                    )
                )
        finally:
            retriever_mod.search = orig  # type: ignore[assignment]

        self.assertEqual(out, {"items": [], "total": 0})
        self.assertEqual(captured["query"], "重启步骤")
        self.assertEqual(captured["top_k"], 3)
        # ★ fail-closed：user_context 必须是当前 ctx，不能是 None
        self.assertIsNotNone(captured["filters"].user_context)
        self.assertEqual(captured["filters"].user_context.user_id, "u-1")
        self.assertEqual(captured["filters"].org_unit_id, "ou-1")

    def test_search_empty_query_short_circuits(self):
        """空 query 直接返回空结果（不触达检索器）。"""
        with _MongoPatch(self.db):
            out = asyncio.run(
                self.kp.search_knowledge(self.kp.SearchRequest(query="  "), ctx=self.ctx)
            )
        self.assertEqual(out, {"items": [], "total": 0})


# ==========================================================================
# 2. artifacts 下载端点
# ==========================================================================


class ArtifactDownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = FakeDB()
        import app.api.endpoints.artifacts as ap

        self.ap = ap
        self.ctx = _ctx()

    def test_signature_params_are_query_args(self):
        """签名四参数以 query string 形式暴露（user_id/nonce/expires_at/signature）。"""
        import inspect

        sig = inspect.signature(self.ap.download_artifact)
        for p in ("user_id", "nonce", "expires_at", "signature"):
            self.assertIn(p, sig.parameters, f"缺签名参数 {p}")

    def test_verify_download_called_with_signed_params(self):
        """校验委托 share.verify_download，参数按其签名传入。"""
        captured: dict[str, Any] = {}

        import app.collaboration.share as share_mod

        async def _fake_verify(params: dict[str, Any], *, secret: str, ctx: Any) -> str:
            captured["params"] = params
            captured["secret"] = secret
            captured["ctx"] = ctx
            return ""  # 交由端点走"文件缺失"分支，避免真实读盘

        orig = share_mod.verify_download
        share_mod.verify_download = _fake_verify  # type: ignore[assignment]
        try:
            with _MongoPatch(self.db):
                with self.assertRaises(NotFound):
                    asyncio.run(
                        self.ap.download_artifact(
                            "cv-1",
                            "art-1",
                            user_id="u-1",
                            nonce="n1",
                            expires_at=9999999999,
                            signature="sig",
                            ctx=self.ctx,
                        )
                    )
        finally:
            share_mod.verify_download = orig  # type: ignore[assignment]

        self.assertEqual(captured["params"]["artifact_id"], "art-1")
        self.assertEqual(captured["params"]["user_id"], "u-1")
        self.assertEqual(captured["params"]["nonce"], "n1")
        self.assertEqual(captured["ctx"].user_id, "u-1")

    def test_cross_conversation_artifact_forbidden(self):
        """产物不属于路径声明的会话 → Forbidden（签名合法也不许跨会话拿）。"""
        import app.collaboration.share as share_mod

        async def _fake_verify(params: dict[str, Any], *, secret: str, ctx: Any) -> str:
            return "some/path.pdf"

        self.db["artifacts"].docs.append(
            {"_id": "art-1", "org_unit_id": "ou-1", "conversation_id": "cv-OTHER"}
        )
        orig = share_mod.verify_download
        share_mod.verify_download = _fake_verify  # type: ignore[assignment]
        try:
            with _MongoPatch(self.db):
                with self.assertRaises(Forbidden):
                    asyncio.run(
                        self.ap.download_artifact(
                            "cv-1",
                            "art-1",
                            user_id="u-1",
                            nonce="n1",
                            expires_at=9999999999,
                            signature="sig",
                            ctx=self.ctx,
                        )
                    )
        finally:
            share_mod.verify_download = orig  # type: ignore[assignment]


# ==========================================================================
# 3. admin 写端点：每条写操作都必须失效 Profile 缓存
# ==========================================================================


class AdminWriteInvalidationTest(unittest.TestCase):
    """写 model/tool/skill/grant/binding 后**必须**触发 Profile 失效。

    这是 Profile 重编译机制的唯一消费入口（``03`` §3.5）——漏一条缓存就不刷新。
    """

    def setUp(self) -> None:
        self.db = FakeDB()
        self.invalidate_calls: list[str] = []

        import app.api.endpoints.admin as admin_ep
        import app.kernel.profile.store as store_mod

        self.admin_ep = admin_ep
        self._store = store_mod
        self._orig_invalidate = admin_ep.invalidate

        async def _fake_invalidate(org_unit_id: str) -> None:
            self.invalidate_calls.append(org_unit_id)

        admin_ep.invalidate = _fake_invalidate  # type: ignore[assignment]

    def tearDown(self) -> None:
        import app.api.endpoints.admin as admin_ep

        admin_ep.invalidate = self._orig_invalidate  # type: ignore[assignment]

    def test_create_endpoints_all_invalidate(self):
        """4 个资源的 POST 写端点都失效缓存。"""
        # ★ 传**真模型实例**而非裸 dict：写端点已改用 Pydantic 模型做必填校验
        # （唯一索引字段必填化），直接传 dict 会绕过模型、也测不到 422 行为。
        cases = [
            (self.admin_ep.create_model_provider,
             self.admin_ep.ModelProviderCreate(name="p", code="c", base_url="u")),
            (self.admin_ep.create_model_instance,
             self.admin_ep.ModelInstanceCreate(provider_id="p1", model_name="m")),
            (self.admin_ep.create_role_binding,
             self.admin_ep.RoleBindingCreate(
                 subject_type="user", subject_id="u1", role_code="admin"
             )),
            (self.admin_ep.create_grant,
             self.admin_ep.GrantCreate(resource_type="tool", resource_id="t1",
                                      subject_type="all", effect="allow")),
        ]
        for fn, body in cases:
            with self.subTest(endpoint=fn.__name__):
                self.invalidate_calls.clear()
                with _MongoPatch(self.db):
                    asyncio.run(fn(body, ctx=_ctx()))
                self.assertEqual(
                    self.invalidate_calls,
                    ["ou-1"],
                    f"{fn.__name__} 未触发 Profile 失效",
                )

    def test_patch_endpoints_all_invalidate(self):
        """4 个资源的 PATCH 写端点都失效缓存。"""
        # 先塞入可命中的文档
        self.db["model_providers"].docs.append({"_id": "p1", "org_unit_id": "ou-1"})
        self.db["model_instances"].docs.append({"_id": "i1", "org_unit_id": "ou-1"})
        self.db["role_bindings"].docs.append({"_id": "rb1", "org_unit_id": "ou-1"})
        self.db["resource_grants"].docs.append({"_id": "g1", "org_unit_id": "ou-1"})

        cases = [
            (self.admin_ep.patch_model_provider, "p1", {"status": "disabled"}),
            (self.admin_ep.patch_model_instance, "i1", {"status": "disabled"}),
            (self.admin_ep.patch_role_binding, "rb1", {"role_code": "viewer"}),
            (self.admin_ep.patch_grant, "g1", {"effect": "deny"}),
        ]
        for fn, oid, body in cases:
            with self.subTest(endpoint=fn.__name__):
                self.invalidate_calls.clear()
                with _MongoPatch(self.db):
                    asyncio.run(fn(oid, body, ctx=_ctx()))
                self.assertEqual(
                    self.invalidate_calls,
                    ["ou-1"],
                    f"{fn.__name__} 未触发 Profile 失效",
                )

    def test_grant_rejects_invalid_effect(self):
        """effect 只允许 allow/deny（deny 优先由 acl.py 兜底，入口先挡非法值）。"""
        from app.core.errors import DomainError

        with _MongoPatch(self.db):
            with self.assertRaises(DomainError):
                asyncio.run(
                    self.admin_ep.create_grant(
                        self.admin_ep.GrantCreate(
                            resource_type="tool", resource_id="t",
                            subject_type="all", effect="maybe",
                        ),
                        ctx=_ctx(),
                    )
                )

    def test_write_endpoints_reject_missing_permission(self):
        """无权限用户被 403 挡住（fail-closed）。"""
        weak_ctx = _ctx(roles=frozenset())  # 无任何角色
        with _MongoPatch(self.db):
            with self.assertRaises(Forbidden):
                asyncio.run(
                    self.admin_ep.create_model_provider(
                        self.admin_ep.ModelProviderCreate(name="x", code="c"),
                        ctx=weak_ctx,
                    )
                )

    def test_provider_write_is_field_whitelisted(self):
        """api_key_enc 等敏感字段不能经管理端写端点落库。"""
        with _MongoPatch(self.db):
            asyncio.run(
                self.admin_ep.create_model_provider(
                    self.admin_ep.ModelProviderCreate.model_validate(
                        {"name": "p", "code": "c", "api_key_enc": "SECRET", "junk": 1}
                    ),
                    ctx=_ctx(),
                )
            )
        doc = self.db["model_providers"].docs[-1]
        self.assertNotIn("api_key_enc", doc, "密钥字段不得经写端点落库")
        self.assertNotIn("junk", doc, "白名单外字段不得落库")
        self.assertEqual(doc["name"], "p")


if __name__ == "__main__":
    unittest.main()
