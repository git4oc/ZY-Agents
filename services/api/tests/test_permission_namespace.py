"""权限码命名空间对齐 + role_bindings 必填 的 HTTP 层回归测试。

## P1-2（权限层此前"仅 admin 可用"）

``role_bindings`` 给的是**角色码**（``ops``），而端点 guard 校验的是**权限码**
（``tool.manage``）——中间缺了"角色码 → 权限码展开"，于是：

    ctx.roles={'ops'}（该角色定义了 tool.manage）
    → has_permission('tool.manage') == False → 管理端全站 403

只有 admin 靠"超级用户直通"侥幸通过，**权限层实际退化成仅 admin 可用**，
卡住阶段 2 验收项"两个不同角色的用户，看到的模型/知识/工具集合不同"（``00`` §11）。

修法：``login`` 时经 ``RolesRepository.expand_permissions`` 把角色定义里的
``permissions``（``02`` §2.1``roles.permissions``）展开进 **``ctx.permissions``**，
``acl.has_permission`` 只认该字段。``ctx.roles`` 回归纯角色码语义
（前端 ``/auth/me`` 的 ``roles`` 与 ``agentPolicy.roleIds`` 直接消费它）。

★ 用独立 org_unit_id（``ou-test-perm``），**不写真库**。
"""

from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用

os.environ.setdefault("JWT_SIGNING_SECRET", "perm-namespace-test-secret-32b")
os.environ.setdefault("KERNEL_HOST_TOKEN", "t")
os.environ.setdefault("KERNEL_MODEL_GATEWAY_SECRET", "t")
os.environ.setdefault("KERNEL_TOOL_GATEWAY_SECRET", "t")
os.environ.setdefault("ARTIFACT_URL_SECRET", "t")

#: ★ 独立测试 org
TEST_ORG = "ou-test-perm"

#: 角色定义（``02`` §2.1``roles.permissions``）——``ops`` 是本轮新增的非 admin 运维角色
ROLE_DEFS: dict[str, list[str]] = {
    "admin": [
        "org.manage", "user.manage", "model.manage", "tool.manage", "skill.manage",
        "device.manage", "audit.read", "quota.read", "acl.manage", "brand.manage",
    ],
    # ★ 非 admin 运维角色：只给 tool.manage + device.manage（**故意不给 user.manage**）
    "ops": ["tool.manage", "device.manage"],
    "member": ["knowledge.read"],
}


class _InsertResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    def skip(self, n: int | None) -> "_Cursor":
        # ★ governance/audit.py:120 链式调用 .find().sort().skip().limit()，
        #   缺 skip 会让 /admin/audit 在夹具下 500（假失败，非产品缺陷）。
        return _Cursor(self._rows[n:] if n else self._rows)

    def limit(self, n: int | None) -> "_Cursor":
        return _Cursor(self._rows[:n] if n else self._rows)

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._rows


class FakeCollection:
    def __init__(self, name: str = "coll") -> None:
        self.name = name
        self.docs: list[dict[str, Any]] = []

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
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False, *a: Any, **k: Any
    ):
        doc = await self.find_one(query)
        if doc is None and upsert:
            created = dict(query)
            created.update(update.get("$set", {}))
            created.setdefault("_id", f"{self.name}-{len(self.docs) + 1}")
            self.docs.append(created)
            return type("R", (), {"modified_count": 0})()
        if doc is not None and "$set" in update:
            doc.update(update["$set"])
        return type("R", (), {"modified_count": 1 if doc else 0})()

    async def replace_one(
        self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False, *a: Any, **k: Any
    ):
        """支持 replace_one（branding seed 用它幂等写单文档集合）。"""
        existing = await self.find_one(query)
        if existing is not None:
            existing.clear()
            existing.update(doc)
        elif upsert:
            self.docs.append(dict(doc))
        return type("R", (), {"modified_count": 1 if existing else 0, "upserted_id": None})()

    async def count_documents(self, query: dict[str, Any] | None = None) -> int:
        return (
            len(self.docs)
            if not query
            else len([d for d in self.docs if self._match(d, query)])
        )


class FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name))


def _ctx(roles: frozenset[str], permissions: frozenset[str]) -> Any:
    from app.identity.context import RequestContext

    return RequestContext(
        user_id="u", org_unit_id=TEST_ORG, roles=roles, permissions=permissions
    )


# ==========================================================================
# 1. 单元层：展开 + 判定
# ==========================================================================


class PermissionNamespaceTest(unittest.TestCase):
    """角色码 → 权限码展开，以及 guard 判定。"""

    def setUp(self) -> None:
        self.db = FakeDB()
        # 根组织单元（_id = TEST_ORG，str 形态，与 seed 写入一致）
        self.db["org_units"].docs.append(
            {"_id": TEST_ORG, "name": "测试组织", "code": "default", "status": "active"}
        )
        for code, perms in ROLE_DEFS.items():
            self.db["roles"].docs.append(
                {
                    "_id": f"role-{code}",
                    "org_unit_id": TEST_ORG,
                    "code": code,
                    "name": code,
                    "permissions": list(perms),
                }
            )

        import app.core.mongo as mongo_mod

        self._orig = mongo_mod.get_collection
        mongo_mod.get_collection = lambda n: self.db[n]  # type: ignore[assignment]

    def tearDown(self) -> None:
        import app.core.mongo as mongo_mod

        mongo_mod.get_collection = self._orig  # type: ignore[assignment]

    def test_expand_permissions_from_role_codes(self):
        """★ 角色码 → 权限码展开（``02`` §2.1``roles.permissions``）。"""
        from app.identity.roles import RolesRepository

        repo = RolesRepository(collection=self.db["roles"])
        ctx = _ctx(frozenset(), frozenset())
        perms = asyncio.run(repo.expand_permissions(ctx, ["ops"]))
        self.assertEqual(perms, frozenset({"tool.manage", "device.manage"}))

    def test_expand_multiple_roles_is_union(self):
        """多个角色的权限取**并集**。"""
        from app.identity.roles import RolesRepository

        repo = RolesRepository(collection=self.db["roles"])
        perms = asyncio.run(
            repo.expand_permissions(_ctx(frozenset(), frozenset()), ["ops", "member"])
        )
        self.assertEqual(
            perms, frozenset({"tool.manage", "device.manage", "knowledge.read"})
        )

    def test_expand_unknown_role_is_skipped_not_raised(self):
        """查不到定义的 role_code **静默跳过**（角色可后补，不应让已发 token 失效）。"""
        from app.identity.roles import RolesRepository

        repo = RolesRepository(collection=self.db["roles"])
        perms = asyncio.run(repo.expand_permissions(_ctx(frozenset(), frozenset()), ["ghost"]))
        self.assertEqual(perms, frozenset())

    def test_ops_role_passes_tool_guard_but_not_user_guard(self):
        """★★ 验收核心：ops 角色（有 tool.manage 无 user.manage）判定正确。"""
        from app.api.endpoints.admin import _Permission
        from app.identity.acl import has_permission

        ops = _ctx(frozenset({"ops"}), frozenset({"tool.manage", "device.manage"}))
        # 有 tool.manage → tools 放行
        _Permission.guard(ops, "tools")
        _Permission.guard(ops, "devices")
        # 无 user.manage → users / role-bindings 拒绝
        self.assertTrue(has_permission(ops, "tool.manage"))
        self.assertFalse(has_permission(ops, "user.manage"))
        from app.core.errors import Forbidden

        with self.assertRaises(Forbidden):
            _Permission.guard(ops, "users")

    def test_admin_still_passes_everything(self):
        """★ admin 仍全通（别把超级用户捷径修没了）。"""
        from app.api.endpoints.admin import _Permission

        admin = _ctx(frozenset({"admin"}), frozenset())
        for res in _Permission.REQUIRED:
            _Permission.guard(admin, res)  # 不抛异常即通过

    def test_member_cannot_access_admin_endpoints(self):
        """member 行为不变：只有 knowledge.read，管理端全拒。"""
        from app.api.endpoints.admin import _Permission
        from app.core.errors import Forbidden

        member = _ctx(frozenset({"member"}), frozenset({"knowledge.read"}))
        for res in ("tools", "users", "devices", "grants"):
            with self.subTest(resource=res):
                with self.assertRaises(Forbidden):
                    _Permission.guard(member, res)

    def test_permission_code_in_roles_no_longer_grants(self):
        """★ **不保留双命名空间兼容**：把权限码塞进 roles 不再能通过判定。

        这是"要么全改要么清楚不改"的护栏——若有人图省事让 has_permission
        同时认 ``ctx.roles``，命名空间错位会**静默复活**。
        """
        from app.api.endpoints.admin import _Permission
        from app.core.errors import Forbidden

        # 旧错法：权限码塞 roles、没有 permissions
        legacy = _ctx(frozenset({"tool.manage"}), frozenset())
        with self.assertRaises(Forbidden):
            _Permission.guard(legacy, "tools")


# ==========================================================================
# 2. HTTP 层：ops 账号真实登录并访问
# ==========================================================================


class OpsRoleHTTPTest(unittest.TestCase):
    """TestClient 走完整 ASGI 栈：ops 账号登录 → 按权限码放行/拒绝。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.db = FakeDB()
        cls.db["org_units"].docs.append(
            {"_id": TEST_ORG, "name": "测试组织", "code": "default", "status": "active"}
        )
        for code, perms in ROLE_DEFS.items():
            cls.db["roles"].docs.append(
                {
                    "_id": f"role-{code}",
                    "org_unit_id": TEST_ORG,
                    "code": code,
                    "name": code,
                    "permissions": list(perms),
                }
            )
        # 三个账号：admin / ops / member，各自绑定对应角色
        for uname, rcode in (("admin", "admin"), ("ops", "ops"), ("member", "member")):
            uid = f"uid-{uname}"
            cls.db["users"].docs.append(
                {
                    "_id": uid,
                    "org_unit_id": TEST_ORG,
                    "username": uname,
                    "display_name": uname,
                    "status": "active",
                }
            )
            cls.db["role_bindings"].docs.append(
                {
                    "_id": f"rb-{uname}",
                    "org_unit_id": TEST_ORG,
                    "subject_type": "user",
                    "subject_id": uid,
                    "role_code": rcode,
                    "scope": "org_unit",
                    "resource_type": None,
                    "resource_id": None,
                }
            )
        cls.db["tools"].docs.append(
            {"_id": "tool-1", "org_unit_id": TEST_ORG, "name": "http_fetch", "status": "active"}
        )

        import app.core.mongo as mongo_mod

        cls._orig_get = mongo_mod.get_collection
        cls._orig_ensure = mongo_mod.ensure_indexes
        mongo_mod.get_collection = lambda n: cls.db[n]  # type: ignore[assignment]
        mongo_mod.ensure_indexes = lambda: None  # type: ignore[assignment]

        import app.core.redis as redis_mod

        class _FakeRedis:
            async def publish(self, *a: Any, **k: Any) -> int:
                return 0

            async def aclose(self) -> None:
                return None

        cls._orig_rc = redis_mod.client
        cls._orig_rconn = redis_mod.connect
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

        mongo_mod.get_collection = cls._orig_get  # type: ignore[assignment]
        mongo_mod.ensure_indexes = cls._orig_ensure  # type: ignore[assignment]
        redis_mod.client = cls._orig_rc  # type: ignore[assignment]
        redis_mod.connect = cls._orig_rconn  # type: ignore[assignment]
        set_sink(None)
        set_event_handler(None)
        invalidate_cache()

    def _login(self, username: str) -> dict[str, str]:
        r = self.client.post(
            "/api/v1/auth/login", json={"username": username, "password": "x"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    def test_ops_login_expands_permissions(self):
        """★ ops 登录 → token 含 ops 角色 + 展开后的权限码（真解 JWT 断言）。"""
        import jwt

        from app.settings import get_settings

        r = self.client.post(
            "/api/v1/auth/login", json={"username": "ops", "password": "x"}
        )
        payload = jwt.decode(
            r.json()["access_token"],
            get_settings().jwt_signing_secret,
            algorithms=["HS256"],
            issuer=get_settings().jwt_issuer,
        )
        self.assertEqual(payload["roles"], ["ops"], "roles 应是纯角色码")
        self.assertEqual(
            sorted(payload["perms"]),
            ["device.manage", "tool.manage"],
            "perms 应是 roles.permissions 展开结果",
        )

    def test_ops_can_read_tools_but_not_users(self):
        """★★ 验收标准实测：ops → tools 200 / users 403。"""
        H = self._login("ops")
        tools = self.client.get("/api/v1/admin/tools", headers=H)
        users = self.client.get("/api/v1/admin/users", headers=H)
        self.assertEqual(tools.status_code, 200, f"ops 有 tool.manage，tools 应 200：{tools.text}")
        self.assertEqual(users.status_code, 403, f"ops 无 user.manage，users 应 403：{users.text}")

    def test_ops_write_allowed_only_for_granted_resources(self):
        """ops 写 tools 放行、写 model-providers 拒绝（按权限码，不是按角色名）。"""
        H = self._login("ops")
        # tool.manage → 写 tools 放行
        w_tool = self.client.patch(
            "/api/v1/admin/tools/tool-1", json={"status": "disabled"}, headers=H
        )
        self.assertEqual(w_tool.status_code, 200, w_tool.text)
        # 无 model.manage → 写 model-providers 拒绝
        w_prov = self.client.post(
            "/api/v1/admin/model-providers",
            json={"name": "x", "code": "x", "base_url": "u"},
            headers=H,
        )
        self.assertEqual(w_prov.status_code, 403, w_prov.text)

    def test_admin_still_all_pass(self):
        """★ admin 未被修复波及：tools/users/model-providers 全 200。"""
        H = self._login("admin")
        for path in ("/api/v1/admin/tools", "/api/v1/admin/users", "/api/v1/admin/model-providers"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=H).status_code, 200)

    def test_member_unchanged(self):
        """★ member 行为不变：管理端仍全 403。"""
        H = self._login("member")
        for path in ("/api/v1/admin/tools", "/api/v1/admin/users", "/api/v1/admin/devices"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=H).status_code, 403)

    def test_auth_me_separates_roles_and_permissions(self):
        """★ ``/auth/me`` 的 roles 与 permissions **分属两字段**、语义各自纯净。

        前端 ``agentPolicy.roleIds`` 与 apps/admin 侧边栏消费 ``roles``，
        若混入权限码会失真（apps/admin 侧边栏按 roles 判断是否显示黄条）。
        """
        H = self._login("ops")
        me = self.client.get("/api/v1/auth/me", headers=H).json()
        self.assertEqual(me["roles"], ["ops"], "roles 只应是角色码")
        self.assertEqual(
            sorted(me["permissions"]),
            ["device.manage", "tool.manage"],
            "permissions 应是展开后的权限码",
        )
        # roleIds 同样只含角色码
        self.assertEqual(me["agentPolicy"]["roleIds"], ["ops"])

    def test_two_roles_see_different_abilities(self):
        """★★ 阶段 2 验收项（``00`` §11）：不同角色拿到的权限集合不同。

        这是 P1-2 的**存在意义**——修前所有非 admin 角色都是"全站 403"，
        根本区分不出能力差异。
        """
        ops = self.client.get("/api/v1/auth/me", headers=self._login("ops")).json()
        member = self.client.get("/api/v1/auth/me", headers=self._login("member")).json()
        self.assertNotEqual(
            sorted(ops["permissions"]),
            sorted(member["permissions"]),
            "不同角色必须看到不同的权限集合",
        )


# ==========================================================================
# 3. role_bindings POST 必填校验
# ==========================================================================


class RoleBindingValidationTest(unittest.TestCase):
    """``create_role_binding`` 的必填与**条件必填**。"""

    def test_empty_body_rejected(self):
        """★ POST {} → 校验失败（修前落一条全空垃圾文档并回 200）。"""
        from pydantic import ValidationError

        from app.api.endpoints.admin import RoleBindingCreate

        with self.assertRaises(ValidationError):
            RoleBindingCreate.model_validate({})

    def test_missing_subject_id_rejected(self):
        """缺 subject_id → 拒（否则 `roles_for_user` 授不了权限的孤儿绑定）。"""
        from pydantic import ValidationError

        from app.api.endpoints.admin import RoleBindingCreate

        with self.assertRaises(ValidationError):
            RoleBindingCreate.model_validate({"role_code": "admin", "subject_type": "user"})

    def test_missing_role_code_rejected(self):
        """缺 role_code → 拒。"""
        from pydantic import ValidationError

        from app.api.endpoints.admin import RoleBindingCreate

        with self.assertRaises(ValidationError):
            RoleBindingCreate.model_validate({"subject_type": "user", "subject_id": "u1"})

    def test_resource_scope_requires_resource_fields(self):
        """★ 条件必填：``scope=resource`` 时 resource_type + resource_id 都必填。"""
        from pydantic import ValidationError

        from app.api.endpoints.admin import RoleBindingCreate

        with self.assertRaises(ValidationError):
            RoleBindingCreate.model_validate(
                {"subject_type": "user", "subject_id": "u1",
                 "role_code": "ops", "scope": "resource"}
            )
        with self.assertRaises(ValidationError):
            RoleBindingCreate.model_validate(
                {"subject_type": "user", "subject_id": "u1", "role_code": "ops",
                 "scope": "resource", "resource_type": "tool"}
            )
        # 两者都给 → 通过
        ok = RoleBindingCreate.model_validate(
            {"subject_type": "user", "subject_id": "u1", "role_code": "ops",
             "scope": "resource", "resource_type": "tool", "resource_id": "t1"}
        )
        self.assertEqual(ok.scope, "resource")

    def test_org_unit_scope_needs_no_resource_fields(self):
        """``scope=org_unit``（默认）不需要资源字段——别把正常路径也拦了。"""
        from app.api.endpoints.admin import RoleBindingCreate

        m = RoleBindingCreate.model_validate(
            {"subject_type": "user", "subject_id": "u1", "role_code": "ops"}
        )
        self.assertEqual(m.scope, "org_unit")
        self.assertIsNone(m.resource_id)


if __name__ == "__main__":
    unittest.main()
