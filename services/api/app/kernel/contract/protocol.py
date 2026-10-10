"""AgentKernelContract —— 内核中立协议（``03`` §2.5）。

DSH 只是其中一个实现（``kernel/dsh/gateway.py``）。换内核 = 换实现，契约不动。

分层规则（``01`` §3）：``kernel/dsh/`` 是实现此协议的唯一位置；
除 ``kernel/dsh/gateway.py`` 自身外，任何模块禁止 import ``kernel/dsh/``。
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from app.kernel.contract.commands import (
    CancelSessionRequest,
    CreateRuntimeRequest,
    CreateSessionRequest,
    SendRequest,
)
from app.kernel.contract.events import KernelEventEnvelope
from app.kernel.contract.handles import RuntimeHandle, SessionHandle


class AgentKernelContract(Protocol):
    """主服务对内核的完整操作契约。

    实现方（目前仅 ``DshKernelGateway``）必须提供以下全部方法。
    """

    async def create_runtime(self, request: CreateRuntimeRequest) -> RuntimeHandle:
        """创建内核 runtime。"""
        ...

    async def dispose_runtime(self, runtime_id: str) -> None:
        """释放内核 runtime。"""
        ...

    async def create_session(self, request: CreateSessionRequest) -> SessionHandle:
        """创建内核会话。"""
        ...

    async def resume_session(self, session_id: str) -> SessionHandle:
        """恢复既有会话（Profile 版本一致才可恢复）。"""
        ...

    async def send(self, request: SendRequest) -> str:
        """发起一次 turn，返回 request_id。"""
        ...

    async def cancel(self, request: CancelSessionRequest) -> dict[str, Any]:
        """取消一次 turn，返回取消结果。"""
        ...

    async def dispose_session(self, session_id: str) -> None:
        """释放内核会话。"""
        ...

    def subscribe(
        self, session_id: str, *, after_cursor: int = 0
    ) -> AsyncIterator[KernelEventEnvelope]:
        """订阅会话事件流（断线续传时从 after_cursor 之后开始）。"""
        ...
