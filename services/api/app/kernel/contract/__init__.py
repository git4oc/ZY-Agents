"""kernel/contract —— 冻结契约（版本化，只增不改）。

见 ``03-kernel-contract.md`` §1、§2。契约命名空间全部以 ``zy`` 为核心（``00`` §0.5）。

分层规则（``01`` §3）：``app.* → kernel/contract/`` 允许；``kernel/ → collaboration/`` 禁止。
"""

from app.kernel.contract.commands import ContractModel
from app.kernel.contract.commands import (
    CancelSessionRequest,
    ContentBlock,
    CreateRuntimeRequest,
    CreateSessionRequest,
    SendMode,
    SendRequest,
    SessionSpec,
    TemporalContext,
)
from app.kernel.contract.errors import KernelError
from app.kernel.contract.events import KernelEventEnvelope, KernelEventSource
from app.kernel.contract.handles import (
    RuntimeHandle,
    SessionHandle,
    SessionStatus,
)
from app.kernel.contract.protocol import AgentKernelContract
from app.kernel.contract.versions import (
    AGENT_KERNEL_CONTRACT_VERSION,
    DSH_HOST_PROTOCOL_VERSION,
    KERNEL_EVENT_VERSION,
    RUNTIME_PROFILE_SCHEMA_VERSION,
)

__all__ = [
    "AGENT_KERNEL_CONTRACT_VERSION",
    "KERNEL_EVENT_VERSION",
    "RUNTIME_PROFILE_SCHEMA_VERSION",
    "DSH_HOST_PROTOCOL_VERSION",
    "ContractModel",
    "CreateRuntimeRequest",
    "CreateSessionRequest",
    "SendRequest",
    "CancelSessionRequest",
    "SessionSpec",
    "TemporalContext",
    "ContentBlock",
    "SendMode",
    "RuntimeHandle",
    "SessionHandle",
    "SessionStatus",
    "KernelEventEnvelope",
    "KernelEventSource",
    "KernelError",
    "AgentKernelContract",
]
