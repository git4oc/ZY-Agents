"""资源 ACL 判定（``identity/acl.py``，见 ``01`` §4.1、``02`` §2.1 集合 ``resource_grants``）。

阶段 1 主服务身份模块。模型 / 知识库 / Skill / 工具 / 设备对用户的可见性。
"""

from __future__ import annotations

from typing import Any, Literal

from motor.motor_asyncio import AsyncIOMotorCollection

from app.core.errors import Forbidden
from app.identity.context import RequestContext

COLLECTION = "resource_grants"

#: 资源类型（``02`` §2.1）
ResourceType = Literal[
    "model_instance", "knowledge_base", "skill", "tool", "device"
]
#: 动作
Action = Literal["read", "use", "manage"]

#: 默认：无任何 allow 授予时，资源对用户不可见（fail-closed）。
_ACTION_DEFAULT_ALLOW: dict[str, bool] = {
    "read": False,
    "use": False,
    "manage": False,
}

#: 超级用户角色码：拥有该角色码即视为拥有**全部**权限（``00`` §8 治理层的隐含前提）。
#: 阶段 1 的 ``ctx.roles`` 装的是**角色码**（admin / member，来自 role_bindings），
#: 而各端点校验的是**权限码**（brand.manage / model.manage …）。若不显式承认
#: admin 角色码放行，管理员会在所有按权限码校验的端点上被误判 403。
SUPERUSER_ROLE = "admin"


def is_admin(ctx: RequestContext) -> bool:
    """是否超级用户（拥有 admin 角色码）。**权限判定的唯一口径**。"""
    return SUPERUSER_ROLE in ctx.roles


def has_permission(ctx: RequestContext, permission: str) -> bool:
    """当前上下文是否拥有某**权限码**。

    判定：拥有 ``admin`` 角色码（超级用户）**或** ``ctx.permissions`` 含该权限码。

    ★ **只认 ``ctx.permissions``，不再到 ``ctx.roles`` 里找权限码**（P1-2 修复）。
    早前实现是 ``permission in ctx.roles``，而 ``ctx.roles`` 装的是**角色码**
    （``admin`` / ``ops``）——**命名空间错位**：

        ctx.roles={'ops'}(该角色定义了 tool.manage)
        → has_permission('tool.manage') == False  → 管理端全站 403

    即"按 seed 方式给非 admin 运维角色授予 ``tool.manage``"仍会被所有管理端端点
    拒绝——权限层实际退化成"**仅 admin 可用**"，卡住阶段 2 验收项
    "两个不同角色的用户，看到的模型/知识/工具集合不同"（``00`` §11 阶段 2）。
    根因是**角色码未展开成权限码**。现改为：``login`` 时经
    ``RolesRepository.expand_permissions`` 展开进 ``ctx.permissions``，本函数只认它。

    **不保留"两个命名空间都能过"的兼容**：那会让权限判定有两种真相，后续维护更乱。
    ``ctx.roles`` 回归纯身份语义（前端 ``/auth/me`` 的 ``roles`` 与
    ``agentPolicy.roleIds`` 直接消费它，语义不能被权限码污染）。

    ⚠️ **admin 直通是否绕过本该对 admin 也生效的限制？——阶段 4 议题（已登记）**
    当前实现里 admin **等价于全权限**：``has_permission`` 一旦命中 admin 角色码
    就直接放行，**不区分**"管理类权限"与"底线约束类限制"。因此若将来引入
    "即便管理员也不得做"的红线类限制（如禁止删除审计日志、禁止跨租户读取），
    本函数**不会**拦住 admin。

    阶段 1 把这条捷径视为**预期**：私有化单租户交付里管理员本就该能配置一切。
    （``roles.permissions`` **已在登录时展开**进 ``ctx.permissions``——见上文；
    尚未细化的是"权限码的粒度与红线约束"，不是"有没有展开"。）

    **阶段 4 前置项（RBAC 细化时下沉）**：届时应拆成两层判定——
      1. ``has_permission``  = **权限码授权**判定（admin 也受约束）；
      2. 独立的 ``ensure_not_violating_hard_limit`` 之类**底线约束**，对 admin 同样生效。
    登记位置：``docs/00-architecture-spec.md`` §13.1 第 8 条。
    """
    if not permission:
        return True
    if is_admin(ctx):
        return True
    return permission in ctx.permissions


def require_permission(ctx: RequestContext, permission: str) -> None:
    """要求当前上下文拥有某权限码，否则抛 ``Forbidden``（**共享 guard**）。

    ★ 全项目**唯一**的权限判定入口。``api/endpoints/admin.py::_Permission.guard``、
    ``api/endpoints/branding.py::_require``、``branding/settings.py::update``
    都必须走这里——此前三处各写各的内联 ``if "x" not in ctx.roles``，
    导致 admin 角色码在 branding 侧被漏判（管理员 403），每加一个端点就可能重犯。
    """
    if has_permission(ctx, permission):
        return
    raise Forbidden(
        f"需要 {permission} 权限",
        details={"required": permission, "roles": sorted(ctx.roles)},
    )


def _role_ids(ctx: RequestContext) -> list[str]:
    return list(ctx.roles)


async def _subject_grants(
    collection: AsyncIOMotorCollection,
    *,
    org_unit_id: str,
    resource_type: str,
    resource_id: str,
    user_id: str,
    roles: list[str],
) -> list[dict[str, Any]]:
    """读取与当前主体相关的全部授权行（user / role / all）。"""
    from bson import ObjectId

    uid = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
    query = {
        "org_unit_id": org_unit_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "subject_type": {"$in": ["user", "role", "all"]},
        "subject_id": {"$in": [uid, None] + roles},
    }
    cursor = collection.find(query)
    return await cursor.to_list(length=None)


async def ensure(
    ctx: RequestContext,
    *,
    resource: ResourceType,
    resource_id: str,
    action: Action,
) -> None:
    """确保当前上下文对某资源具备指定动作权限。

    判定规则（``02`` §2.1）：
    - **deny 优先**：任一条 deny 命中即拒绝（私有化下"临时收回"必须立刻生效）。
    - 无 deny 时，任一条 allow 命中即放行。
    - 无任何 allow → 拒绝（fail-closed）。

    参数
    ----
    resource / resource_id:
        资源类型与资源 id。
    action:
        动作（read / use / manage）。

    异常
    ----
    Forbidden:
        无权限时抛出。
    """
    from app.core import mongo

    coll = mongo.get_collection(COLLECTION)
    grants = await _subject_grants(
        coll,
        org_unit_id=ctx.org_unit_id,
        resource_type=resource,
        resource_id=resource_id,
        user_id=ctx.user_id,
        roles=_role_ids(ctx),
    )

    granted = _ACTION_DEFAULT_ALLOW[action]
    for g in grants:
        effect = g.get("effect")
        if effect not in ("allow", "deny"):
            continue
        if effect == "deny":
            raise Forbidden(
                "资源访问被拒绝",
                details={"resource_type": resource, "resource_id": resource_id, "action": action},
            )
        if effect == "allow":
            granted = True

    if not granted:
        raise Forbidden(
            "资源访问被拒绝（未授权）",
            details={"resource_type": resource, "resource_id": resource_id, "action": action},
        )


async def visible_ids(
    ctx: RequestContext,
    *,
    resource: ResourceType,
    all_ids: set[str],
) -> set[str]:
    """对一组资源 id 做批量可见性过滤（用于列表类查询）。

    逐条判定成本高，这里用一条聚合查询预取全部相关授权行后内存判定。
    """
    from app.core import mongo
    from bson import ObjectId

    coll = mongo.get_collection(COLLECTION)
    roles = _role_ids(ctx)
    uid = ObjectId(ctx.user_id) if ObjectId.is_valid(ctx.user_id) else ctx.user_id

    cursor = coll.find(
        {
            "org_unit_id": ctx.org_unit_id,
            "resource_type": resource,
            "resource_id": {"$in": list(all_ids)},
            "subject_type": {"$in": ["user", "role", "all"]},
            "subject_id": {"$in": [uid, None] + roles},
        }
    )
    rows = await cursor.to_list(length=None)

    # 先收集 deny（最高优先级）
    denied: set[str] = set()
    # 再收集 allow
    allowed: set[str] = set()
    for g in rows:
        rid = str(g.get("resource_id"))
        effect = g.get("effect")
        if effect == "deny":
            denied.add(rid)
        elif effect == "allow":
            allowed.add(rid)

    return {rid for rid in all_ids if rid not in denied and rid in allowed}
