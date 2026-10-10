"""确定性 Profile 编译（``03`` §3.1，``00`` §3）。

``compile_profile(org_unit_id, user_id)`` —— 内容寻址编译：

1. 聚合 admin 数据（模型实例 / 工具 / Skill / 用户的静态工具授权）成 Python 结构；
2. 规范化 JSON（``ensure_ascii=False, sort_keys=True, separators=(",",":")``）→ sha256；
3. ``content_hash`` = 全量 sha256；``profile_version = "rp-" + sha256[:24]``。

**纯函数式**：同输入必得同 ``profile_version``（不变量 2，``00`` §3.2）。
**编译输入不含任何运行时易变状态**（不变量 4）：尤其 ``local`` 工具是否入 Profile
只由静态授权决定，与在线状态无关；**品牌不进 Profile**（``S-60``）。
"""

from __future__ import annotations

import hashlib
import json

from app.core.logging import get_logger
from app.kernel.profile.catalog import (
    load_model_for_user,
    load_skills,
    load_tools_for_user,
)
from app.kernel.profile.models import (
    ProfileModelInstance,
    ProfileSkill,
    ProfileTool,
    RuntimeProfileSnapshot,
)
from app.kernel.profile.tools import compile_tool
from app.kernel.profile.skills import check_skill_dependencies

log = get_logger("kernel.profile.compiler")


def _canonical_json(payload: dict) -> str:
    """规范化 JSON 序列化——参数必须逐字一致，否则同输入得到不同 hash（``03`` §3.1）。"""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_profile_version(content_hash: str) -> str:
    """由全量哈希派生 profile_version = "rp-" + hash[:24]。"""
    return "rp-" + content_hash[:24]


def _build_compile_payload(
    *,
    org_unit_id: str,
    user_id: str,
    model: ProfileModelInstance | None,
    tools: list[ProfileTool],
    tool_versions: dict[str, str],
    skills: list[ProfileSkill],
    skill_versions: list[str],
    workflow_versions: list[str],
) -> dict:
    """构造参与内容寻址的规范负载。**不含品牌、不含密钥、不含在线状态。**"""
    return {
        "schema_version": "zy.runtime-profile.v1",
        "org_unit_id": org_unit_id,
        "subject_user_id": user_id,
        "model": model.model_dump() if model else None,
        "tools": [t.model_dump() for t in tools],
        "tool_versions": tool_versions,
        "skills": [s.model_dump() for s in skills],
        "skill_versions": skill_versions,
        "workflow_versions": workflow_versions,
    }


async def compile_profile(org_unit_id: str, user_id: str) -> RuntimeProfileSnapshot:
    """编译某用户在当前配置下的 Runtime Profile（每人一份）。

    参数
    ----
    org_unit_id:
        组织单元。
    user_id:
        编译主体（参与者不改变发起者的 Profile，见 ``00`` §3.2 不变量 3）。

    返回不可变 ``RuntimeProfileSnapshot``。

    异常
    ----
    SkillDependencyError:
        Skill 声明的工具依赖不在其 Profile 内（``00`` §3.4，缺依赖直接编译失败）。
    """
    # 1) 从 admin 数据读取编译输入（静态：模型 / 工具 / Skill / 授权）
    model = await load_model_for_user(org_unit_id, user_id)
    tools = [compile_tool(t) for t in await load_tools_for_user(org_unit_id, user_id)]
    skills, skill_versions, workflow_versions = await load_skills(org_unit_id)

    # 2) 校验 Skill 依赖（编译期，运行时无法越权加载）
    check_skill_dependencies(skills, tools)

    # 3) 工具版本快照（tool_name -> version）
    tool_versions = {t.name: _tool_version(t) for t in tools}

    payload = _build_compile_payload(
        org_unit_id=org_unit_id,
        user_id=user_id,
        model=model,
        tools=tools,
        tool_versions=tool_versions,
        skills=skills,
        skill_versions=skill_versions,
        workflow_versions=workflow_versions,
    )

    canonical = _canonical_json(payload)
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    profile_version = compute_profile_version(content_hash)

    return RuntimeProfileSnapshot(
        schema_version="zy.runtime-profile.v1",
        profile_version=profile_version,
        content_hash=content_hash,
        org_unit_id=org_unit_id,
        subject_user_id=user_id,
        model=model,
        tools=tools,
        tool_versions=tool_versions,
        skills=skills,
        skill_versions=skill_versions,
        workflow_versions=workflow_versions,
    )


def _tool_version(tool: ProfileTool) -> str:
    """派生工具版本快照（稳定标识 + 内容指纹）。"""
    raw = json.dumps(tool.model_dump(), ensure_ascii=False, sort_keys=True)
    return "tv-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
