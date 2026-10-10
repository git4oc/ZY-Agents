"""工具适配（``kernel/dsh/adapters/tools.py``）。

把 Profile 内工具定义映射为宿主可装配的工具注册描述（``03`` §6.2 ``ToolFormat``）。
上游工具定义形状变化只改这里。
"""

from __future__ import annotations

from typing import Any

from app.kernel.profile.models import ProfileTool


def to_registration(tool: ProfileTool) -> dict[str, Any]:
    """把 ProfileTool 编译为宿主 ``ctx.tools.register`` 的注册描述。

    与 ``03`` §6.2 对齐：name / description / parameters（即 input_schema）/
    output( schema + render ) / timeoutMs / isConcurrencySafe。
    """
    is_read = tool.risk_level == "read"
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.input_schema,
        "output": {
            "schema": tool.output_schema or {"type": "object"},
            "present_call": True,
            "present_result": True,
        },
        "timeoutMs": tool.timeout_ms,
        "isConcurrencySafe": is_read,  # 仅 read 型才可并行（``03`` §6.2）
        "approvalRequired": tool.approval_required,
        "category": tool.category,
    }
