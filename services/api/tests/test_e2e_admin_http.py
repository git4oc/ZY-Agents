"""★ 端到端 HTTP 测试（TestClient 走完整 ASGI 栈）——本轮最重要的护栏。

## 为什么必须是"真打 HTTP"

本轮修的三个缺陷**全部是单元测试测不到、只有真打 HTTP 才暴露**的：

- **缺陷 A**：``branding.py`` 的 PATCH handler 把 body 声明成 ``Any``，FastAPI 据此
  判定它是 **query 参数** → ``client.patch(url, json={...})`` 收不到 body → 422
  ``{"loc": ["query","body"]}``。只测 ``settings.update()`` 的单元测试**永远发现不了**，
  因为它根本不经过 HTTP 层。
- **缺陷 B**：``admin`` 列表端点直接返回含 ``ObjectId`` 的 mongo 原始文档 →
  FastAPI 序列化 500。此前 ``role_bindings`` 集合一直为空，``items=[]`` 不触发序列化，
  所以"假集合永远返回空列表"的测试测不到真实数据形态。
- **缺陷 C**：branding 侧内联 ``if "brand.manage" not in ctx.roles``，不承认
  ``admin`` 角色码 → 管理员被 403。纯函数层的权限测试用的是"权限码当角色码"的
  假数据，与真实登录流程（roles 装的是**角色码**）不一致，也就测不出来。

所以本文件用 ``TestClient`` 走**完整 ASGI 栈**：真路由、真依赖解析、真中间件
（TraceId / ErrorMapping / Auth / HmacAuth）、真 FastAPI 响应序列化。
只桩**驱动层**（mongo / redis），被测逻辑全部是真实 ``app/`` 源码。
"""

from __future__ import annotations

import os
import unittest
from typing import Any

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用

os.environ.setdefault("JWT_SIGNING_SECRET", "e2e-test-jwt-secret-value-32b")
os.environ.setdefault("KERNEL_HOST_TOKEN", "t")
os.environ.setdefault("KERNEL_MODEL_GATEWAY_SECRET", "t")
os.environ.setdefault("KERNEL_TOOL_GATEWAY_SECRET", "t")
os.environ.setdefault("ARTIFACT_URL_SECRET", "t")


# ==========================================================================
# 内存假集合（含真实 ObjectId 形态）
# ==========================================================================


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
    """内存集合，**按 ``core/mongo.INDEX_SPECS`` 真实模拟唯一索引语义**。

    ★ 必须模拟的原因：Mongo 对唯一索引把**缺失字段视为 null**。不模拟这点，
    端到端测试就测不出"重复 code → E11000 → 500"这类缺陷——第二次插入会静默成功，
    让人误以为修复生效。这正是"用假集合测不出真实数据形态"的又一次重演。
    索引规格直接取自 ``app.core.mongo.INDEX_SPECS``，与生产**同源**（不手抄）。
    """

    def __init__(self, name: str = "coll", unique_fields: list[str] | None = None) -> None:
        self.name = name
        self.docs: list[dict[str, Any]] = []
        self.unique_fields = unique_fields or []

    def _uniq_key(self, doc: dict[str, Any]) -> tuple:
        # ★ 缺失字段 → None（Mongo 真实语义）
        return tuple(doc.get(f) for f in self.unique_fields)

    def _assert_unique(self, candidate: dict[str, Any], *, ignore: Any = None) -> None:
        if not self.unique_fields:
            return
        key = self._uniq_key(candidate)
        for d in self.docs:
            if d is ignore:
                continue
            if self._uniq_key(d) == key:
                from pymongo.errors import DuplicateKeyError

                raise DuplicateKeyError(
                    f"E11000 duplicate key error collection: {self.name} dup key: {key}"
                )

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

    async def replace_one(self, query: dict[str, Any], doc: dict[str, Any], *a: Any, **k: Any):
        existing = await self.find_one(query)
        if existing is not None:
            existing.clear()
            existing.update(doc)
        else:
            self.docs.append(dict(doc))
        return type("R", (), {"modified_count": 1})()

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *a: Any, **k: Any):
        doc = await self.find_one(query)
        if doc is not None and "$set" in update:
            merged = {**doc, **update["$set"]}
            self._assert_unique(merged, ignore=doc)
            doc.update(update["$set"])
        return type("R", (), {"modified_count": 1 if doc else 0, "upserted_id": None})()

    async def delete_one(self, query: dict[str, Any], *a: Any, **k: Any):
        doc = await self.find_one(query)
        if doc is not None:
            self.docs.remove(doc)
        return type("R", (), {"deleted_count": 1 if doc else 0})()

    async def count_documents(self, query: dict[str, Any] | None = None) -> int:
        if not query:
            return len(self.docs)
        return len([d for d in self.docs if self._match(d, query)])


def _unique_fields_for(name: str) -> list[str]:
    """从 ``app.core.mongo.INDEX_SPECS`` 取该集合的全部唯一索引字段（并集）。"""
    from app.core.mongo import INDEX_SPECS

    out: set[str] = set()
    for coll, specs in INDEX_SPECS:
        if coll != name:
            continue
        for spec in specs:
            if spec.get("unique"):
                out.update(k for k, _ in spec.get("keys", []))
    return sorted(out)


class FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        # 索引规格与生产同源，不手抄
        return self.collections.setdefault(
            name, FakeCollection(name, _unique_fields_for(name))
        )


# ==========================================================================
# 端到端测试
# ==========================================================================


class EndToEndAdminFlowTest(unittest.TestCase):
    """管理员完整链路：登录 → /auth/me → 管理端读 → 改品牌 → 读授权列表。"""

    @classmethod
    def setUpClass(cls) -> None:
        from bson import ObjectId

        cls.ObjectId = ObjectId
        cls.db = FakeDB()

        # ---- 真实形态数据：ObjectId 类型的 _id / subject_id ----
        admin_uid = ObjectId()
        member_uid = ObjectId()
        # ★ 预置 code="default" 的根组织单元，让 lifespan 的 seed_identity() 识别
        #   "根已存在"而跳过，不会另建 default 组织、也不会再插一份同名用户。
        #   注意 org 过滤用的是 **str** 形态（RequestContext.org_unit_id 来自 JWT claim），
        #   所以子文档的 org_unit_id 一律用 str(...)——与 seed 的写入形态一致。
        org_oid = ObjectId()
        org_id = str(org_oid)
        cls.org_id = org_id
        cls.db["org_units"].docs.append(
            {
                "_id": org_oid,
                "name": "默认组织",
                "code": "default",
                "parent_id": None,
                "status": "active",
            }
        )

        cls.db["users"].docs.append(
            {
                "_id": admin_uid,
                "org_unit_id": org_id,
                "username": "admin",
                "display_name": "系统管理员",
                "status": "active",
            }
        )
        cls.db["users"].docs.append(
            {
                "_id": member_uid,
                "org_unit_id": org_id,
                "username": "member",
                "display_name": "普通成员",
                "status": "active",
            }
        )
        cls.db["role_bindings"].docs.append(
            {
                "_id": ObjectId(),
                "org_unit_id": org_id,
                "subject_type": "user",
                "subject_id": admin_uid,  # ★ ObjectId —— 缺陷 B 的触发点
                "role_code": "admin",
                "scope": "org_unit",
                "resource_type": None,
                "resource_id": None,
            }
        )
        cls.db["role_bindings"].docs.append(
            {
                "_id": ObjectId(),
                "org_unit_id": org_id,
                "subject_type": "user",
                "subject_id": member_uid,
                "role_code": "member",
                "scope": "org_unit",
                "resource_type": None,
                "resource_id": None,
            }
        )
        cls.db["roles"].docs.append(
            {
                "_id": ObjectId(),
                "org_unit_id": org_id,
                "code": "admin",
                "name": "管理员",
                "permissions": ["brand.manage", "user.manage", "model.manage"],
            }
        )
        cls.db["tools"].docs.append(
            {
                "_id": ObjectId(),
                "org_unit_id": org_id,
                "name": "http_fetch",
                "description": "抓取网页",
                "category": "server",
                "status": "active",
            }
        )
        cls.db["branding_settings"].docs.append(
            {
                "_id": "singleton",
                "display_name": "旧公司名",
                "short_name": "旧",
                "theme": {"primary": "#111111", "accent": "#222222"},
                "vendor_badge_enabled": True,
            }
        )

        # ---- 桩驱动层（只桩这些；被测逻辑走真实源码）----
        import app.core.mongo as mongo_mod

        cls._orig_get_collection = mongo_mod.get_collection
        cls._orig_ensure = mongo_mod.ensure_indexes
        mongo_mod.get_collection = lambda name: cls.db[name]  # type: ignore[assignment]
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

        cls._app = create_app()
        cls.client = TestClient(cls._app)
        # ★ 进 lifespan（触发 seed 与事件接线），让被测的是"真实启动后"的系统
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

    # ---------------- helpers ----------------

    def _login(self, username: str, password: str = "x") -> dict[str, str]:
        resp = self.client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # ---------------- 测试 ----------------

    def test_01_admin_login_yields_admin_role(self):
        """验收 1：管理员登录 → /auth/me 的 roles 非空（含 admin）。"""
        headers = self._login("admin")
        resp = self.client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        roles = resp.json()["roles"]
        self.assertIn("admin", roles, f"管理员 roles 应含 admin，实际 {roles}")

    def test_02_admin_list_endpoints_return_200(self):
        """验收 2：管理员打管理端读端点 → 200 + 真实数据（不是 403）。"""
        headers = self._login("admin")
        for path, expect_key in (
            ("/api/v1/admin/users", "items"),
            ("/api/v1/admin/tools", "items"),
            ("/api/v1/admin/model-providers", "items"),
            ("/api/v1/admin/role-bindings", "items"),
        ):
            with self.subTest(path=path):
                resp = self.client.get(path, headers=headers)
                self.assertEqual(resp.status_code, 200, f"{path} → {resp.status_code} {resp.text}")
                self.assertIn(expect_key, resp.json())

    def test_03_patch_branding_succeeds_and_public_reflects(self):
        """验收 3：★ PATCH /admin/branding 200（缺陷 A），且公开端点回读到新值。

        缺陷 A 实测是 422 `{"loc": ["query","body"]}`——body 被当成 query 参数。
        这里用 `json=` 发 body，再回读公开端点确认真的落库生效。
        """
        headers = self._login("admin")
        resp = self.client.patch(
            "/api/v1/admin/branding",
            json={"display_name": "实测公司名"},
            headers=headers,
        )
        self.assertEqual(
            resp.status_code, 200, f"PATCH branding 应 200，实际 {resp.status_code}: {resp.text}"
        )
        self.assertEqual(resp.json()["display_name"], "实测公司名")

        # 公开端点（免 JWT）必须能回读到新值 —— 证明真落库而非仅返回值
        pub = self.client.get("/api/v1/branding")
        self.assertEqual(pub.status_code, 200)
        self.assertEqual(
            pub.json()["display_name"], "实测公司名", "公开端点未反映新值，说明没真正落库"
        )

    def test_04_get_branding_allowed_for_admin(self):
        """验收 3b：★ GET /admin/branding 200（缺陷 C：管理员曾被 403）。"""
        headers = self._login("admin")
        resp = self.client.get("/api/v1/admin/branding", headers=headers)
        self.assertEqual(
            resp.status_code, 200, f"管理员读品牌配置应 200，实际 {resp.status_code}: {resp.text}"
        )

    def test_05_role_bindings_ids_are_strings(self):
        """★ 缺陷 B 直接回归：role_bindings 含 ObjectId 时端点仍 200 且 id 是字符串。

        修复前：PydanticSerializationError → 500（因为 items 非空才触发）。
        """
        headers = self._login("admin")
        resp = self.client.get("/api/v1/admin/role-bindings", headers=headers)
        self.assertEqual(
            resp.status_code, 200, f"role-bindings 应 200，实际 {resp.status_code}: {resp.text}"
        )
        items = resp.json()["items"]
        self.assertTrue(items, "应有两条绑定数据")
        for row in items:
            self.assertIsInstance(row["_id"], str, "_id 必须是字符串")
            self.assertIsInstance(
                row["subject_id"], str, "subject_id 必须是字符串（原来是 ObjectId）"
            )
            self.assertIn(row["role_code"], {"admin", "ops", "member"})

    def test_06_member_write_denied_but_not_all_denied(self):
        """验收 4：普通成员 roles=['member'] → 写 403，但不是所有操作都 403。"""
        headers = self._login("member")
        me = self.client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["roles"], ["member"])

        # 写操作被拒
        write = self.client.post(
            "/api/v1/admin/model-providers",
            json={"name": "x", "code": "x", "base_url": "u"},
            headers=headers,
        )
        self.assertEqual(write.status_code, 403, "普通成员写管理端应 403")

        # 读公开端点不受影响（不是"什么都 403"）
        pub = self.client.get("/api/v1/branding")
        self.assertEqual(pub.status_code, 200, "公开端点应始终可读")
        # 成员能过 auth/me（鉴权本身通）
        self.assertEqual(me.status_code, 200)

    def test_07_admin_write_endpoint_persists_and_invalidates(self):
        """验收 5：管理员 POST /admin/model-providers 落库（invalidate 由后端触发）。"""
        headers = self._login("admin")
        before = len(self.db["model_providers"].docs)
        resp = self.client.post(
            "/api/v1/admin/model-providers",
            json={
                "name": "实测供应商",
                "code": "e2e-provider",
                "base_url": "https://api.example.internal",
                "provider_type": "openai_compatible",
                "status": "active",
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(self.db["model_providers"].docs), before + 1, "应真落库一条")
        # 敏感字段不得经写端点落库
        stored = self.db["model_providers"].docs[-1]
        self.assertNotIn("api_key_enc", stored)

    def test_08_write_endpoint_rejects_sensitive_fields_silently_dropped(self):
        """写端点字段白名单：api_key_enc 不得落库（被丢弃而非报错）。"""
        headers = self._login("admin")
        resp = self.client.post(
            "/api/v1/admin/model-providers",
            json={"name": "n", "code": "c2", "api_key_enc": "x", "junk": 1},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        stored = self.db["model_providers"].docs[-1]
        self.assertNotIn("api_key_enc", stored)
        self.assertNotIn("junk", stored)


if __name__ == "__main__":
    unittest.main()
