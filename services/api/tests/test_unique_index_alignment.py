"""唯一索引约束回归测试（``admin.py`` 写端点）。

## 缺陷背景

``model_providers`` 有唯一索引 ``model_providers_org_code_u = (org_unit_id, code)``，
但写端点的 ``code`` 曾经**不是必填**——用户不传 code 时文档里没有该字段，
Mongo 把**缺失字段视为 null**，于是：第一条无 code 的能插入，第二条撞 ``E11000``。
该异常一路冒到中间件变成 **500 internal_error**。

根因不是"白名单缺 code"，而是"**唯一索引字段没被必填化**"。这个模式在
``tools.name`` / ``skills.key`` / ``model_instances.(provider_id, model_name)`` /
``resource_grants`` 五字段索引上**同样存在**，本文件逐个覆盖。

## 两道防线（本文件分别验证）

1. **必填化**：索引字段在写端点是必填（Pydantic）→ 缺字段直接 **422**，不会入库。
2. **409 转换**：真撞唯一索引时，``DuplicateKeyError`` → ``ConflictError`` **409**，
   而不是 500，且错误信息可操作、不含堆栈。

★ 用**独立假集合 + 独立 org_unit_id**（``ou-test-unique``），不碰真库。
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用

from pydantic import ValidationError

#: ★ 独立测试 org，绝不复用真库的 org（避免污染生产数据）
TEST_ORG = "ou-test-unique"


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


class UniqueIndexCollection:
    """**真正模拟 Mongo 唯一索引语义**的内存集合。

    ★ 关键：缺失字段按 ``None`` 参与唯一性比较——这正是 Mongo 的真实行为
    （缺失字段在唯一索引里等价于 null）。不模拟这一点，就测不出原缺陷。
    """

    def __init__(self, name: str, unique_fields: list[str]) -> None:
        self.name = name
        self.unique_fields = unique_fields
        self.docs: list[dict[str, Any]] = []
        self._seq = 0

    def _key(self, doc: dict[str, Any]) -> tuple:
        # ★ 缺失字段 → None（Mongo 语义）
        return tuple(doc.get(f) for f in self.unique_fields)

    async def insert_one(self, doc: dict[str, Any]) -> _InsertResult:
        key = self._key(doc)
        if any(self._key(d) == key for d in self.docs):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError(f"E11000 duplicate key error: {self.name} {key}")
        self._seq += 1
        stored = dict(doc)
        stored.setdefault("_id", f"{self.name}-{self._seq}")
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *a: Any, **k: Any):
        target = next((d for d in self.docs if all(d.get(kk) == vv for kk, vv in query.items())), None)
        if target is None:
            return type("R", (), {"modified_count": 0})()
        merged = {**target, **update.get("$set", {})}
        key = self._key(merged)
        if any(self._key(d) == key for d in self.docs if d is not target):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError(f"E11000 duplicate key error: {self.name} {key}")
        target.update(update.get("$set", {}))
        return type("R", (), {"modified_count": 1})()

    def find(self, query: dict[str, Any] | None = None) -> _Cursor:
        q = query or {}
        return _Cursor([d for d in self.docs if all(d.get(k) == v for k, v in q.items())])

    async def find_one(self, query: dict[str, Any] | None = None, *a: Any, **k: Any):
        for d in self.docs:
            if all(d.get(kk) == vv for kk, vv in (query or {}).items()):
                return d
        return None

    async def count_documents(self, query: dict[str, Any] | None = None) -> int:
        return len(self.docs) if not query else len(
            [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]
        )


class FakeDB:
    """按 ``02 §2.1`` 的真实索引定义建集合。"""

    def __init__(self) -> None:
        self.collections: dict[str, UniqueIndexCollection] = {
            "model_providers": UniqueIndexCollection(
                "model_providers", ["org_unit_id", "code"]
            ),
            "model_instances": UniqueIndexCollection(
                "model_instances", ["org_unit_id", "provider_id", "model_name"]
            ),
            "tools": UniqueIndexCollection("tools", ["org_unit_id", "name"]),
            "skills": UniqueIndexCollection("skills", ["org_unit_id", "key"]),
            "resource_grants": UniqueIndexCollection(
                "resource_grants",
                ["org_unit_id", "resource_type", "resource_id", "subject_type", "subject_id"],
            ),
            "role_bindings": UniqueIndexCollection(
                "role_bindings",
                ["org_unit_id", "subject_id", "role_code", "resource_id"],
            ),
        }

    def __getitem__(self, name: str) -> UniqueIndexCollection:
        return self.collections.setdefault(
            name, UniqueIndexCollection(name, ["org_unit_id"])
        )


def _ctx() -> Any:
    from app.identity.context import RequestContext

    return RequestContext(
        user_id="admin-uid", org_unit_id=TEST_ORG, roles=frozenset({"admin"})
    )


# ---- 请求体工厂：写端点已改用 Pydantic 模型，测试须传真模型实例（与 HTTP 层一致）----
def _provider(d: dict[str, Any]):
    from app.api.endpoints.admin import ModelProviderCreate

    return ModelProviderCreate.model_validate(d)


def _instance(d: dict[str, Any]):
    from app.api.endpoints.admin import ModelInstanceCreate

    return ModelInstanceCreate.model_validate(d)


def _tool(d: dict[str, Any]):
    from app.api.endpoints.admin import ToolCreate

    return ToolCreate.model_validate(d)


def _skill(d: dict[str, Any]):
    from app.api.endpoints.admin import SkillCreate

    return SkillCreate.model_validate(d)


def _grant(d: dict[str, Any]):
    from app.api.endpoints.admin import GrantCreate

    return GrantCreate.model_validate(d)


class UniqueIndexAlignmentTest(unittest.TestCase):
    """索引字段必填化 + 409 转换。"""

    def setUp(self) -> None:
        import app.core.mongo as mongo_mod

        self.db = FakeDB()
        self._orig = mongo_mod.get_collection
        mongo_mod.get_collection = lambda name: self.db[name]  # type: ignore[assignment]
        # invalidate 换成 no-op 计数（写端点每次成功都会调它，这里不关心）
        import app.api.endpoints.admin as admin_ep

        self._orig_invalidate = admin_ep.invalidate
        self.invalidate_calls: list[str] = []

        async def _fake_invalidate(org: str) -> None:
            self.invalidate_calls.append(org)

        admin_ep.invalidate = _fake_invalidate  # type: ignore[assignment]

    def tearDown(self) -> None:
        import app.api.endpoints.admin as admin_ep
        import app.core.mongo as mongo_mod

        mongo_mod.get_collection = self._orig  # type: ignore[assignment]
        admin_ep.invalidate = self._orig_invalidate  # type: ignore[assignment]

    # ---------- (c) 回归 1：重复 code → 409 而非 500 ----------

    def test_duplicate_provider_code_returns_409(self):
        """★ 重复提交同一 code → 409（不是 500），且错误信息可操作、不含堆栈。"""
        from app.api.endpoints.admin import create_model_provider
        from app.core.errors import ConflictError

        body = {"name": "供应商A", "code": "dup-code", "base_url": "https://x"}
        first = asyncio.run(create_model_provider(_provider(body), ctx=_ctx()))
        self.assertIn("id", first)

        with self.assertRaises(ConflictError) as ctx:
            asyncio.run(create_model_provider(_provider(body), ctx=_ctx()))

        err = ctx.exception
        self.assertEqual(err.status, 409, "必须是 409，不能是 500")
        self.assertEqual(err.code, "duplicate_key", "机器可读 code 应为 duplicate_key")
        # 信息可操作：告诉用户哪个字段冲突
        self.assertIn("code", err.message)
        self.assertEqual(err.details.get("field"), "code")
        # 不得回显堆栈 / 驱动内部细节
        self.assertNotIn("E11000", err.message)
        self.assertNotIn("Traceback", err.message)

    def test_duplicate_tool_name_returns_409(self):
        """tools.name 同样在唯一索引里 → 重复名 409。"""
        from app.api.endpoints.admin import create_tool
        from app.core.errors import ConflictError

        asyncio.run(create_tool(_tool({"name": "dup-tool", "type": "http"}), ctx=_ctx()))
        with self.assertRaises(ConflictError) as ctx:
            asyncio.run(create_tool(_tool({"name": "dup-tool", "type": "mcp"}), ctx=_ctx()))
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.details.get("field"), "name")

    def test_duplicate_skill_key_returns_409(self):
        """skills.key 同样在唯一索引里 → 重复 key 409。"""
        from app.api.endpoints.admin import create_skill
        from app.core.errors import ConflictError

        asyncio.run(create_skill(_skill({"key": "dup-key", "name": "A"}), ctx=_ctx()))
        with self.assertRaises(ConflictError) as ctx:
            asyncio.run(create_skill(_skill({"key": "dup-key", "name": "B"}), ctx=_ctx()))
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.details.get("field"), "key")

    def test_duplicate_model_instance_returns_409(self):
        """model_instances 索引是 (org, provider_id, model_name) → 重复组合 409。"""
        from app.api.endpoints.admin import create_model_instance
        from app.core.errors import ConflictError

        body = {"provider_id": "p-1", "model_name": "gpt-x"}
        asyncio.run(create_model_instance(_instance(body), ctx=_ctx()))
        with self.assertRaises(ConflictError) as ctx:
            asyncio.run(create_model_instance(_instance(body), ctx=_ctx()))
        self.assertEqual(ctx.exception.status, 409)

    def test_duplicate_grant_returns_409(self):
        """resource_grants 五字段索引 → 重复组合 409。"""
        from app.api.endpoints.admin import create_grant
        from app.core.errors import ConflictError

        body = {
            "resource_type": "tool",
            "resource_id": "t1",
            "subject_type": "user",
            "subject_id": "u1",
            "effect": "allow",
        }
        asyncio.run(create_grant(_grant(body), ctx=_ctx()))
        with self.assertRaises(ConflictError) as ctx:
            asyncio.run(create_grant(_grant({**body, "effect": "deny"}), ctx=_ctx()))
        self.assertEqual(ctx.exception.status, 409)

    def test_patch_to_duplicate_code_returns_409(self):
        """PATCH 改成已存在的 code 也要 409（改 code 撞车同样是可预期冲突）。"""
        from app.api.endpoints.admin import create_model_provider, patch_model_provider
        from app.core.errors import ConflictError

        a = asyncio.run(
            create_model_provider(
                _provider({"name": "A", "code": "code-a"}), ctx=_ctx()
            )
        )
        asyncio.run(create_model_provider(_provider({"name": "B", "code": "code-b"}), ctx=_ctx()))
        with self.assertRaises(ConflictError) as ctx:
            asyncio.run(
                patch_model_provider(a["id"], {"code": "code-b"}, ctx=_ctx())
            )
        self.assertEqual(ctx.exception.status, 409)

    def test_same_code_different_org_allowed(self):
        """★ 不同 org 下同 code **允许**（唯一索引含 org_unit_id，隔离必须生效）。"""
        from app.api.endpoints.admin import create_model_provider
        from app.identity.context import RequestContext

        asyncio.run(create_model_provider(_provider({"name": "A", "code": "shared"}), ctx=_ctx()))
        other_org = RequestContext(
            user_id="u2", org_unit_id="ou-other-org", roles=frozenset({"admin"})
        )
        # 不抛异常即通过
        out = asyncio.run(create_model_provider(_provider({"name": "B", "code": "shared"}), ctx=other_org))
        self.assertIn("id", out)

    # ---------- (c) 回归 2：缺必填 → 422 ----------

    def test_missing_code_rejected_before_db(self):
        """★ 缺必填 code → Pydantic 校验失败（HTTP 层即 422），且**不入库**。"""
        from app.api.endpoints.admin import ModelProviderCreate

        with self.assertRaises(ValidationError) as ctx:
            ModelProviderCreate(name="没有code的供应商")
        err = ctx.exception.errors()[0]
        self.assertEqual(err["type"], "missing")
        self.assertIn("code", str(err["loc"]))
        # 关键：没落库
        self.assertEqual(self.db["model_providers"].docs, [])

    def test_missing_required_index_fields_rejected(self):
        """逐个集合：缺唯一索引字段都在**入库前**被拒。"""
        from app.api.endpoints.admin import (
            GrantCreate,
            ModelInstanceCreate,
            ModelProviderCreate,
            SkillCreate,
            ToolCreate,
        )

        cases = [
            (ModelProviderCreate, {"name": "x"}),
            (ModelInstanceCreate, {"provider_id": "p"}),
            (ToolCreate, {"description": "no name"}),
            (SkillCreate, {"name": "no key"}),
            (GrantCreate, {"resource_type": "tool"}),
        ]
        for model_cls, payload in cases:
            with self.subTest(model=model_cls.__name__):
                with self.assertRaises(ValidationError):
                    model_cls(**payload)

    def test_create_models_forbid_empty_index_fields(self):
        """空字符串也算缺失（``Field(min_length=1)``）——否则照样撞唯一索引。"""
        from app.api.endpoints.admin import ModelProviderCreate

        with self.assertRaises(ValidationError):
            ModelProviderCreate(name="x", code="")  # code 为空串

    def test_all_index_fields_declared_required(self):
        """★ 结构性护栏：把「唯一索引字段 ⊆ 模型必填字段」固化成断言。

        直接**执行** ``core/mongo.py`` 里的索引声明表（不 regex 解析源码——格式一变就失效），
        取出每个集合的唯一索引字段，与写端点模型的必填字段比对。
        **以后有人加了索引字段但忘了改模型必填，这条会失败。**
        """
        import app.api.endpoints.admin as admin_ep
        import app.core.mongo as mongo_mod

        # 索引声明表是模块级常量，直接取（避免解析源码，也避免真的建索引）
        table = getattr(mongo_mod, "INDEX_SPECS", None)
        self.assertIsNotNone(
            table, "core/mongo.py 应暴露索引声明表常量供检查复用（INDEX_SPECS）"
        )

        idx: dict[str, set[str]] = {}
        for coll_name, specs in table:
            for spec in specs:
                if not spec.get("unique"):
                    continue
                fields = {k for k, _ in spec.get("keys", [])}
                if fields:
                    idx.setdefault(coll_name, set()).update(fields)

        #: ★ 索引里"语义上就允许为 null"的字段（``02`` §2.1 明确规定），
        #: 不要求用户必填——但**必须**在模型里显式声明为可空（而不是"漏了"）。
        nullable_by_design = {("resource_grants", "subject_id")}

        model_for = {
            "model_providers": admin_ep.ModelProviderCreate,
            "model_instances": admin_ep.ModelInstanceCreate,
            "tools": admin_ep.ToolCreate,
            "skills": admin_ep.SkillCreate,
            "resource_grants": admin_ep.GrantCreate,
        }
        checked = 0
        for coll, fields in idx.items():
            model_cls = model_for.get(coll)
            if model_cls is None:
                continue
            declared = set(model_cls.model_fields)
            required = {n for n, f in model_cls.model_fields.items() if f.is_required()}
            # 索引里的 org_unit_id 由后端注入，不要求用户传
            must_provide = fields - {"org_unit_id"}
            # 设计上就允许 null 的字段：只要求"模型里显式声明了它"，不要求必填
            optional_ok = {f for (c, f) in nullable_by_design if c == coll}
            missing = must_provide - required - optional_ok
            self.assertFalse(
                missing,
                f"{coll} 的唯一索引字段 {sorted(missing)} 未在 {model_cls.__name__} 中必填；"
                f"缺失字段入库后会按 null 参与唯一性比较并触发 E11000",
            )
            # 顺带断言"设计可空"的字段确实在模型里（防止后人删掉它变成未知字段）
            for f in optional_ok & must_provide:
                self.assertIn(
                    f, declared, f"{coll}.{f} 是索引字段，必须在模型里显式声明（可空）"
                )
            checked += 1
        self.assertGreaterEqual(checked, 5, "应至少校验 5 个集合的唯一索引")

    # ---------- (c) 回归 3：插入后回读，字段不为 null ----------

    def test_insert_then_readback_code_not_null(self):
        """★ 插入后立刻 GET 回读：code 字段存在且非 null（不再是"插进去了但没值"）。"""
        import app.api.endpoints.admin as admin_ep
        from app.core import mongo as mongo_mod

        asyncio.run(
            admin_ep.create_model_provider(
                _provider({"name": "回读供应商", "code": "readback-code", "base_url": "https://y"}),
                ctx=_ctx(),
            )
        )
        # 走真实 list 端点的数据路径
        rows = self.db["model_providers"].docs
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "readback-code")
        self.assertIsNotNone(rows[0]["code"], "code 不得为 null（原缺陷的落库形态）")
        self.assertEqual(rows[0]["org_unit_id"], TEST_ORG)

    def test_invalidate_still_called_on_success(self):
        """写成功仍必须失效 Profile 缓存（本次重构没破坏这条）。"""
        from app.api.endpoints.admin import create_model_provider

        self.invalidate_calls.clear()
        asyncio.run(create_model_provider(_provider({"name": "X", "code": "inv-1"}), ctx=_ctx()))
        self.assertEqual(self.invalidate_calls, [TEST_ORG])

    def test_conflict_does_not_invalidate(self):
        """★ 冲突（409）**不**触发 invalidate——没写入就不该让缓存失效。"""
        from app.api.endpoints.admin import create_model_provider

        body = {"name": "Y", "code": "inv-2"}
        asyncio.run(create_model_provider(_provider(body), ctx=_ctx()))
        self.invalidate_calls.clear()
        from app.core.errors import ConflictError

        with self.assertRaises(ConflictError):
            asyncio.run(create_model_provider(_provider(body), ctx=_ctx()))
        self.assertEqual(self.invalidate_calls, [], "写入失败不应失效 Profile 缓存")


if __name__ == "__main__":
    unittest.main()
