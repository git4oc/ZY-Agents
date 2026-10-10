"""单 turn 执行器（``kernel/dsh/runner.py``，``01`` §2 目录树）。

负责一次 turn 的驱动：向宿主 send，订阅事件流，把归一化事件交给上层（collaboration）
持久化与投影，直到收到终态事件。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.kernel.contract.commands import SendRequest
from app.kernel.contract.events import KernelEventEnvelope
from app.kernel.dsh.gateway import DshKernelGateway

log = get_logger("kernel.dsh.runner")


class TurnRunner:
    """单 turn 执行器。

    参数
    ----
    gateway:
        已装配的 DshKernelGateway（AgentKernelContract 实现）。
    """

    def __init__(self, gateway: DshKernelGateway) -> None:
        self._gateway = gateway

    async def run(
        self,
        request: SendRequest,
        *,
        on_event: Any | None = None,
    ) -> str:
        """执行一次 turn。

        参数
        ----
        request:
            SendRequest（含会话 id 与内容）。
        on_event:
            可选回调，每收到一条归一化事件后调用（用于持久化/投影/推送）。

        返回 request_id。
        """
        request_id = await self._gateway.send(request)
        log.info(
            "turn started",
            extra={"zy": {"session_id": request.session_id, "request_id": request_id}},
        )

        # 订阅事件流，交 on_event 处理；直至收到终态
        async for envelope in self._gateway.subscribe(request.session_id):
            if on_event is not None:
                await on_event(envelope)
        return request_id
