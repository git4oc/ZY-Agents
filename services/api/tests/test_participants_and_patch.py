"""participants 端点签名 + 空 PATCH 假成功 的 HTTP 层回归测试。

两件被独立复验抓出来的问题，都是**只有真打 HTTP 才暴露**的：

1. **P0 复发**：``conversations.py::add_participant`` 的 ``body: Any`` 让 FastAPI
   把它当 **query 参数** → ``422 {"loc":["query","body"]}``，端点完全不可用。
   （与已修的 ``branding.py`` 同款缺陷。）
2. **假成功**：5 个 PATCH 端点在 body 为空 / 字段全被白名单滤掉时**什么都没改**
   却回 ``200 {"ok": true}``——违背本项目"不假装成功"的原则。

★ 全部走 **TestClient 完整 ASGI 栈**（真路由 / 真依赖解析 / 真中间件 / 真序列化），
只桩驱动层；用独立 org_unit_id，**不写真库**。
"""

from __future__ import annotations

import os
import unittest
from typing import Any

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用

os.environ.setdefault("JWT_SIGNING_SECRET", "participants-patch-test-secret-32")
os.environ.setdefault("KERNEL_HOST_TOKEN", "t")
os.environ.setdefault("KERNEL_MODEL_GATEWAY_SECRET", "t")
os.environ.setdefault("KERNEL_TOOL_GATEWAY_SECRET", "t")
os.environ.setdefault("ARTIFACT_URL_SECRET", "t")

#: ★ 独立测试 org，不碰真库
TEST_ORG = "ou-test-participants"


class _InsertResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    def limit(self, n: int | None) -> "_Cursor":
        return _Cursor(self._rows[:n] if n else self._rows)

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._rows


class FakeCollection:
    def __init__(
        self,
        name: str = "coll",
        unique_specs: list[tuple[list[str], bool]] | None = None,
    ) -> None:
        self.name = name
        self.docs: list[dict[str, Any]] = []
        self.unique_specs = unique_specs or []

    def _assert_unique(self, cand: dict[str, Any], *, ignore: Any = None) -> None:
        """按每条唯一索引校验；**sparse 索引跳过缺字段的文档**（Mongo 真实语义）。"""
        from pymongo.errors import DuplicateKeyError

        for fields, sparse in self.unique_specs:
            # sparse：候选缺任一索引字段 → 不进索引，不参与唯一性比较
            if sparse and any(f not in cand for f in fields):
                continue
            key = tuple(cand.get(f) for f in fields)
            for d in self.docs:
                if d is ignore:  # ★ 跳过"被修改自身"，只检查其它文档
                    continue
                if sparse and any(f not in d for f in fields):
                    continue
                if tuple(d.get(f) for f in fields) == key:
                    raise DuplicateKeyError(f"E11000 dup key: {self.name} {fields}={key}")

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

    def find(self, query: dict[str, Any] | None = None) -> _Cursor:
        return _Cursor([d for d in self.docs if self._match(d, query or {})])

    async def insert_one(self, doc: dict[str, Any]) -> _InsertResult:
        stored = dict(doc)
        stored.setdefault("_id", f"{self.name}-{len(self.docs) + 1}")
        self._assert_unique(stored)
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
        *a: Any,
        **k: Any,
    ):
        """支持 ``upsert``：无匹配时按 query + $set **新建**文档。

        ★ 必须实现 upsert——``collaboration/participants.py::add`` 用的是
        ``update_one(..., upsert=True)``，不实现它则参与者永远插不进去
        （表现为"接口回 200 但 GET 读不到"）。
        """
        doc = await self.find_one(query)
        if doc is None and upsert:
            created = dict(query)
            created.update(update.get("$set", {}))
            created.setdefault("_id", f"{self.name}-{len(self.docs) + 1}")
            self._assert_unique(created)
            self.docs.append(created)
            return type("R", (), {"modified_count": 0, "upserted_id": created["_id"]})()
        if doc is not None and "$set" in update:
            merged = {**doc, **update["$set"]}
            self._assert_unique(merged, ignore=doc)
            doc.update(update["$set"])
        return type("R", (), {"modified_count": 1 if doc else 0})()

    async def replace_one(self, query: dict[str, Any], doc: dict[str, Any], *a: Any, **k: Any):
        existing = await self.find_one(query)
        if existing is not None:
            existing.clear()
            existing.update(doc)
        else:
            self.docs.append(dict(doc))
        return type("R", (), {"modified_count": 1})()

    async def delete_one(self, query: dict[str, Any], *a: Any, **k: Any):
        doc = await self.find_one(query)
        if doc is not None:
            self.docs.remove(doc)
        return type("R", (), {"deleted_count": 1 if doc else 0})()

    async def count_documents(self, query: dict[str, Any] | None = None) -> int:
        return (
            len(self.docs)
            if not query
            else len([d for d in self.docs if self._match(d, query)])
        )


def _unique_specs_for(name: str) -> list[tuple[list[str], bool]]:
    """从 ``app.core.mongo.INDEX_SPECS`` 取该集合的唯一索引 ``[(字段, sparse)]``。

    ★ 必须带出 ``sparse``：Mongo 的 **sparse 唯一索引只索引"有该字段"的文档**，
    缺字段的文档**不参与**唯一性比较。不模拟 sparse 就会把"都没有 session_id"
    的两条文档判成冲突——而真实 Mongo 允许（``conversations_session`` 就是这种索引）。
    """
    from app.core.mongo import INDEX_SPECS

    out: list[tuple[list[str], bool]] = []
    for coll, specs in INDEX_SPECS:
        if coll != name:
            continue
        for spec in specs:
            if spec.get("unique"):
                out.append(([k for k, _ in spec.get("keys", [])], bool(spec.get("sparse"))))
    return out



class FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(
            name, FakeCollection(name, _unique_specs_for(name))
        )


class ParticipantsAndPatchTest(unittest.TestCase):
    """participants 端点 + 5 个 PATCH 端点的空提交语义。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.db = FakeDB()
        # ★ 预置 code="default" 的根组织单元（org_unit_id 用 str 形态，与 seed 写入
        #   形态一致、也与 RequestContext.org_unit_id 一致）。这样 lifespan 的
        #   seed_identity() 识别"根已存在"而跳过，不会另插一份同名 admin 用户
        #   ——否则 login 的重复用户名守卫会直接 401。
        # _id 取 TEST_ORG 本身（str 形态）：seed 用 str(root._id) 去查 users，
        # 命中即跳过插入——否则会多插一个同名 admin，login 的重复守卫直接 401。
        cls.db["org_units"].docs.append(
            {"_id": TEST_ORG, "name": "测试组织", "code": "default", "status": "active"}
        )
        cls.db["users"].docs.append(
            {
                "_id": "admin-uid",
                "org_unit_id": TEST_ORG,
                "username": "admin",
                "status": "active",
            }
        )
        cls.db["users"].docs.append(
            {
                "_id": "someone-1",
                "org_unit_id": TEST_ORG,
                "username": "someone",
                "status": "active",
            }
        )
        cls.db["role_bindings"].docs.append(
            {
                "_id": "rb-1",
                "org_unit_id": TEST_ORG,
                "subject_type": "user",
                "subject_id": "admin-uid",
                "role_code": "admin",
                "scope": "org_unit",
                "resource_type": None,
                "resource_id": None,
            }
        )
        cls.db["tools"].docs.append(
            {
                "_id": "tool-1",
                "org_unit_id": TEST_ORG,
                "name": "http_fetch",
                "status": "active",
                "description": "抓取网页",
            }
        )
        cls.db["model_providers"].docs.append(
            {"_id": "prov-1", "org_unit_id": TEST_ORG, "name": "A", "code": "code-a",
             "status": "active"}
        )
        cls.db["model_instances"].docs.append(
            {"_id": "inst-1", "org_unit_id": TEST_ORG, "provider_id": "prov-1",
             "model_name": "gpt-x", "status": "active"}
        )
        cls.db["role_bindings"].docs.append(
            {"_id": "rb-2", "org_unit_id": TEST_ORG, "subject_type": "user",
             "subject_id": "someone-1", "role_code": "member", "scope": "org_unit",
             "resource_type": None, "resource_id": None}
        )
        cls.db["resource_grants"].docs.append(
            {"_id": "grant-1", "org_unit_id": TEST_ORG, "resource_type": "tool",
             "resource_id": "t1", "subject_type": "user", "subject_id": "u1",
             "effect": "allow"}
        )

        import app.core.mongo as mongo_mod

        cls._orig_get_collection = mongo_mod.get_collection
        cls._orig_ensure = mongo_mod.ensure_indexes
        mongo_mod.get_collection = lambda n: cls.db[n]  # type: ignore[assignment]
        mongo_mod.ensure_indexes = lambda: None  # type: ignore[assignment]

        import app.core.redis as redis_mod

        class _FakeRedis:
            async def publish(self, *a: Any, **k: Any) -> int:
                return 0

            async def aclose(self) -> None:
                return None

        cls._orig_redis_client = redis_mod.client
        cls._orig_redis_connect = redis_mod.connect
        redis_mod.client = lambda: _FakeRedis()  # type: ignore[assignment]
        redis_mod.connect = lambda url: _FakeRedis()  # type: ignore[assignment]

        from starlette.testclient import TestClient

        from app.main import create_app

        cls.client = TestClient(create_app())
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        import app.core.mongo as mongo_mod
        import app.core.redis as redis_mod
        from app.branding.settings import invalidate_cache
        from app.kernel.events import set_sink
        from app.kernel.turn.driver import set_event_handler

        mongo_mod.get_collection = cls._orig_get_collection  # type: ignore[assignment]
        mongo_mod.ensure_indexes = cls._orig_ensure  # type: ignore[assignment]
        redis_mod.client = cls._orig_redis_client  # type: ignore[assignment]
        redis_mod.connect = cls._orig_redis_connect  # type: ignore[assignment]
        set_sink(None)
        set_event_handler(None)
        invalidate_cache()

    def setUp(self) -> None:
        r = self.client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "x"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.H = {"Authorization": "Bearer " + r.json()["access_token"]}
        # ★ 每个用例前把被 PATCH 的文档恢复初值——否则用例之间互相污染
        #   （unittest 按字母序跑，前一个用例改了 status，后一个就断言失败）。
        self._reset_patched_docs()

    def _reset_patched_docs(self) -> None:
        initial = {
            "tool-1": {"name": "http_fetch", "status": "active", "description": "抓取网页"},
            "prov-1": {"name": "A", "code": "code-a", "status": "active"},
            "inst-1": {"provider_id": "prov-1", "model_name": "gpt-x", "status": "active"},
            "rb-2": {"role_code": "member", "scope": "org_unit"},
            "grant-1": {"resource_type": "tool", "resource_id": "t1", "effect": "allow"},
        }
        colls = {
            "tool-1": "tools", "prov-1": "model_providers", "inst-1": "model_instances",
            "rb-2": "role_bindings", "grant-1": "resource_grants",
        }
        for doc_id, values in initial.items():
            coll = self.db[colls[doc_id]]
            target = next((d for d in coll.docs if d.get("_id") == doc_id), None)
            if target is None:
                target = {"_id": doc_id, "org_unit_id": TEST_ORG}
                coll.docs.append(target)
            target.clear()
            target.update({"_id": doc_id, "org_unit_id": TEST_ORG, **values})

    # ============ 1. participants 端点 ============

    def test_add_participant_accepts_json_body(self):
        """★ P0 回归：``json=`` 传 body 必须被接受（修前恒 422 loc=query.body）。"""
        cid = self.client.post(
            "/api/v1/conversations", json={"title": "参与者测试"}, headers=self.H
        ).json()["id"]
        r = self.client.post(
            f"/api/v1/conversations/{cid}/participants",
            json={"user_id": "someone-1", "role": "viewer"},
            headers=self.H,
        )
        self.assertEqual(
            r.status_code, 200, f"加参与者应 200，实际 {r.status_code}: {r.text}"
        )
        self.assertEqual(r.json()["user_id"], "someone-1")
        self.assertEqual(r.json()["role"], "viewer")

    def test_added_participant_is_readable(self):
        """加完能回读（证明真落库，不只是返回值）。"""
        cid = self.client.post(
            "/api/v1/conversations", json={"title": "回读测试"}, headers=self.H
        ).json()["id"]
        self.client.post(
            f"/api/v1/conversations/{cid}/participants",
            json={"user_id": "someone-1", "role": "editor"},
            headers=self.H,
        )
        items = self.client.get(
            f"/api/v1/conversations/{cid}/participants", headers=self.H
        ).json()["items"]
        self.assertTrue(any(i["user_id"] == "someone-1" for i in items), str(items))

    def test_missing_user_id_returns_422(self):
        """缺 user_id → 422（**不是**旧代码那个 404 NotFound——字段缺失不是"资源不存在"）。"""
        cid = self.client.post(
            "/api/v1/conversations", json={"title": "缺字段"}, headers=self.H
        ).json()["id"]
        r = self.client.post(
            f"/api/v1/conversations/{cid}/participants", json={"role": "viewer"},
            headers=self.H,
        )
        self.assertEqual(r.status_code, 422, f"缺 user_id 应 422，实际 {r.status_code}")
        self.assertIn("user_id", str(r.json()["detail"][0]["loc"]))

    def test_empty_user_id_returns_422(self):
        """空串 user_id 也拒（``min_length=1``，否则会落一条空主体）。"""
        cid = self.client.post(
            "/api/v1/conversations", json={"title": "空串"}, headers=self.H
        ).json()["id"]
        r = self.client.post(
            f"/api/v1/conversations/{cid}/participants", json={"user_id": ""},
            headers=self.H,
        )
        self.assertEqual(r.status_code, 422, f"空 user_id 应 422，实际 {r.status_code}")

    # ============ 2. 空 PATCH 不得假成功 ============

    PATCH_CASES = [
        ("tools/tool-1", "tools"),
        ("model-providers/prov-1", "model-providers"),
        ("model-instances/inst-1", "model-instances"),
        ("role-bindings/rb-2", "role-bindings"),
        ("grants/grant-1", "grants"),
    ]

    def test_empty_patch_body_returns_422_not_200(self):
        """★ 空 body → 422（修前 200 ok:true 却什么都没改）。"""
        for path, label in self.PATCH_CASES:
            with self.subTest(resource=label):
                r = self.client.patch(f"/api/v1/admin/{path}", json={}, headers=self.H)
                self.assertEqual(
                    r.status_code, 422,
                    f"{label} 空 PATCH 应 422，实际 {r.status_code}（200 是假成功）",
                )
                self.assertEqual(r.json()["error"]["code"], "empty_patch_body")

    def test_all_non_writable_fields_returns_422(self):
        """★ 字段全被白名单滤掉 → 422 ``no_writable_fields``（更危险：调用方以为改了）。

        以 ``api_key_enc`` 为例：传了它却被静默丢弃，调用方会以为密钥已更新。
        """
        for path, label in self.PATCH_CASES:
            with self.subTest(resource=label):
                r = self.client.patch(
                    f"/api/v1/admin/{path}",
                    json={"api_key_enc": "SECRET", "_id": "hack", "org_unit_id": "hack"},
                    headers=self.H,
                )
                self.assertEqual(
                    r.status_code, 422,
                    f"{label} 全非白名单字段应 422，实际 {r.status_code}",
                )
                self.assertEqual(r.json()["error"]["code"], "no_writable_fields")
                # 错误信息回显被拒字段，让调用方知道该传什么
                self.assertIn("api_key_enc", r.json()["error"]["details"]["rejected_fields"])

    def test_valid_patch_still_works_and_reports_updated(self):
        """正常字段 → 200，且**真的改到了库**（响应回显实际更新的字段）。"""
        r = self.client.patch(
            "/api/v1/admin/tools/tool-1", json={"status": "disabled"}, headers=self.H
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["updated"], ["status"])
        tool = next(t for t in self.db["tools"].docs if t["_id"] == "tool-1")
        self.assertEqual(tool["status"], "disabled", "库里的值必须真的变了")

    def test_empty_patch_did_not_touch_db(self):
        """空 PATCH 失败后，库里的文档**未被改动**（且不应触发 invalidate）。"""
        tool = next(t for t in self.db["tools"].docs if t["_id"] == "tool-1")
        before = dict(tool)
        self.client.patch("/api/v1/admin/tools/tool-1", json={}, headers=self.H)
        self.assertEqual(tool, before, "空 PATCH 不得改动任何字段")

    def test_partial_whitelist_still_applies(self):
        """混合字段：白名单内的照常生效（不被误判为"全不可写"）。"""
        r = self.client.patch(
            "/api/v1/admin/tools/tool-1",
            json={"status": "active", "api_key_enc": "SHOULD_BE_IGNORED"},
            headers=self.H,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["updated"], ["status"])
        tool = next(t for t in self.db["tools"].docs if t["_id"] == "tool-1")
        self.assertEqual(tool["status"], "active")
        self.assertNotIn("api_key_enc", tool, "非白名单字段仍不得入库")

    def test_explicit_null_can_clear_field(self):
        """★ 显式 null 表示"清空该字段"，必须真的落库（不被静默丢弃）。

        ``output_schema: null`` 在 02 §2.3 里有明确语义 = "不做输出校验"。
        若 PATCH 过滤掉 null，这条清空意图就被静默吞掉——又是一种假成功。
        """
        r = self.client.patch(
            "/api/v1/admin/tools/tool-1", json={"output_schema": None}, headers=self.H
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["updated"], ["output_schema"])
        tool = next(t for t in self.db["tools"].docs if t["_id"] == "tool-1")
        self.assertIn("output_schema", tool)
        self.assertIsNone(tool["output_schema"], "显式 null 应真的清空该字段")

    # ============ 3. 顺手发现：conversations PATCH 同一类假成功 ============

    def test_conversation_empty_patch_returns_422(self):
        """★ ``PATCH /conversations/{id}`` 也有同一类假成功（web 会调这个端点）。

        修前：body 为空 → 跳过 update → 仍返回 200 + 完整会话对象，
        调用方无法区分"改成功"与"什么都没发生"。
        """
        cid = self.client.post(
            "/api/v1/conversations", json={"title": "空 PATCH"}, headers=self.H
        ).json()["id"]
        r = self.client.patch(f"/api/v1/conversations/{cid}", json={}, headers=self.H)
        self.assertEqual(r.status_code, 422, f"空 PATCH 应 422，实际 {r.status_code}")
        self.assertEqual(r.json()["error"]["code"], "empty_patch_body")

    def test_conversation_null_only_patch_returns_422(self):
        """全 null 字段（显式传 title:null）同样不得假成功。"""
        cid = self.client.post(
            "/api/v1/conversations", json={"title": "全 null"}, headers=self.H
        ).json()["id"]
        r = self.client.patch(
            f"/api/v1/conversations/{cid}", json={"title": None}, headers=self.H
        )
        self.assertEqual(r.status_code, 422, f"全 null PATCH 应 422，实际 {r.status_code}")

    def test_conversation_valid_patch_still_works(self):
        """正常 PATCH 仍 200 且真的改到（别把正常路径也拦了）。"""
        cid = self.client.post(
            "/api/v1/conversations", json={"title": "旧标题"}, headers=self.H
        ).json()["id"]
        r = self.client.patch(
            f"/api/v1/conversations/{cid}", json={"title": "新标题"}, headers=self.H
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["title"], "新标题")


if __name__ == "__main__":
    unittest.main()
