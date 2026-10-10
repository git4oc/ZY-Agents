"""管理端端点（``api/endpoints/admin.py``，``02`` §4.8）。

路由：org-units、users、role-bindings、model-providers、model-instances、tools、skills、
devices、audit、quota、grants（资源授权）。写 model/tool/skill/grants 后须失效 Profile 缓存
（``03`` §3.5 / ``00`` §8：写入即失效相关 Profile 缓存）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core import mongo
from app.core.clock import utcnow
from app.core.errors import Forbidden, NotFound
from app.deps import get_ctx
from app.identity.acl import require_permission
from app.identity.context import RequestContext
from app.kernel.profile.store import invalidate

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class _Permission:
    """细粒度权限守卫（``00`` §8 资源 ACL；阶段 1 近似角色码）。"""

    REQUIRED = {
        "org-units": "org.manage",
        "users": "user.manage",
        "role-bindings": "user.manage",
        "model-providers": "model.manage",
        "model-instances": "model.manage",
        "tools": "tool.manage",
        "skills": "skill.manage",
        "devices": "device.manage",
        "audit": "audit.read",
        "quota": "quota.read",
        "grants": "acl.manage",
    }

    @classmethod
    def guard(cls, ctx: RequestContext, resource: str) -> None:
        """按资源取所需权限码，交给**共享 guard** 判定。

        ★ 判定逻辑收敛到 ``identity.acl.require_permission``（admin 角色码放行 +
        权限码直判）。此处不再自己写 ``perm not in ctx.roles and not _is_admin``——
        之前 branding 侧用的是另一套内联判定，导致管理员被误判 403（缺陷 C）。
        """
        perm = cls.REQUIRED.get(resource, "")
        if not perm:
            return
        require_permission(ctx, perm)


def _is_admin(ctx: RequestContext) -> bool:
    return "admin" in ctx.roles


# ---------------------------------------------------------------------------
# ★ JSON 可序列化：管理端列表端点统一出口
# ---------------------------------------------------------------------------
def _jsonable(value: Any) -> Any:
    """把 Mongo 文档递归转成 JSON 友好类型（``ObjectId`` / ``datetime`` → 原生）。

    ## 为什么必须有这个

    管理端列表端点直接返回 mongo 原始文档，而文档里大量字段是 ``ObjectId``
    （如 ``role_bindings.subject_id``）与 ``datetime``（``created_at`` 等）。
    FastAPI 序列化不了它们 → ``PydanticSerializationError`` → **500**。

    ## 为什么不能手写 ``{**r, "_id": str(r["_id"])}``

    那种写法**只解决 ``_id``**；``subject_id`` / ``owner_user_id`` / ``provider_id``
    等其它 ObjectId 字段照样崩。缺陷 B 就是这么来的：``role_bindings`` 集合一直为空，
    ``items=[]`` 不触发序列化，所以"测试用假集合"永远测不到真实数据形态。

    故本函数**递归**处理，覆盖任意深度与任意字段名。
    """
    # 延迟 import：避免本模块在无 motor 环境下的导入负担
    from datetime import date, datetime

    try:
        from bson import ObjectId
    except Exception:  # pragma: no cover - bson 恒在（pymongo 依赖）
        ObjectId = None  # type: ignore[assignment]

    if ObjectId is not None and isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _jsonable_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """列表端点统一出口：整批转成 JSON 友好。"""
    return [_jsonable(r) for r in rows]


# ---------------- org-units ----------------

class OrgUnitBody(BaseModel):
    name: str
    code: str
    parent_id: str | None = None


@router.get("/org-units")
async def list_org_units(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "org-units")
    from app.identity.org_units import OrgUnitsRepository

    repo = OrgUnitsRepository()
    cursor = repo._collection.find({})
    rows = await cursor.to_list(length=None)
    return {"items": _jsonable_items(rows)}


@router.post("/org-units")
async def create_org_unit(
    body: OrgUnitBody, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    _Permission.guard(ctx, "org-units")
    from app.identity.org_units import OrgUnitsRepository

    repo = OrgUnitsRepository()
    oid = await repo.create(ctx, name=body.name, code=body.code, parent_id=body.parent_id)
    return {"id": oid}


# ---------------- users ----------------

@router.get("/users")
async def list_users(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "users")
    from app.identity.users import UsersRepository

    repo = UsersRepository()
    rows = await repo.list(ctx)
    return {
        "items": [
            {
                "id": str(r.get("_id", "")),
                "username": r.get("username"),
                "display_name": r.get("display_name"),
                "status": r.get("status"),
            }
            for r in rows
        ]
    }


# ---------------- model providers / instances ----------------

@router.get("/model-providers")
async def list_model_providers(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "model-providers")
    coll = mongo.get_collection("model_providers")
    rows = await coll.find({"org_unit_id": ctx.org_unit_id}).to_list(length=None)
    return {"items": _jsonable_items(rows)}


@router.get("/model-instances")
async def list_model_instances(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "model-instances")
    coll = mongo.get_collection("model_instances")
    rows = await coll.find({"org_unit_id": ctx.org_unit_id}).to_list(length=None)
    return {"items": _jsonable_items(rows)}


# ---------------- model providers / instances（写端点：写入即失效 Profile 缓存）----------------

#: 允许管理端直接落库的 provider 字段（其余字段如 api_key_enc 不接受外部写入）
_PROVIDER_WRITABLE = {
    "name",
    "code",
    "base_url",
    "provider_type",
    "status",
    "settings",
}
_INSTANCE_WRITABLE = {
    "provider_id",
    "model_name",
    "display_name",
    "capabilities",
    "max_context_tokens",
    "settings",
    "status",
}


# ---------------------------------------------------------------------------
# ★ 唯一索引字段必填化（修"索引与写端点不一致"）
# ---------------------------------------------------------------------------
# Mongo 唯一索引把**缺失字段视为 null**：`(org_unit_id, code)` unique 意味着
# 第一条无 code 的文档能插入，第二条就撞 E11000。若把 code 留成"可选"，
# 这个资源就**根本无法通过 API 正常创建**（用户不传 code → 第一次成功、第二次 500）。
#
# 因此：**凡出现在唯一索引里的字段，写端点一律必填**（Pydantic 校验 → 422），
# 而不是"白名单允许但不要求"。下表的 required 列即索引约束。
class ModelProviderCreate(BaseModel):
    """``POST /admin/model-providers`` 请求体。

    必填 ``name`` / ``code``——``code`` 是唯一索引
    ``model_providers_org_code_u = (org_unit_id, code)`` 的组成部分（``02`` §2.1）。
    ⚠️ ``api_key_enc`` **刻意不在此模型**：密钥不经管理端写端点落库。
    """

    model_config = ConfigDict(extra="ignore")  # 白名单外字段静默丢弃（保持既有语义）

    name: str = Field(min_length=1)
    code: str = Field(min_length=1)
    base_url: str = ""
    provider_type: str = "openai_compatible"
    status: str = "active"
    settings: dict[str, Any] | None = None


class ModelInstanceCreate(BaseModel):
    """``POST /admin/model-instances`` 请求体。

    必填 ``provider_id`` / ``model_name``——唯一索引
    ``model_instances_u = (org_unit_id, provider_id, model_name)`` 的组成部分。
    """

    model_config = ConfigDict(extra="ignore")

    provider_id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    display_name: str = ""
    capabilities: list[str] = Field(default_factory=lambda: ["chat"])
    max_context_tokens: int = 0
    status: str = "active"
    settings: dict[str, Any] | None = None


class ToolCreate(BaseModel):
    """``POST /admin/tools`` 请求体。

    必填 ``name``——唯一索引 ``tools_org_name_u = (org_unit_id, name)`` 的组成部分。
    """

    model_config = ConfigDict(extra="allow")  # tools 保留透传（handler 等是合法字段）

    name: str = Field(min_length=1)
    description: str = ""
    type: str = "http"
    category: str = "server"
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    risk_level: str = "read"
    approval_required: bool = False
    timeout_ms: int = 30000
    status: str = "active"


class SkillCreate(BaseModel):
    """``POST /admin/skills`` 请求体。

    必填 ``key``——唯一索引 ``skills_org_key_u = (org_unit_id, key)`` 的组成部分。
    """

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1)
    name: str = ""
    kind: str = "skill"
    status: str = "active"
    requires_tools: list[str] = Field(default_factory=list)


class GrantCreate(BaseModel):
    """``POST /admin/grants`` 请求体。

    必填 ``resource_type`` / ``resource_id`` / ``subject_type``——唯一索引
    ``resource_grants_u = (org_unit_id, resource_type, resource_id, subject_type, subject_id)``
    的组成部分。``subject_id`` 可空（``subject_type=all`` 时语义上就是 null）。
    """

    model_config = ConfigDict(extra="ignore")

    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    subject_type: str = Field(min_length=1)
    subject_id: str | None = None
    effect: str = "allow"


class RoleBindingCreate(BaseModel):
    """``POST /admin/role-bindings`` 请求体（``02`` §2.1）。

    早前该端点吃 ``body: dict`` 按白名单过滤，``POST {}`` 会落一条
    ``{org_unit_id, created_at}`` 的**全空垃圾文档**并回 200——与"空 PATCH 假成功"
    同源（缺 ``if body:`` 守卫）。这里补齐必填与**条件必填**。

    唯一索引 ``role_bindings_u = (org_unit_id, subject_id, role_code, resource_id)``
    —— ``subject_id`` / ``role_code`` 必填（否则又是 null 撞唯一索引）。

    **条件必填**（对齐 ``identity/roles.py::bind`` 的既有语义）：
    ``scope=resource`` 时 ``resource_type`` + ``resource_id`` 必填。
    """

    model_config = ConfigDict(extra="ignore")

    subject_type: str = Field(min_length=1)  # user | role
    subject_id: str = Field(min_length=1)
    role_code: str = Field(min_length=1)
    scope: str = "org_unit"  # org_unit | resource
    resource_type: str | None = None
    resource_id: str | None = None

    @model_validator(mode="after")
    def _check_resource_scope(self) -> "RoleBindingCreate":
        """``scope=resource`` 时资源类型/资源 id 必填（否则是一条无法定位的授权）。"""
        if self.scope == "resource":
            missing = [
                name
                for name, value in (("resource_type", self.resource_type), ("resource_id", self.resource_id))
                if not value
            ]
            if missing:
                raise ValueError(
                    f"scope=resource 时必须提供 {'、'.join(missing)}"
                )
        return self


# ---------------------------------------------------------------------------
# 唯一键冲突 → 409（只转换可预期的驱动异常，未知异常仍 500）
# ---------------------------------------------------------------------------
async def _insert_or_conflict(coll: Any, doc: dict[str, Any], *, label: str, field: str) -> Any:
    """插入并把唯一键冲突转成 ``ConflictError``（409）。

    只捕获 ``DuplicateKeyError``——它语义明确（唯一索引冲突），
    换成 409 + 可读提示；**其它异常一律照旧冒泡**（未知异常吞成 4xx 才是危险的）。
    """
    from pymongo.errors import DuplicateKeyError

    from app.core.errors import ConflictError

    try:
        return await coll.insert_one(doc)
    except DuplicateKeyError as exc:
        # 不回显 exc（含索引名/键值细节，且可能很长）——只给可操作的一句话
        raise ConflictError(
            f"已存在相同的{label}（字段 {field}），请换一个",
            details={"field": field, "collection": getattr(coll, "name", "")},
        ) from exc


async def _update_or_conflict(coll: Any, query: dict[str, Any], update: dict[str, Any], *, label: str, field: str) -> None:
    """更新并把唯一键冲突转成 409（改 code/name 撞已有记录时同样要 409 而非 500）。"""
    from pymongo.errors import DuplicateKeyError

    from app.core.errors import ConflictError

    try:
        await coll.update_one(query, update)
    except DuplicateKeyError as exc:
        raise ConflictError(
            f"已存在相同的{label}（字段 {field}），请换一个",
            details={"field": field, "collection": getattr(coll, "name", "")},
        ) from exc


@router.post("/model-providers")
async def create_model_provider(
    body: ModelProviderCreate, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """创建模型提供商（``model_providers``）。

    写表后**必须**失效 Profile 缓存——否则已缓存的 Profile 仍指向旧提供商，
    配置变更要等到缓存自然过期才生效（``03`` §3.5）。
    """
    _Permission.guard(ctx, "model-providers")
    doc = {
        **{k: v for k, v in body.model_dump().items() if k in _PROVIDER_WRITABLE and v is not None},
        "org_unit_id": ctx.org_unit_id,
        "created_at": utcnow(),
    }
    result = await _insert_or_conflict(
        mongo.get_collection("model_providers"), doc, label="提供商 code", field="code"
    )
    await invalidate(ctx.org_unit_id)  # ★ 写入 model_providers → 失效 Profile 缓存
    return {"id": str(result.inserted_id)}


@router.patch("/model-providers/{provider_id}")
async def patch_model_provider(
    provider_id: str, body: dict[str, Any], ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """更新模型提供商（仅白名单字段；写后失效 Profile 缓存）。"""
    _Permission.guard(ctx, "model-providers")
    update = _require_update(
        body,
        {k: v for k, v in body.items() if k in _PROVIDER_WRITABLE},
        resource="model-providers",
    )
    await _update_or_conflict(
        mongo.get_collection("model_providers"),
        {"_id": _oid(provider_id), "org_unit_id": ctx.org_unit_id},
        {"$set": update},
        label="提供商 code",
        field="code",
    )
    await invalidate(ctx.org_unit_id)  # ★
    return {"ok": True, "updated": sorted(update)}


@router.post("/model-instances")
async def create_model_instance(
    body: ModelInstanceCreate, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """创建模型实例（``model_instances``；写后失效 Profile 缓存）。"""
    _Permission.guard(ctx, "model-instances")
    doc = {
        **{k: v for k, v in body.model_dump().items() if k in _INSTANCE_WRITABLE and v is not None},
        "org_unit_id": ctx.org_unit_id,
        "created_at": utcnow(),
    }
    result = await _insert_or_conflict(
        mongo.get_collection("model_instances"),
        doc,
        label="模型实例（provider_id + model_name）",
        field="provider_id+model_name",
    )
    await invalidate(ctx.org_unit_id)  # ★ 写入 model_instances → 失效 Profile 缓存
    return {"id": str(result.inserted_id)}


@router.patch("/model-instances/{instance_id}")
async def patch_model_instance(
    instance_id: str, body: dict[str, Any], ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """更新模型实例（仅白名单字段；写后失效 Profile 缓存）。"""
    _Permission.guard(ctx, "model-instances")
    update = _require_update(
        body,
        {k: v for k, v in body.items() if k in _INSTANCE_WRITABLE},
        resource="model-instances",
    )
    await mongo.get_collection("model_instances").update_one(
        {"_id": _oid(instance_id), "org_unit_id": ctx.org_unit_id}, {"$set": update}
    )
    await invalidate(ctx.org_unit_id)  # ★
    return {"ok": True, "updated": sorted(update)}


# ---------------- tools ----------------

#: tools PATCH 允许写入的字段（``_id`` / ``org_unit_id`` / ``created_at`` 不接受外部写入）
_TOOL_WRITABLE = {
    "name",
    "description",
    "type",
    "category",
    "input_schema",
    "output_schema",
    "risk_level",
    "approval_required",
    "timeout_ms",
    "handler",
    "local_action",
    "status",
}

@router.get("/tools")
async def list_tools(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "tools")
    coll = mongo.get_collection("tools")
    rows = await coll.find({"org_unit_id": ctx.org_unit_id}).to_list(length=None)
    return {"items": _jsonable_items(rows)}


@router.post("/tools")
async def create_tool(
    body: ToolCreate, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """创建工具（必填 ``name``——唯一索引 ``tools_org_name_u`` 的组成部分）。"""
    _Permission.guard(ctx, "tools")
    coll = mongo.get_collection("tools")
    doc = {
        **body.model_dump(exclude_none=True),
        "org_unit_id": ctx.org_unit_id,
        "created_at": utcnow(),
    }
    result = await _insert_or_conflict(coll, doc, label="工具名", field="name")
    await invalidate(ctx.org_unit_id)  # ★ 写入 tools → 失效 Profile 缓存
    return {"id": str(result.inserted_id)}


@router.patch("/tools/{tool_id}")
async def patch_tool(
    tool_id: str, body: dict[str, Any], ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    _Permission.guard(ctx, "tools")
    # ★ 空 update 不再"静默跳过 + 200 ok"——空更新显式 422（见 _require_update）
    #   注意**不过滤 null**：PATCH 里显式传 null 表示"清空该字段"
    #   （如 output_schema: null = 不做输出校验），滤掉就等于静默丢弃用户的意图。
    update = _require_update(
        body,
        {k: v for k, v in body.items() if k in _TOOL_WRITABLE},
        resource="tools",
    )
    await _update_or_conflict(
        mongo.get_collection("tools"),
        {"_id": _oid(tool_id), "org_unit_id": ctx.org_unit_id},
        {"$set": update},
        label="工具名",
        field="name",
    )
    await invalidate(ctx.org_unit_id)
    return {"ok": True, "updated": sorted(update)}


# ---------------- skills ----------------

@router.get("/skills")
async def list_skills(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    # 读取放宽为登录用户即可：member 接受分享的技能后落在本 org 的 skills 集合，
    # "我的技能"页需要能看到（写操作仍走 skill.manage 守卫）。
    coll = mongo.get_collection("skills")
    rows = await coll.find({"org_unit_id": ctx.org_unit_id}).to_list(length=None)
    return {"items": [_skill_jsonable(r) for r in rows]}


@router.post("/skills")
async def create_skill(
    body: SkillCreate, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """创建技能（必填 ``key``——唯一索引 ``skills_org_key_u`` 的组成部分）。"""
    _Permission.guard(ctx, "skills")
    coll = mongo.get_collection("skills")
    doc = {
        **body.model_dump(exclude_none=True),
        "org_unit_id": ctx.org_unit_id,
        "created_at": utcnow(),
    }
    result = await _insert_or_conflict(coll, doc, label="技能 key", field="key")
    await invalidate(ctx.org_unit_id)
    return {"id": str(result.inserted_id)}


class SkillPatch(BaseModel):
    """``PATCH /admin/skills/{id}`` 请求体（阶段 2 任务 #3：技能 CRUD 补齐）。"""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    description: str | None = None
    scenario: str | None = None
    type: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


def _skill_jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """skills 文档 → 前端 SkillItem 形状（enabled ↔ status active/disabled 双向映射）。"""
    return {
        "id": str(row.get("_id")),
        "mainId": "default",
        "key": str(row.get("key") or ""),
        "name": str(row.get("name") or row.get("key") or ""),
        "description": str(row.get("description") or ""),
        "scenario": str(row.get("scenario") or ""),
        "type": str(row.get("type") or "ordinary"),
        "config": dict(row.get("config") or {}),
        "enabled": str(row.get("status") or "active") == "active",
        "createdAt": _iso_or_empty(row.get("created_at")),
        "updatedAt": _iso_or_empty(row.get("updated_at") or row.get("created_at")),
    }


def _iso_or_empty(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _skill_oid(value: str) -> Any:
    """字符串 → ObjectId（失败则原样返回；skills 集合 _id 有 ObjectId 与 str 两种形态）。"""
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return value


async def _load_skill(skill_id: str, ctx: RequestContext) -> dict[str, Any]:
    row = await mongo.get_collection("skills").find_one(
        {"_id": _skill_oid(skill_id), "org_unit_id": ctx.org_unit_id}
    )
    if row is None:
        raise NotFound("技能不存在")
    return row


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    # 读取放宽为登录用户即可（同 list_skills；写操作仍走 skill.manage 守卫）。
    row = await _load_skill(skill_id, ctx)
    return _skill_jsonable(row)


@router.patch("/skills/{skill_id}")
async def patch_skill(
    skill_id: str, body: SkillPatch, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    _Permission.guard(ctx, "skills")
    await _load_skill(skill_id, ctx)
    updates: dict[str, Any] = {"updated_at": utcnow()}
    if body.name is not None:
        updates["name"] = body.name.strip()
    if body.description is not None:
        updates["description"] = body.description
    if body.scenario is not None:
        updates["scenario"] = body.scenario
    if body.type is not None:
        updates["type"] = body.type
    if body.config is not None:
        updates["config"] = body.config
    if body.enabled is not None:
        updates["status"] = "active" if body.enabled else "disabled"
    await _update_or_conflict(
        mongo.get_collection("skills"),
        {"_id": _skill_oid(skill_id), "org_unit_id": ctx.org_unit_id},
        {"$set": updates},
        label="技能 key",
        field="key",
    )
    await invalidate(ctx.org_unit_id)
    fresh = await _load_skill(skill_id, ctx)
    return _skill_jsonable(fresh)


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "skills")
    await _load_skill(skill_id, ctx)
    await mongo.get_collection("skills").delete_one(
        {"_id": _skill_oid(skill_id), "org_unit_id": ctx.org_unit_id}
    )
    await invalidate(ctx.org_unit_id)
    return {"id": skill_id}


# ---------------- skills：ZIP 安装（阶段 2：Skill 安装接真） ----------------

def _zip_http_error(exc: "SkillZipError") -> Any:
    """ZIP 校验错误 → FastAPI HTTPException（``{"detail": {code, file, field}}``）。

    ⚠️ 前端 ``skillInstallError.ts`` 契约是 ``response.data.detail.code``（FastAPI
    原生 HTTPException 格式），**不是**项目 DomainError 的 ``{"error": {...}}`` 格式。
    """
    from fastapi import HTTPException

    return HTTPException(
        status_code=400,
        detail={"code": exc.code, "file": exc.file, "field": exc.field},
    )


@router.post("/skills/install")
async def install_skill_zip(
    file: UploadFile = File(...),
    confirm_replace: bool = Form(False),
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """从 ZIP 安装 Skill（``skill.manage`` 权限；multipart 上传）。

    校验（``capability/skill_zip.py``）→ 同 key 版本比对（同版返回 duplicate /
    异版 409 要求确认 / confirm_replace 直接替换）→ 落库 skills + skill_versions
    （definition 含 SKILL.md 指令正文，供 Profile ``load_skills`` 消费）→ 失效 Profile 缓存。
    """
    from fastapi import HTTPException

    _Permission.guard(ctx, "skills")
    from app.capability.skill_zip import (
        SkillZipError,
        parse_skill_zip,
        semver_tuple,
    )

    data = await file.read()
    try:
        package = parse_skill_zip(data)
    except SkillZipError as exc:
        raise _zip_http_error(exc) from exc

    import hashlib as _hashlib

    digest = _hashlib.sha256(data).hexdigest()
    skills_coll = mongo.get_collection("skills")
    versions_coll = mongo.get_collection("skill_versions")
    now = utcnow()

    existing = await skills_coll.find_one(
        {"key": package.name, "org_unit_id": ctx.org_unit_id}
    )
    duplicate = False
    updated = False
    if existing is None:
        result = await skills_coll.insert_one(
            {
                "key": package.name,
                "name": package.name,
                "description": package.description,
                "kind": "skill",
                "type": "ordinary",
                "scenario": "",
                "config": {
                    "source": "zip",
                    "version": package.version,
                    "file_count": package.file_count,
                    "digest": digest,
                },
                "requires_tools": [],
                "current_version": package.version,
                "status": "active",
                "org_unit_id": ctx.org_unit_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        skill_id = result.inserted_id
    else:
        skill_id = existing["_id"]
        current_version = str(existing.get("current_version") or "0.0.0")
        duplicate = current_version == package.version
        if duplicate:
            return _skill_install_result(
                existing, package, duplicate=duplicate, updated=False, digest=digest
            )
        if not confirm_replace:
            incoming = semver_tuple(package.version)
            current = semver_tuple(current_version)
            action = "upgrade" if incoming > current else "downgrade"
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "skill_upgrade_confirmation_required",
                    "upgrade": {
                        "action": action,
                        "currentVersion": current_version,
                        "incomingVersion": package.version,
                        "currentName": str(existing.get("name") or ""),
                    },
                },
            )
        await skills_coll.update_one(
            {"_id": skill_id, "org_unit_id": ctx.org_unit_id},
            {
                "$set": {
                    "name": package.name,
                    "description": package.description,
                    "config": {
                        "source": "zip",
                        "version": package.version,
                        "file_count": package.file_count,
                        "digest": digest,
                    },
                    "current_version": package.version,
                    "status": "active",
                    "updated_at": now,
                }
            },
        )
        updated = True

    await versions_coll.insert_one(
        {
            "skill_id": skill_id,
            "version": package.version,
            "definition": {
                "instructions": package.body,
                "source": "zip",
                "files": package.files,
                "sha256": package.sha256,
            },
            "created_at": now,
        }
    )

    # 专家包子技能：每个额外 SKILL.md 各落一份 skills + skill_versions（upsert，跟随主技能处置）
    for child in package.children:
        child_existing = await skills_coll.find_one(
            {"key": child.slug, "org_unit_id": ctx.org_unit_id}
        )
        child_config = {
            "source": "zip-child",
            "version": child.version,
            "file_count": package.file_count,
            "digest": digest,
        }
        if child_existing is None:
            child_result = await skills_coll.insert_one(
                {
                    "key": child.slug,
                    "name": child.name,
                    "description": child.description,
                    "kind": "skill",
                    "type": "ordinary",
                    "scenario": "",
                    "config": child_config,
                    "requires_tools": [],
                    "current_version": child.version,
                    "status": "active",
                    "org_unit_id": ctx.org_unit_id,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            child_skill_id = child_result.inserted_id
        else:
            child_skill_id = child_existing["_id"]
            await skills_coll.update_one(
                {"_id": child_skill_id, "org_unit_id": ctx.org_unit_id},
                {
                    "$set": {
                        "description": child.description,
                        "config": child_config,
                        "current_version": child.version,
                        "status": "active",
                        "updated_at": now,
                    }
                },
            )
        await versions_coll.insert_one(
            {
                "skill_id": child_skill_id,
                "version": child.version,
                "definition": {
                    "instructions": child.body,
                    "source": "zip-child",
                    "files": [],
                    "sha256": "",
                },
                "created_at": now,
            }
        )

    await invalidate(ctx.org_unit_id)

    fresh = await skills_coll.find_one({"_id": skill_id})
    return _skill_install_result(
        fresh or {}, package, duplicate=duplicate, updated=updated, digest=digest
    )


def _skill_install_result(
    row: dict[str, Any], package: Any, *, duplicate: bool, updated: bool, digest: str
) -> dict[str, Any]:
    """skills 文档 + 解析结果 → 前端 SkillInstallResult 形状。"""
    return {
        "id": str(row.get("_id") or ""),
        "name": str(row.get("name") or package.name),
        "slug": package.name,
        "description": str(row.get("description") or package.description),
        "version": package.version,
        "type": "ordinary",
        "enabled": str(row.get("status") or "active") == "active",
        "duplicate": duplicate,
        "updated": updated,
        "fileCount": package.file_count,
        "digest": digest,
        "warnings": [],
        "kind": package.kind,
        "childCount": len(package.children),
        "children": [
            {
                "slug": child.slug,
                "name": child.name,
                "description": child.description,
                "version": child.version,
            }
            for child in package.children
        ],
        "compatibility": {
            "status": "compatible",
            "declaredTools": [],
            "referencedTools": [],
            "warnings": [],
        },
    }


# ---------------- devices ----------------

@router.get("/devices")
async def admin_list_devices(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "devices")
    coll = mongo.get_collection("devices")
    rows = await coll.find({"org_unit_id": ctx.org_unit_id}).to_list(length=None)
    return {"items": _jsonable_items(rows)}


@router.delete("/devices/{device_id}")
async def admin_revoke_device(
    device_id: str, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    _Permission.guard(ctx, "devices")
    coll = mongo.get_collection("devices")
    await coll.update_one(
        {"org_unit_id": ctx.org_unit_id, "device_id": device_id},
        {"$set": {"status": "revoked", "revoked_at": utcnow()}},
    )
    return {"ok": True}


# ---------------- audit ----------------

@router.get("/audit")
async def query_audit(
    ctx: RequestContext = Depends(get_ctx),
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _Permission.guard(ctx, "audit")
    from app.governance.audit import AuditFilters, query

    filters = AuditFilters(action=action)
    result = await query(ctx, filters=filters, page={"limit": limit, "offset": offset})
    # audit_logs 的 actor_user_id 等字段是 ObjectId → 同样必须过 _jsonable
    return {"items": _jsonable_items(result.items), "total": result.total}


# ---------------- quota ----------------

@router.get("/quota")
async def get_quota(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "quota")
    from app.governance.quota import current_usage

    return await current_usage(ctx)


# ---------------- role-bindings / grants ----------------

@router.get("/role-bindings")
async def list_role_bindings(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "role-bindings")
    coll = mongo.get_collection("role_bindings")
    rows = await coll.find({"org_unit_id": ctx.org_unit_id}).to_list(length=None)
    return {"items": _jsonable_items(rows)}


@router.get("/grants")
async def list_grants(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    _Permission.guard(ctx, "grants")
    coll = mongo.get_collection("resource_grants")
    rows = await coll.find({"org_unit_id": ctx.org_unit_id}).to_list(length=None)
    return {"items": _jsonable_items(rows)}


# ---------------- role-bindings / grants（写端点）----------------

#: role_bindings 允许写入的字段
_BINDING_WRITABLE = {
    "subject_type",
    "subject_id",
    "role_code",
    "scope",
    "resource_type",
    "resource_id",
}
#: resource_grants 允许写入的字段（``effect`` 只允许 allow/deny——deny 优先由 acl.py 兜底）
_GRANT_WRITABLE = {
    "resource_type",
    "resource_id",
    "subject_type",
    "subject_id",
    "effect",
}


@router.post("/role-bindings")
async def create_role_binding(
    body: RoleBindingCreate, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """创建角色绑定（``role_bindings``）。

    角色绑定决定用户可见的模型/工具/技能集合 → 影响 Profile 编译输入，
    因此写后**必须**失效 Profile 缓存。

    必填 ``subject_type`` / ``subject_id`` / ``role_code``；``scope=resource`` 时
    资源类型与 id 也必填——缺了会落一条无法定位的垃圾绑定（且撞唯一索引）。
    """
    _Permission.guard(ctx, "role-bindings")
    doc = {
        **{
            k: v
            for k, v in body.model_dump().items()
            if k in _BINDING_WRITABLE
        },
        "org_unit_id": ctx.org_unit_id,
        "created_at": utcnow(),
    }
    result = await _insert_or_conflict(
        mongo.get_collection("role_bindings"),
        doc,
        label="角色绑定（subject + role + resource 组合）",
        field="subject_id+role_code+resource_id",
    )
    await invalidate(ctx.org_unit_id)  # ★ 授权变化 → Profile 重编译
    return {"id": str(result.inserted_id)}


@router.patch("/role-bindings/{binding_id}")
async def patch_role_binding(
    binding_id: str, body: dict[str, Any], ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """更新角色绑定（仅白名单字段；写后失效 Profile 缓存）。"""
    _Permission.guard(ctx, "role-bindings")
    update = _require_update(
        body,
        {k: v for k, v in body.items() if k in _BINDING_WRITABLE},
        resource="role-bindings",
    )
    await mongo.get_collection("role_bindings").update_one(
        {"_id": _oid(binding_id), "org_unit_id": ctx.org_unit_id}, {"$set": update}
    )
    await invalidate(ctx.org_unit_id)  # ★
    return {"ok": True, "updated": sorted(update)}


@router.post("/grants")
async def create_grant(
    body: GrantCreate, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """创建资源授权（``resource_grants``；写后失效 Profile 缓存）。

    必填 ``resource_type`` / ``resource_id`` / ``subject_type``（唯一索引组成部分）。
    ``effect`` 只允许 allow/deny——``identity/acl.py`` 判定时 **deny 优先**。
    """
    _Permission.guard(ctx, "grants")
    _validate_effect(body.effect)
    doc = {
        **body.model_dump(),
        "org_unit_id": ctx.org_unit_id,
        "created_at": utcnow(),
    }
    result = await _insert_or_conflict(
        mongo.get_collection("resource_grants"),
        doc,
        label="资源授权（resource + subject 组合）",
        field="resource_type+resource_id+subject_type+subject_id",
    )
    await invalidate(ctx.org_unit_id)  # ★ 资源授权变化 → Profile 重编译
    return {"id": str(result.inserted_id)}


@router.patch("/grants/{grant_id}")
async def patch_grant(
    grant_id: str, body: dict[str, Any], ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """更新资源授权（仅白名单字段；写后失效 Profile 缓存）。"""
    _Permission.guard(ctx, "grants")
    update = _require_update(
        body,
        {k: v for k, v in body.items() if k in _GRANT_WRITABLE},
        resource="grants",
    )
    if "effect" in update:
        _validate_effect(update["effect"])
    await _update_or_conflict(
        mongo.get_collection("resource_grants"),
        {"_id": _oid(grant_id), "org_unit_id": ctx.org_unit_id},
        {"$set": update},
        label="资源授权（resource + subject 组合）",
        field="resource_type+resource_id+subject_type+subject_id",
    )
    await invalidate(ctx.org_unit_id)  # ★
    return {"ok": True, "updated": sorted(update)}


def _validate_effect(effect: str) -> None:
    """``effect`` 只允许 allow / deny（deny 优先由 acl.py 兜底，入口先挡非法值）。"""
    if effect not in ("allow", "deny"):
        from app.core.errors import DomainError

        raise DomainError("effect 只允许 allow 或 deny", code="invalid_effect", status=422)


def _require_update(body: dict[str, Any], update: dict[str, Any], *, resource: str) -> dict[str, Any]:
    """PATCH 守卫：**空更新必须显式失败**，不得 200 假成功。

    ## 为什么

    早前各 PATCH 是 ``if update: await ...update_one(...)`` 然后**无条件**
    ``return {"ok": True}``——空 body（或字段全被白名单滤掉）时**什么都没改**
    却回 200 ok。这违背本项目一直在坚持的"不假装成功"（前端定时任务假成功、
    配额假数据都是同一类）。调用方无法区分"改成功"与"什么都没发生"。

    ## 两种空态要给**不同**的错误（都不该是 200）

    1. ``body`` 本身为空 → 422 ``empty_patch_body``：调用方明显忘了传内容；
    2. 字段**全被白名单过滤掉** → 422 ``no_writable_fields``：更危险的一种——
       调用方以为改了（比如传了 ``api_key_enc``），实际被静默丢弃。
       错误信息里**回显被拒的字段名**，让调用方知道该传什么。
    """
    from app.core.errors import DomainError

    if not body:
        raise DomainError(
            "未提供任何可更新字段（请求体为空）",
            code="empty_patch_body",
            status=422,
        )
    if not update:
        raise DomainError(
            f"提交的字段均不可写；{resource} 允许更新的字段：{sorted(_writable_of(resource))}",
            code="no_writable_fields",
            status=422,
            details={"rejected_fields": sorted(body.keys()), "resource": resource},
        )
    return update


#: 资源 → 可写字段表（供错误信息回显，避免调用方盲猜）
_WRITABLE_BY_RESOURCE: dict[str, set[str]] = {
    "model-providers": _PROVIDER_WRITABLE,
    "model-instances": _INSTANCE_WRITABLE,
    "tools": _TOOL_WRITABLE,
    "role-bindings": _BINDING_WRITABLE,
    "grants": _GRANT_WRITABLE,
}


def _writable_of(resource: str) -> set[str]:
    return _WRITABLE_BY_RESOURCE.get(resource, set())


def _oid(value: str):
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return value
