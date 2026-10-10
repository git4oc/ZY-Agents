"""工具快照编译（``kernel/profile/tools.py``）。

把 ``tools`` 集合的文档行转换为 Profile 内的 ``ProfileTool`` 定义
（含类别 server/local/hybrid 与审批开关）。
"""

from __future__ import annotations

from typing import Any

from app.kernel.profile.models import ProfileTool


def compile_tool(row: dict[str, Any]) -> ProfileTool:
    """将一条 ``tools`` 集合文档编译为 ``ProfileTool``。

    ``tools`` 文档字段（``02`` §2.3）：name / description / category /
    input_schema / output_schema / risk_level / approval_required / timeout_ms。
    """
    return ProfileTool(
        name=str(row.get("name", "")),
        description=str(row.get("description", "")),
        input_schema=row.get("input_schema") or {},
        output_schema=row.get("output_schema") or None,
        category=row.get("category", "server"),
        risk_level=row.get("risk_level", "read"),
        approval_required=bool(row.get("approval_required", False)),
        timeout_ms=int(row.get("timeout_ms") or 30000),
    )
