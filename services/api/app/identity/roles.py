"""角色与绑定（``identity/roles.py``，见 ``02`` §2.1 集合 ``roles`` / ``role_bindings``）。

阶段 1 主服务身份模块。
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from app.core.errors import NotFound
from app.identity.context import RequestContext
from app.identity.repository import ScopedRepository

ROLES_COLLECTION = "roles"
ROLE_BINDINGS_COLLECTION = "role_bindings"


class RolesRepository(ScopedRepository[dict[str, Any]]):
    """角色定义数据访问。"""

    def __init__(self, collection: AsyncIOMotorCollection | None = None) -> None:
        if collection is None:
            from app.core import mongo

            collection = mongo.get_collection(ROLES_COLLECTION)
        super().__init__(collection)

    async def get_by_code(self, ctx: RequestContext, code: str) -> dict[str, Any]:
        """按角色码查询角色（org 隔离）。"""
        doc = await self.find_one(ctx, code=code)
        if doc is None:
            raise NotFound(f"角色不存在: {code}")
        return doc

    async def permissions_for_role(self, ctx: RequestContext, role_code: str) -> frozenset[str]:
        """返回某角色码携带的权限集合。"""
        role = await self.get_by_code(ctx, role_code)
        return frozenset(role.get("permissions", []) or [])

    async def expand_permissions(
        self, ctx: RequestContext, role_codes: list[str] | set[str] | frozenset[str]
    ) -> frozenset[str]:
        """把一组**角色码**展开成**权限码**并集（``02`` §2.1``roles.permissions``）。

        ★ 这是"角色 → 授权"的**唯一展开点**：``role_bindings`` 给的是角色码，
        而各端点 guard 校验的是权限码（如 ``tool.manage``）。不在这里展开，
        非 admin 的运维角色会全站 403（只有 admin 靠超级用户直通侥幸通过）。

        - 查不到定义的 ``role_code`` **静默跳过**（不报错）：角色定义可被后续
          补建，已发的 token 不应因此失效。
        - org 隔离：只取当前 org 下的角色定义（跨 org 同名角色不串味）。
        - 只查一次（``$in``），避免 N 次往返。
        """
        codes = {str(c) for c in role_codes if c}
        if not codes:
            return frozenset()
        rows = await self._collection.find(
            {"org_unit_id": ctx.org_unit_id, "code": {"$in": sorted(codes)}}
        ).to_list(length=None)
        out: set[str] = set()
        for doc in rows:
            out.update(str(p) for p in (doc.get("permissions") or []) if p)
        return frozenset(out)


class RoleBindingsRepository(ScopedRepository[dict[str, Any]]):
    """角色绑定（用户↔角色，可到资源级）数据访问。"""

    def __init__(self, collection: AsyncIOMotorCollection | None = None) -> None:
        if collection is None:
            from app.core import mongo

            collection = mongo.get_collection(ROLE_BINDINGS_COLLECTION)
        super().__init__(collection)

    async def bind(
        self,
        ctx: RequestContext,
        *,
        subject_id: str,
        role_code: str,
        scope: str = "org_unit",
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """为用户或角色（subject_type=role）绑定角色码。

        参数
        ----
        subject_id:
            主体 id（user_id 或 role id）。
        role_code:
            绑定的角色码。
        scope:
            ``org_unit``（全组织）或 ``resource``（资源级）。
        resource_type / resource_id:
            scope=resource 时必填。
        """
        from bson import ObjectId

        doc = {
            "subject_type": "user",
            "subject_id": ObjectId(subject_id) if ObjectId.is_valid(subject_id) else subject_id,
            "role_code": role_code,
            "scope": scope,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "created_at": _utcnow(),
        }
        await self._collection.update_one(
            {
                "org_unit_id": ctx.org_unit_id,
                "subject_id": doc["subject_id"],
                "role_code": role_code,
                "resource_id": resource_id,
            },
            {"$set": doc},
            upsert=True,
        )

    async def roles_for_user(
        self, ctx: RequestContext, user_id: str, *, org_scope_only: bool = True
    ) -> list[str]:
        """返回用户绑定的角色码列表。

        参数
        ----
        org_scope_only:
            仅返回 org_unit 级绑定（默认 True）；False 时含资源级。

        ★ org 过滤用 ``$in`` 同时匹配 **str 与 ObjectId** 两种 ``org_unit_id`` 形态：
        ``RequestContext.org_unit_id`` 是字符串（来自 JWT claim），而集合里可能是
        ObjectId（``02`` §2.1`` 规定 org_unit_id 为 ObjectId``）也可能是历史遗留的
        字符串（如 ``identity/seed.py`` 写入值）。只按单一形态等值匹配会**静默漏查**
        ——表现是"登录成功但 roles 为空"，极难定位。

        ⚠️ **写入侧必须与 ``ctx.org_unit_id`` 同为 str**（结论，勿轻易改）
        曾尝试让 ``identity/seed.py`` 写**原始 ObjectId** 以"对齐 ``02`` §2.1 的 schema"，
        结果**回归**了已验证通过的真库环境：登录解析角色、ACL 隔离全是拿
        ``ctx.org_unit_id``（恒为 str，来自 JWT claim）做等值匹配，写入侧改成
        ObjectId 后两边类型对不上，绑定查不到、登录后 roles 恒空。
        结论：**``RequestContext.org_unit_id`` 是 str 是全链路契约**，
        任何写入 ``org_unit_id`` 的地方都应写 str；真要统一到 ObjectId，
        必须**连同 ctx 的类型与全部按 org 的查询**一起改，那是独立的迁移任务。
        """
        from bson import ObjectId

        filters: dict[str, Any] = {
            "subject_type": "user",
            "subject_id": ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id,
        }
        if org_scope_only:
            filters["scope"] = "org_unit"
        org_variants: list[Any] = [ctx.org_unit_id]
        if ObjectId.is_valid(ctx.org_unit_id):
            org_variants.append(ObjectId(ctx.org_unit_id))
        cursor = self._collection.find(
            {"org_unit_id": {"$in": org_variants}, **filters}
        )
        rows = await cursor.to_list(length=None)
        return [r.get("role_code") for r in rows if r.get("role_code")]


def _utcnow():
    from app.core.clock import utcnow

    return utcnow()
