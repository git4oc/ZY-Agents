"""preset 适配（``kernel/dsh/adapters/preset.py``）。

把内核 preset id（``zy-enterprise``，``00`` §0.5 / ``03`` §2.3）映射到宿主装配时的 preset 组合。
``01`` §3.2 / ``00`` §10：上游 preset 组合变化时只改这里。
"""

from __future__ import annotations

from typing import Any


def preset_definition(preset_id: str) -> dict[str, Any]:
    """返回指定 preset 的宿主装配定义。

    参数
    ----
    preset_id:
        内核 preset id（默认 ``zy-enterprise``）。

    返回宿主装配所需的结构（工具集、模型接线等）。
    """
    if preset_id == "zy-enterprise":
        return {
            "id": "zy-enterprise",
            "name": "ZY Enterprise",
            "execution_location": "server",
            "floating_versions": False,
        }
    # 未知 preset：返回最小默认，宿主侧 fail-closed 会拒绝
    return {"id": preset_id, "name": preset_id, "execution_location": "server", "floating_versions": False}
