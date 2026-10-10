"""句柄与状态（``03`` §2.3）。

runtime / session 句柄是不可变契约模型；状态枚举见 ``commands.SessionStatus``。
"""

from __future__ import annotations

from typing import Literal

from app.kernel.contract.commands import ContractModel, SessionStatus
from app.kernel.contract.versions import AGENT_KERNEL_CONTRACT_VERSION


class RuntimeHandle(ContractModel):
    """内核 runtime 句柄。"""

    schema_version: Literal["zy.agent-kernel.v1"] = AGENT_KERNEL_CONTRACT_VERSION
    runtime_id: str
    kernel: Literal["dsh"] = "dsh"
    kernel_version: str
    profile_version: str
    model_instance_id: str | None = None
    isolation_key: str


class SessionHandle(ContractModel):
    """内核会话句柄。"""

    runtime_id: str
    session_id: str
    conversation_id: str
    profile_version: str
    preset_id: str
    status: SessionStatus
