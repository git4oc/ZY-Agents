"""内核事件 envelope（``03`` §2.4）。

事件统一归一化为 ``KernelEventEnvelope`` 进入产品层；``source.native_event_type`` 是**唯一**
允许把上游类型名带进产品层的位置，且只用于排查。产品逻辑与前端**永远**只看 ``type``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from app.kernel.contract.commands import ContractModel
from app.kernel.contract.versions import KERNEL_EVENT_VERSION


class KernelEventSource(ContractModel):
    """事件来源信息（用于排查与审计）。"""

    kernel: Literal["dsh"] = "dsh"
    kernel_version: str
    native_event_type: str | None = None  # ★ 上游原始类型名，只为排查保留


class KernelEventEnvelope(ContractModel):
    """一条归一化后的内核事件。"""

    schema_version: Literal["zy.kernel-event.v1"] = KERNEL_EVENT_VERSION
    event_id: str
    runtime_id: str
    session_id: str
    parent_session_id: str | None = None
    profile_version: str
    cursor: int  # ge=0，会话内单调递增
    type: str  # 归一化后的事件类型（自己定义）
    occurred_at: datetime  # 必须 tz-aware
    payload: dict[str, Any] = {}
    source: KernelEventSource
