"""命令与句柄模型（``03`` §2.2、§2.3）。

所有跨内核边界的值继承同一个严格基类 ``ContractModel``——**不可变 + 拒绝多余字段**
（``extra="forbid"``）。

``extra="forbid"`` 是刻意的（``03`` §2.2）：内核升级后若上游多发一个字段，这里会**炸**，
而不是静默忽略——在"内核会被换掉"的架构里，静默忽略比报错危险得多。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.kernel.contract.versions import AGENT_KERNEL_CONTRACT_VERSION


class ContractModel(BaseModel):
    """契约基类：不可变（frozen）+ 拒绝多余字段（extra="forbid"）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionStatus(str, Enum):
    """会话状态枚举。"""

    CREATING = "creating"
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    CANCELLING = "cancelling"
    FAILED = "failed"
    DISPOSED = "disposed"


class SendMode(str, Enum):
    """turn 发送模式。"""

    PROMPT = "prompt"  # 新输入
    FOLLOWUP = "followup"  # 续接
    STEER = "steer"  # 打断并改向


class CreateRuntimeRequest(ContractModel):
    """创建内核 runtime 的请求。"""

    schema_version: Literal["zy.agent-kernel.v1"] = AGENT_KERNEL_CONTRACT_VERSION
    org_unit_id: str  # ★ 组织单元维度（不引入独立 tenant 概念）
    profile_version: str
    isolation_key: str  # "{org_unit_id}:{user_id}"


class SessionSpec(ContractModel):
    """会话规格。"""

    conversation_id: str
    org_unit_id: str
    user_id: str
    profile_version: str
    preset_id: str = "zy-enterprise"  # 本项目自定 preset 名，不与他方 preset 混用
    execution_location: Literal["server"] = "server"
    metadata: dict[str, Any] = {}


class CreateSessionRequest(ContractModel):
    """创建会话的请求。"""

    schema_version: Literal["zy.agent-kernel.v1"] = AGENT_KERNEL_CONTRACT_VERSION
    runtime_id: str
    session_spec: SessionSpec


class ContentBlock(ContractModel):
    """消息内容块。"""

    type: Literal["text", "image", "document", "artifact_ref"]
    data: dict[str, Any] = {}


class TemporalContext(ContractModel):
    """服务端可信时钟快照。内核不自己取时间（``03`` §2.3）。"""

    captured_at_utc: datetime  # 必须 tz-aware
    user_local_time: datetime  # 必须 tz-aware
    user_timezone: str  # 必须是合法 IANA 时区


class SendRequest(ContractModel):
    """发起一次 turn 的请求。"""

    schema_version: Literal["zy.agent-kernel.v1"] = AGENT_KERNEL_CONTRACT_VERSION
    session_id: str
    request_id: str
    mode: SendMode = SendMode.PROMPT
    content: list[ContentBlock] = Field(min_length=1)
    temporal_context: TemporalContext | None = None
    turn_context: dict[str, Any] = {}


class CancelSessionRequest(ContractModel):
    """取消会话的请求。"""

    schema_version: Literal["zy.agent-kernel.v1"] = AGENT_KERNEL_CONTRACT_VERSION
    session_id: str
    cause: str
