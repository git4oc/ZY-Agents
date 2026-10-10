"""★ 事件投影分级 / 脱敏唯一位置（``collaboration/projection.py``，``00`` §7.3、``01`` §4.2）。

- 全量事件只存一份（``kernel_events``），脱敏发生在投影层，不在存储层。
- 脱敏是**声明式**的（规则见 ``sensitivity.py``），不在代码里到处写 ``if``。
- viewer 的 SSE 推的是脱敏流，**不是前端过滤**（前端过滤等于没过滤，``00`` §9.1）。

接口（``01`` §4.2）：:

    project(event, *, viewer_role) -> ViewerEvent | None
        # owner / editor → FullViewerEvent（原样）
        # viewer        → RedactedViewerEvent（按 sensitivity 过滤）
        # 返回 None 表示该事件对该角色完全不可见
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.collaboration import sensitivity
from app.kernel.contract.events import KernelEventEnvelope

#: 角色字面量
ViewerRole = str  # owner | editor | viewer


@dataclass(frozen=True, slots=True)
class ViewerEvent:
    """投影后的 viewer 可见事件。"""

    event_id: str
    cursor: int
    type: str
    occurred_at: Any
    payload: dict[str, Any] = field(default_factory=dict)
    redacted: bool = False


def project(
    event: KernelEventEnvelope,
    *,
    viewer_role: ViewerRole,
) -> ViewerEvent | None:
    """把一条归一化内核事件投影为某 viewer 角色可见的视图。

    参数
    ----
    event:
        归一化的契约事件（``KernelEventEnvelope``）。
    viewer_role:
        查看者角色（owner / editor / viewer）。

    返回：
    - ``FullViewerEvent``（redacted=False）：owner / editor 原样。
    - ``RedactedViewerEvent``（redacted=True）：viewer 按敏感度过滤。
    - ``None``：该事件对该角色完全不可见。
    """
    if viewer_role in ("owner", "editor"):
        return ViewerEvent(
            event_id=event.event_id,
            cursor=event.cursor,
            type=event.type,
            occurred_at=event.occurred_at,
            payload=event.payload,
            redacted=False,
        )

    # viewer：按敏感度投影
    projected_payload = sensitivity.project_payload(
        event.type, event.payload, viewer_role=viewer_role
    )
    if _is_invisible(event.type, projected_payload):
        return None
    return ViewerEvent(
        event_id=event.event_id,
        cursor=event.cursor,
        type=event.type,
        occurred_at=event.occurred_at,
        payload=projected_payload,
        redacted=True,
    )


def _is_invisible(event_type: str, payload: dict[str, Any]) -> bool:
    """判定某个事件类型对 viewer 是否完全不可见。

    规则：事件负载脱敏后为空，且该事件类型核心字段全部为 secret 时视为不可见。
    简单实现：若脱敏后负载为空，且该类型没有任何 public 字段，则不可见。
    """
    if payload:
        return False
    table = sensitivity.SENSITIVITY_TABLE.get(event_type, {})
    has_visible_field = any(
        sens in (sensitivity.PUBLIC, sensitivity.INTERNAL) for sens in table.values()
    )
    return not has_visible_field
