"""认证与自我端点（``api/endpoints/auth.py``，``02`` §4.2）。

- POST /api/v1/auth/login：返回 access token
- POST /api/v1/auth/logout
- GET  /api/v1/auth/me：{user, org_unit, roles[], permissions[]}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.clock import utcnow
from app.core.errors import Forbidden, Unauthenticated
from app.core.logging import get_logger
from app.deps import get_ctx
from app.identity.context import RequestContext

log = get_logger("api.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LogoutRequest(BaseModel):
    pass


@router.post("/login")
async def login(body: LoginRequest) -> dict[str, Any]:
    """登录：校验用户名/密码，解析角色，签发 access token。

    **角色来自数据，不来自硬编码**：用 ``RoleBindingsRepository.roles_for_user``
    查该用户的 org_unit 级角色绑定，结果写进 JWT 的 ``roles`` claim
    （``identity/auth.py::generate_access_token``）。这样前端 ``/auth/me`` 的
    ``roles`` 与后端 ``admin.py::_Permission.guard`` 的判定同源——否则管理端会全 403。

    查不到绑定 → **空集**（不是 admin）。角色必须来自数据；"查不到就默认给 admin"
    是个后门，会把"忘记配权限"变成"默认全通"。

    阶段 1 简化：密码校验是**占位**（只判非空，未做哈希比对；生产需 argon2id）。
    本函数不引入哈希依赖，``users.password_hash`` 由 ``identity/seed.py`` 存明文占位，
    两者如实对应。
    """
    from app.core import mongo
    from app.identity.auth import generate_access_token
    from app.identity.roles import RoleBindingsRepository, RolesRepository
    from app.settings import get_settings

    users_coll = mongo.get_collection("users")
    # ★ 登录名在**单租户内**唯一，不是全库唯一（org 归属从文档取）。
    #   历史数据里存在跨 org 的同名账号（手工导入 / 遗留集成账号），此时
    #   `find_one({"username": ...})` 会按自然顺序返回插入最早的那条——可能属于
    #   另一个 org，且带 `status` 字段缺失的历史结构 → 登录被误判为"账号已停用"。
    #   策略：先按 status=active 过滤；命中多条时**报错而非猜测**（fail-closed，
    #   不把登录态给到不确定的账号，实施人员应先清理重名数据）。
    active = {"username": body.username, "status": "active"}
    candidates = await users_coll.find(active).to_list(length=2)
    if not candidates:
        # 兼容无 status 字段的历史账号：单独兜底查一次，仍只接受唯一命中
        legacy = await users_coll.find({"username": body.username}).to_list(length=2)
        if len(legacy) > 1:
            raise Unauthenticated(
                "登录名重复，请联系管理员清理",
                details={"username": body.username, "reason": "duplicate_username"},
            )
        candidates = legacy
    if not candidates:
        raise Unauthenticated("用户名或密码错误")
    if len(candidates) > 1:
        raise Unauthenticated(
            "登录名重复，请联系管理员清理",
            details={"username": body.username, "reason": "duplicate_username"},
        )
    doc = candidates[0]

    # 密码校验占位：阶段 1 不做真实哈希比对，仅校验非空（生产由 argon2id 替换）
    if not body.password:
        raise Unauthenticated("用户名或密码错误")

    if doc.get("status") not in ("active", None):
        # 说明走的是 active 过滤后的记录却仍带非 active 状态——属数据异常，fail-closed
        raise Unauthenticated("账号已停用")

    settings = get_settings()
    user_id = str(doc.get("_id"))
    org_unit_id = str(doc.get("org_unit_id", ""))

    # ★ 用**真实 org_unit_id** 构造 ctx —— roles_for_user 靠 ctx.org_unit_id 做
    # org 隔离过滤，若沿用占位 org 会查不到任何绑定（这正是本缺陷的另一半）。
    ctx = RequestContext(
        user_id=user_id, org_unit_id=org_unit_id, roles=frozenset()
    )
    bindings = RoleBindingsRepository(collection=mongo.get_collection("role_bindings"))
    role_codes = await bindings.roles_for_user(ctx, user_id, org_scope_only=True)
    roles = frozenset(str(r) for r in role_codes if r)

    # ★ 角色码 → 权限码展开（``02`` §2.1``roles.permissions``）。各端点 guard 校验的
    #   是**权限码**（``tool.manage`` 等），若不展开，非 admin 的运维角色会全站 403
    #   （admin 只是靠"超级用户直通"侥幸通过）。分属两个字段，见 RequestContext 注释。
    roles_repo = RolesRepository(collection=mongo.get_collection("roles"))
    permissions = await roles_repo.expand_permissions(ctx, roles)

    log.debug(
        "login resolved roles and permissions",
        extra={
            "zy": {
                "user_id": user_id,
                "roles": sorted(roles),
                "permissions": sorted(permissions),
            }
        },
    )

    token = generate_access_token(
        settings=settings,
        user_id=user_id,
        org_unit_id=org_unit_id,
        roles=roles,
        permissions=permissions,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.access_token_ttl_seconds,
        "roles": sorted(roles),
        "permissions": sorted(permissions),
    }


@router.post("/logout")
async def logout(
    request: Request,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """登出（无状态 JWT：客户端丢弃 token 即可；此处仅留审计）。"""
    return {"ok": True}


@router.get("/me")
async def me(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """返回当前登录态、权限与 agentPolicy（Profile 重编译结果的对外下发通道）。

    ``agentPolicy.capabilities`` 的真值来源是**该用户实际编译出的 Profile**
    （``kernel/profile/compiler.py``，含其授权的工具/技能），不是硬编码常量——
    两个不同角色的用户应看到不同的能力集合（阶段 2 验收项，``00`` §11）。
    编译失败时**全部降级为 False 并给出空 roleIds**（fail-closed，不假装有能力）。
    """
    from app.identity.users import UsersRepository
    from app.core import mongo

    users = UsersRepository(collection=mongo.get_collection("users"))
    user = await users._collection.find_one(
        {"_id": _oid(ctx.user_id) if _oid(ctx.user_id) else ctx.user_id}
    )
    user_payload = {
        "id": ctx.user_id,
        "username": user.get("username") if user else None,
        "display_name": user.get("display_name") if user else None,
    }
    return {
        "user": user_payload,
        "org_unit": {"id": ctx.org_unit_id},
        # ★ roles = **角色码**（admin / ops / member）；permissions = **权限码**
        #   （tool.manage / device.manage …），由 roles.permissions 在登录时展开。
        #   **两者语义不同、刻意分字段**（不是混在一起）：前端 ``agentPolicy.roleIds``
        #   与 apps/admin 侧边栏都消费 ``roles``，混入权限码会让它们失真。
        #   permissions 此前是硬编码空数组（占位），现已下发真实值。
        "roles": sorted(ctx.roles),
        "permissions": sorted(ctx.permissions),
        "agentPolicy": await _build_agent_policy(ctx),
    }


async def _build_agent_policy(ctx: RequestContext) -> dict[str, Any]:
    """由该用户实际 Profile 推导 agentPolicy（capabilities 真值，不硬编码）。

    映射规则（依据 Profile 内实际工具/技能声明，``kernel/profile/models.py``）：

    - ``content_generation``：有可用模型实例（``profile.model`` 存在，capabilities 含 chat）。
    - ``image_generation``：存在 ``category`` 产出图像的工具（本仓无内置 → 通常 False，
      但由工具声明驱动，不写死）。
    - ``code_generation``：存在代码类工具（工具名/描述含 code 相关标记）或代码类技能。
    - ``browser_automation``：存在 ``category in (local, hybrid)`` 的浏览器类工具。
    - ``internal_knowledge``：存在知识检索工具（``knowledge_search``）。

    编译异常 → 全部 False（fail-closed）。
    """
    capabilities = {
        "content_generation": False,
        "image_generation": False,
        "code_generation": False,
        "browser_automation": False,
        "internal_knowledge": False,
    }
    try:
        from app.kernel.profile.compiler import compile_profile

        profile = await compile_profile(ctx.org_unit_id, ctx.user_id)
    except Exception:  # noqa: BLE001 - 编译失败 fail-closed，不假装有能力
        return {
            "capabilities": capabilities,
            "roleIds": sorted(ctx.roles),
            "roleNames": sorted(ctx.roles),
        }

    # content_generation：模型实例带 chat 能力
    if profile.model is not None and "chat" in (profile.model.capabilities or []):
        capabilities["content_generation"] = True

    tool_names = {t.name.lower() for t in profile.tools}
    skill_blob = " ".join(
        f"{s.key} {s.name} {s.definition}".lower() for s in profile.skills
    )

    # internal_knowledge：知识检索工具在授权集合内
    if any("knowledge" in n for n in tool_names):
        capabilities["internal_knowledge"] = True

    # code_generation：代码类工具或技能
    if any("code" in n or "python" in n or "shell" in n for n in tool_names) or (
        "code" in skill_blob
    ):
        capabilities["code_generation"] = True

    # image_generation：图像类工具或技能
    if any("image" in n or "draw" in n for n in tool_names) or "image" in skill_blob:
        capabilities["image_generation"] = True

    # browser_automation：存在 local/hybrid 类浏览器工具
    for t in profile.tools:
        blob = f"{t.name} {t.description}".lower()
        if t.category in ("local", "hybrid") and ("browser" in blob or "web" in blob):
            capabilities["browser_automation"] = True
            break

    return {
        "capabilities": capabilities,
        "roleIds": sorted(ctx.roles),
        "roleNames": sorted(ctx.roles),
    }


def _oid(value: str):
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return None
