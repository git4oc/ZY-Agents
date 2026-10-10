"""Skill 编译 + 依赖校验（``kernel/profile/skills.py``）。

Skill 声明它需要的工具，编译器校验该工具是否在此用户 Profile 内——
**缺依赖直接编译失败**，而不是运行时才发现（``00`` §3.4）。
"""

from __future__ import annotations

from app.core.errors import SkillDependencyError
from app.kernel.profile.models import ProfileSkill, ProfileTool


def check_skill_dependencies(
    skills: list[ProfileSkill], tools: list[ProfileTool]
) -> None:
    """校验每个 Skill 声明的 ``requires_tools`` 是否都在 Profile 内。

    参数
    ----
    skills:
        编译后的 Skill 列表。
    tools:
        编译后的工具列表（当前用户 Profile 内）。

    异常
    ----
    SkillDependencyError:
        任一 Skill 依赖的工具不在工具集合中时抛出（编译失败）。
    """
    tool_names = {t.name for t in tools}
    for skill in skills:
        required = skill.definition.get("requires_tools") or []
        for req in required:
            if req not in tool_names:
                raise SkillDependencyError(
                    f"Skill '{skill.key}' 依赖的工具 '{req}' 不在该用户 Profile 内",
                    details={"skill_key": skill.key, "missing_tool": req},
                )
