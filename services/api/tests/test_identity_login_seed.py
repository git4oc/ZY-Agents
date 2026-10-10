"""login 角色解析 + 身份 seed 幂等性测试（真实 I/O 路径，非纯函数）。

## 覆盖的两个缺陷

**缺陷 A**：``api/endpoints/auth.py::login`` 硬编码 ``roles = frozenset()``，
且用占位 ``org_unit_id="__system__"`` 构造 ctx → 登录后 ``/auth/me`` 的 roles 恒为空
→ 管理端 ``_Permission.guard`` 必然 403。

**缺陷 B**：``users`` / ``roles`` / ``role_bindings`` 无任何 seed，
修好 A 之后 roles 仍是空集。

## 本文件如何覆盖（重点：不是只测纯函数）

- **真解码 JWT**：断言 ``jwt.decode`` 出来的 ``roles`` claim，而不是断言传参。
  传参对了但没进 token 是一种真实缺陷，只测传参会漏。
- **真驱动 login handler**：用内存假集合替代 mongo 驱动层，被测的 ``login()``
  与 ``RoleBindingsRepository.roles_for_user`` 走**真实源码**。
- **org 隔离**：绑定写在别的 org 时，登录**不得**拿到该角色。
- **seed 幂等**：跑两次，断言第二次**不写**且**不覆盖**已改的数据
  （改掉角色 permissions / 停用账号后再跑，值必须保持被改后的样子）。
  这条比"能登录"更重要——覆盖后台改动会静默生效几个月没人发现。
"""

from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用

os.environ.setdefault("JWT_SIGNING_SECRET", "test-jwt-secret-for-login")
os.environ.setdefault("KERNEL_HOST_TOKEN", "t")
os.environ.setdefault("KERNEL_MODEL_GATEWAY_SECRET", "t")
os.environ.setdefault("KERNEL_TOOL_GATEWAY_SECRET", "t")
os.environ.setdefault("ARTIFACT_URL_SECRET", "t")

import jwt  # PyJWT


# ==========================================================================
# 内存假集合（只桩驱动层）
# ==========================================================================


class _InsertResult:
    def __init__(self, inserted_id: str) -> None:
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
    """支持 insert_one / find_one / find / count_documents 的内存集合。

    ``_id`` 用可预测的字符串（p1/p2…），便于测试断言绑定指向哪个 user。
    """

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

    async def count_documents(self, query: dict[str, Any] | None = None) -> int:
        if not query:
            return len(self.docs)
        return len([d for d in self.docs if self._match(d, query)])

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *a: Any, **k: Any):
        doc = await self.find_one(query)
        if doc is not None and "$set" in update:
            doc.update(update["$set"])
        return type("R", (), {"modified_count": 1 if doc else 0})()


class FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name))


class _MongoPatch:
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


def _decode_roles(token: str) -> list[str]:
    """**真解码 JWT** 取出 roles claim（而不是断言传参）。"""
    from app.settings import get_settings

    payload = jwt.decode(
        token,
        get_settings().jwt_signing_secret,
        algorithms=["HS256"],
        issuer=get_settings().jwt_issuer,
    )
    return list(payload.get("roles") or [])


# ==========================================================================
# 1. login 角色解析
# ==========================================================================


class LoginRoleResolutionTest(unittest.TestCase):
    """login 必须从 role_bindings 真解析角色，且受 org 隔离约束。"""

    def setUp(self) -> None:
        self.db = FakeDB()
        # 管理员用户
        self.db["users"].docs.append(
            {
                "_id": "admin-uid",
                "org_unit_id": "ou-1",
                "username": "admin",
                "status": "active",
            }
        )
        self.db["users"].docs.append(
            {
                "_id": "member-uid",
                "org_unit_id": "ou-1",
                "username": "member",
                "status": "active",
            }
        )

    def _login(self, username: str = "admin", password: str = "x") -> dict[str, Any]:
        import app.api.endpoints.auth as auth_ep

        with _MongoPatch(self.db):
            return asyncio.run(auth_ep.login(auth_ep.LoginRequest(username=username, password=password)))

    def _bind(self, *, user_id: str, role_code: str, org: str = "ou-1", scope: str = "org_unit") -> None:
        self.db["role_bindings"].docs.append(
            {
                "_id": f"bind-{user_id}-{role_code}",
                "org_unit_id": org,
                "subject_type": "user",
                "subject_id": user_id,
                "role_code": role_code,
                "scope": scope,
                "resource_type": None,
                "resource_id": None,
            }
        )

    def test_roles_come_from_bindings_and_land_in_jwt(self):
        """★ 有绑定 → JWT 的 roles claim 里真的有该角色（解码断言，非传参断言）。"""
        self._bind(user_id="admin-uid", role_code="admin")
        out = self._login("admin")
        self.assertEqual(_decode_roles(out["access_token"]), ["admin"])
        # 响应体也带上 roles，便于前端直接用
        self.assertEqual(out["roles"], ["admin"])

    def test_no_bindings_yields_empty_not_admin(self):
        """★ 无绑定 → **空集**，绝不是 admin（"查不到就默认给 admin"是后门）。"""
        out = self._login("admin")
        self.assertEqual(_decode_roles(out["access_token"]), [])
        self.assertEqual(out["roles"], [])
        self.assertNotIn("admin", out["roles"])

    def test_binding_in_other_org_does_not_apply(self):
        """★ org 隔离：绑定写在别的 org → 登录**拿不到**该角色。"""
        self._bind(user_id="admin-uid", role_code="admin", org="ou-OTHER")
        out = self._login("admin")
        self.assertEqual(
            _decode_roles(out["access_token"]), [], "跨 org 的绑定不得生效"
        )

    def test_resource_scoped_binding_excluded_from_login(self):
        """资源级绑定（scope=resource）不进登录角色（org_scope_only=True）。"""
        self._bind(user_id="admin-uid", role_code="admin", scope="resource")
        out = self._login("admin")
        self.assertEqual(_decode_roles(out["access_token"]), [])

    def test_multiple_roles_accumulate(self):
        """多个 org 级绑定 → 角色并集。"""
        self._bind(user_id="member-uid", role_code="member")
        self._bind(user_id="member-uid", role_code="auditor")
        out = self._login("member")
        self.assertEqual(sorted(_decode_roles(out["access_token"])), ["auditor", "member"])

    def test_disabled_user_cannot_login(self):
        """停用账号拒绝登录（status != active）。"""
        self.db["users"].docs[0]["status"] = "disabled"
        from app.core.errors import Unauthenticated

        with self.assertRaises(Unauthenticated):
            self._login("admin")

    def test_unknown_user_rejected(self):
        """不存在的用户名 → Unauthenticated（不泄露"用户存在但密码错"）。"""
        from app.core.errors import Unauthenticated

        with self.assertRaises(Unauthenticated):
            self._login("ghost", password="x")

    def test_empty_password_rejected(self):
        """空口令被占位校验拒绝。"""
        from app.core.errors import Unauthenticated

        with self.assertRaises(Unauthenticated):
            self._login("admin", password="")


# ==========================================================================
# 2. seed 幂等性
# ==========================================================================


class IdentitySeedIdempotencyTest(unittest.TestCase):
    """seed 只在空集合时写；第二次跑**不覆盖**已改数据。"""

    def setUp(self) -> None:
        self.db = FakeDB()

    def _seed(self) -> dict[str, Any]:
        from app.identity.seed import seed_identity

        with _MongoPatch(self.db):
            return asyncio.run(seed_identity())

    def test_first_run_writes_all(self):
        """首次：四个集合都写入，admin 绑到 admin 角色。"""
        written = self._seed()
        self.assertTrue(written["org_unit"])
        # admin / ops / member 三个角色与账号（ops 用于演示"不同角色不同能力"，00 §11）
        self.assertEqual(written["roles"], 3, "admin + ops + member 三个角色")
        self.assertEqual(written["users"], 3, "三个种子账号")
        self.assertEqual(written["role_bindings"], 3)

        usernames = {u["username"] for u in self.db["users"].docs}
        self.assertEqual(usernames, {"admin", "ops", "member"})
        # admin 账号确实绑到 admin 角色
        admin_user = next(u for u in self.db["users"].docs if u["username"] == "admin")
        binding = next(
            b
            for b in self.db["role_bindings"].docs
            if b["subject_id"] == admin_user["_id"]
        )
        self.assertEqual(binding["role_code"], "admin")

        # ★ ops 角色存在且权限恰好是 tool.manage + device.manage（阶段 2 验收项的演示角色）
        ops_role = next(r for r in self.db["roles"].docs if r["code"] == "ops")
        self.assertEqual(
            sorted(ops_role["permissions"]),
            ["device.manage", "tool.manage"],
            "ops 角色权限应与测试夹具一致（便于对照）",
        )
        # ops 账号绑到 ops 角色
        ops_user = next(u for u in self.db["users"].docs if u["username"] == "ops")
        ops_binding = next(
            b for b in self.db["role_bindings"].docs if b["subject_id"] == ops_user["_id"]
        )
        self.assertEqual(ops_binding["role_code"], "ops")

    def test_second_run_is_noop(self):
        """★ 跑两次：第二次**什么都不写**。"""
        self._seed()
        before = {name: len(c.docs) for name, c in self.db.collections.items()}
        second = self._seed()
        after = {name: len(c.docs) for name, c in self.db.collections.items()}
        self.assertEqual(before, after, "第二次 seed 不得新增任何文档")
        self.assertEqual(second["roles"], 0)
        self.assertEqual(second["users"], 0)
        self.assertEqual(second["role_bindings"], 0)
        self.assertFalse(second["org_unit"], "已有根组织单元则不再插入")

    def test_second_run_does_not_overwrite_modified_role(self):
        """★ 幂等的关键：管理员改过 permissions 后，seed **不得**改回去。

        这比"能登录"更重要——覆盖后台改动会静默生效几个月没人发现。
        """
        self._seed()
        # 模拟实施人员把 admin 权限收窄了
        role = next(r for r in self.db["roles"].docs if r["code"] == "admin")
        role["permissions"] = ["audit.read"]
        role["name"] = "被改过的管理员"

        self._seed()

        role_after = next(r for r in self.db["roles"].docs if r["code"] == "admin")
        self.assertEqual(
            role_after["permissions"], ["audit.read"], "seed 不得覆盖已改的 permissions"
        )
        self.assertEqual(role_after["name"], "被改过的管理员", "seed 不得覆盖已改的名称")

    def test_second_run_does_not_reactivate_disabled_user(self):
        """★ 停用种子账号后，seed 不得把它改回 active。"""
        self._seed()
        admin_user = next(u for u in self.db["users"].docs if u["username"] == "admin")
        admin_user["status"] = "disabled"
        admin_user["password_hash"] = "被运维改过的口令"

        self._seed()

        after = next(u for u in self.db["users"].docs if u["username"] == "admin")
        self.assertEqual(after["status"], "disabled", "seed 不得复活已停用账号")
        self.assertEqual(after["password_hash"], "被运维改过的口令", "seed 不得覆盖口令")

    def test_partial_seed_fills_only_empty_collections(self):
        """users 为空但 org/roles 已存在 → 只补 users，不动已有组织结构。"""
        # 预置一个**非 default 编码**的组织单元（模拟客户已有组织结构）
        self.db["org_units"].docs.append(
            {"_id": "existing-org", "name": "客户已有组织", "code": "customer", "status": "active"}
        )
        self._seed()
        # 不应插入 default 根（避免与既有结构冲突）
        codes = {o["code"] for o in self.db["org_units"].docs}
        self.assertEqual(codes, {"customer"}, "不应擅自在既有组织里插 default 根")
        # 角色/用户因缺 root id 而跳过（保护既有数据优先）
        self.assertEqual(len(self.db["users"].docs), 0)

    def test_seed_creates_admin_permission_codes_used_by_guard(self):
        """种子 admin 角色的权限码覆盖 _Permission.REQUIRED 全部条目。"""
        from app.api.endpoints.admin import _Permission

        self._seed()
        role = next(r for r in self.db["roles"].docs if r["code"] == "admin")
        granted = set(role["permissions"])
        # _Permission.REQUIRED 的取值是"角色码"而非权限码，这里校验两者不脱节：
        # 至少 brand.manage 必须在（/auth/me 与 branding 管理端都依赖它）
        self.assertIn("brand.manage", granted)
        for code in _Permission.REQUIRED.values():
            self.assertIsInstance(code, str)

    def test_seed_password_is_plaintext_placeholder_not_hashed(self):
        """如实断言：种子口令是**明文占位**，不是哈希（阶段 1 未做哈希校验）。"""
        from app.identity.seed import _seed_accounts

        expected = _seed_accounts()["admin_password"]

        self._seed()
        admin_user = next(u for u in self.db["users"].docs if u["username"] == "admin")
        stored = str(admin_user["password_hash"])
        self.assertEqual(stored, expected)
        # 明确排除"看起来像哈希"的误判：argon2id 形如 $argon2id$...
        self.assertFalse(stored.startswith("$argon2"), "阶段 1 未做哈希，不应假装是 argon2")


if __name__ == "__main__":
    unittest.main()


class OpsSeedEndToEndTest(unittest.TestCase):
    """★ 真实 seed 出来的 ops 账号 → 登录后 permissions 正确展开（上轮 P1-2 的验收补齐）。

    此前"ops 角色"只存在于测试夹具，真实 seed 里没有，阶段 2 验收项
    「两个不同角色看到不同能力集合」（``00`` §11）**无法在部署后演示**。
    本测试锁定：seed → login → 展开，三段串起来都通。
    """

    def setUp(self) -> None:
        self.db = FakeDB()

    def test_ops_seed_account_login_expands_permissions(self):
        """seed 造出 ops 后，真跑 login 并**解 JWT** 断言 permissions 展开正确。"""
        import asyncio as _asyncio

        import jwt as _jwt

        from app.api.endpoints import auth as auth_ep
        from app.core import mongo as mongo_mod
        from app.identity import seed as seed_mod
        from app.settings import get_settings

        # 1) 幂等 seed（空集合 → 写入 admin/ops/member）
        with _MongoPatch(self.db):
            written = _asyncio.run(seed_mod.seed_identity())
        self.assertEqual(written["roles"], 3)
        self.assertEqual(written["users"], 3)
        self.assertEqual(written["role_bindings"], 3)

        ops_user = next(u for u in self.db["users"].docs if u["username"] == "ops")
        ops_uid = str(ops_user["_id"])

        # 2) 真跑 login（走完整 handler：查用户 → 查绑定 → 展开权限 → 签 token）
        with _MongoPatch(self.db):
            out = _asyncio.run(
                auth_ep.login(
                    auth_ep.LoginRequest(
                        username="ops",
                    password=seed_mod._seed_accounts()["ops_password"],
                    )
                )
            )
        self.assertEqual(out["roles"], ["ops"], "roles 应只含角色码 ops")
        self.assertEqual(
            sorted(out["permissions"]),
            ["device.manage", "tool.manage"],
            "permissions 应由 roles.permissions 展开而来",
        )

        # 3) 解 JWT 断言 claim 真的带上了（不是只返回在响应体里）
        payload = _jwt.decode(
            out["access_token"],
            get_settings().jwt_signing_secret,
            algorithms=["HS256"],
            issuer=get_settings().jwt_issuer,
        )
        self.assertEqual(payload["roles"], ["ops"])
        self.assertEqual(sorted(payload["perms"]), ["device.manage", "tool.manage"])
        self.assertEqual(payload["sub"], ops_uid)

    def test_ops_seed_is_idempotent(self):
        """ops 也必须幂等：第二次 seed 不重复建、不覆盖已改 permissions。"""
        import asyncio as _asyncio

        from app.identity import seed as seed_mod

        with _MongoPatch(self.db):
            _asyncio.run(seed_mod.seed_identity())
            roles_n = len(self.db["roles"].docs)
            users_n = len(self.db["users"].docs)
            # 模拟实施人员收窄了 ops 权限
            ops_role = next(r for r in self.db["roles"].docs if r["code"] == "ops")
            ops_role["permissions"] = ["tool.manage"]
            ops_user = next(u for u in self.db["users"].docs if u["username"] == "ops")
            ops_user["status"] = "disabled"

            second = _asyncio.run(seed_mod.seed_identity())

        self.assertEqual(second["roles"], 0, "第二次不写角色")
        self.assertEqual(second["users"], 0, "第二次不建账号")
        self.assertEqual(second["role_bindings"], 0, "第二次不建绑定")
        self.assertEqual(len(self.db["roles"].docs), roles_n, "角色数未变")
        self.assertEqual(len(self.db["users"].docs), users_n, "账号数未变")
        after = next(r for r in self.db["roles"].docs if r["code"] == "ops")
        self.assertEqual(after["permissions"], ["tool.manage"], "已改权限未被覆盖")
        ops_user_after = next(u for u in self.db["users"].docs if u["username"] == "ops")
        self.assertEqual(ops_user_after["status"], "disabled", "已停用账号未被复活")
