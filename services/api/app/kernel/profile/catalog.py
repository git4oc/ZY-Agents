"""admin 行 → 编译输入（``kernel/profile/catalog.py``）。

从 Mongo 读模型实例 / 工具 / Skill / 用户的静态工具授权，作为 Profile 编译输入。

关键约束（``00`` §3.2 不变量 4 / ``S-60``）：此处**只读静态配置**，
不读任何运行时易变状态（品牌、在线性、当前日期）。
"""

from __future__ import annotations

from typing import Any

from app.core import mongo
from app.kernel.profile.models import ProfileModelInstance, ProfileSkill

#: 集合名
_TOOLS_COLLECTION = "tools"
_SKILLS_COLLECTION = "skills"
_SKILL_VERSIONS_COLLECTION = "skill_versions"
_MODEL_INSTANCES_COLLECTION = "model_instances"
_MODEL_PROVIDERS_COLLECTION = "model_providers"
_GRANTS_COLLECTION = "resource_grants"

#: 工具对用户可见性：按资源授权（resource_grants, resource_type=tool）
_SERVER_TOOL_KINDS = {"server", "hybrid"}
#: local 类工具是否入 Profile 只由静态授权决定（与在线性无关）
_LOCAL_TOOL_GRANT = "use"


async def _granted_tool_names(org_unit_id: str, user_id: str) -> set[str]:
    """读取用户对工具的**静态授权**（resource_grants 中 subject 命中用户/角色/all 的 tool 授权）。

    deny 优先：任一条 deny 即剔除；否则任一条 allow 即授予。
    """
    grants = mongo.get_collection(_GRANTS_COLLECTION)
    cursor = grants.find(
        {
            "org_unit_id": org_unit_id,
            "resource_type": "tool",
            "subject_type": {"$in": ["user", "role", "all"]},
        }
    )
    rows = await cursor.to_list(length=None)

    denied: set[str] = set()
    allowed: set[str] = set()
    for g in rows:
        rid = str(g.get("resource_id"))
        effect = g.get("effect")
        if effect == "deny":
            denied.add(rid)
        elif effect == "allow":
            allowed.add(rid)
    return allowed - denied


async def load_tools_for_user(org_unit_id: str, user_id: str) -> list[dict[str, Any]]:
    """读取某用户 Profile 中应包含的工具定义（静态授权 + status=active）。

    返回原始工具文档列表（调用方经 ``tools.compile_tool`` 转为 ProfileTool）。
    """
    granted = await _granted_tool_names(org_unit_id, user_id)
    coll = mongo.get_collection(_TOOLS_COLLECTION)
    cursor = coll.find({"org_unit_id": org_unit_id, "status": "active"})
    rows = await cursor.to_list(length=None)
    return [r for r in rows if str(r.get("name")) in granted]


async def load_model_for_user(
    org_unit_id: str, user_id: str
) -> ProfileModelInstance | None:
    """读取用户的默认模型实例元数据（不含 api_key）。未配置则返回 None。"""
    providers = mongo.get_collection(_MODEL_PROVIDERS_COLLECTION)
    instances = mongo.get_collection(_MODEL_INSTANCES_COLLECTION)

    inst_cursor = instances.find({"org_unit_id": org_unit_id, "status": "active"})
    inst_rows = await inst_cursor.to_list(length=None)
    if not inst_rows:
        return None
    # 取第一个 active 实例作为默认（阶段 1 简化；多实例选择属于能力强化的范围）
    inst = inst_rows[0]
    provider_id = inst.get("provider_id")

    provider = None
    if provider_id is not None:
        provider = await providers.find_one(
            {"org_unit_id": org_unit_id, "_id": provider_id, "status": "active"}
        )
    if provider is None:
        provider = {}

    return ProfileModelInstance(
        model_instance_id=str(inst.get("_id")),
        provider_id=str(inst.get("provider_id", "")),
        provider_type=str(provider.get("provider_type", "openai_compatible")),
        provider_name=str(provider.get("name", "")),
        model_name=str(inst.get("model_name", "")),
        display_name=str(inst.get("display_name", "")),
        capabilities=list(inst.get("capabilities") or ["chat"]),
        context_window=int(inst.get("max_context_tokens") or 0),
        max_output_tokens=int((inst.get("settings") or {}).get("max_output_tokens") or 0),
    )


async def load_skills(
    org_unit_id: str,
) -> tuple[list[ProfileSkill], list[str], list[str]]:
    """读取某组织单元的可用 Skill（status=active），返回 ``(skills, skill_versions, workflow_versions)``。"""
    skills_coll = mongo.get_collection(_SKILLS_COLLECTION)
    versions_coll = mongo.get_collection(_SKILL_VERSIONS_COLLECTION)

    cursor = skills_coll.find({"org_unit_id": org_unit_id, "status": "active"})
    skill_rows = await cursor.to_list(length=None)

    skills: list[ProfileSkill] = []
    skill_versions: list[str] = []
    workflow_versions: list[str] = []

    for row in skill_rows:
        skill_id = row.get("_id")
        version = row.get("current_version") or "0.0.0"
        definition: dict[str, Any] = {}
        if skill_id is not None:
            v = await versions_coll.find_one(
                {"skill_id": skill_id, "version": version}, sort=[("created_at", -1)]
            )
            if v is not None:
                definition = v.get("definition") or {}

        key = str(row.get("key"))
        kind = row.get("kind", "skill")
        skills.append(
            ProfileSkill(
                key=key,
                name=str(row.get("name", key)),
                kind=kind,
                version=version,
                definition=definition,
            )
        )
        if kind == "workflow":
            workflow_versions.append(f"{key}@{version}")
        else:
            skill_versions.append(f"{key}@{version}")

    return skills, skill_versions, workflow_versions
