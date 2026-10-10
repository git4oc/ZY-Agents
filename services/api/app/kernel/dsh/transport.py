"""NDJSON 事件流解析 / 心跳 / 重连（``kernel/dsh/transport.py``，``03`` §5）。

主服务侧按行解析宿主 ``/v1/runtimes/{id}/sessions/{sid}/event-stream`` 的 NDJSON 流，
逐条归一化为 ``KernelEventEnvelope``。断线续传（``after_cursor``）与心跳在收尾层处理。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.kernel.contract.events import KernelEventEnvelope, KernelEventSource
from app.kernel.dsh.adapters import events as events_adapter

log = get_logger("kernel.dsh.transport")


class EventStreamTransport:
    """对宿主 NDJSON 事件流的流式读取与归一化。"""

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/x-ndjson",
        }

    async def subscribe(
        self, session_id: str, *, after_cursor: int = 0
    ) -> AsyncIterator[KernelEventEnvelope]:
        """订阅会话事件流，逐条 yield 归一化后的事件。

        断线重连（``03`` §5 / ``00`` §7.6）：连接中断时尝试续传，cursor 单调递增由适配层保证。
        """
        url = f"{self._base_url}/v1/runtimes/_/sessions/{session_id}/event-stream"
        if after_cursor and after_cursor > 0:
            url += f"?after_cursor={after_cursor}"

        # 阶段 1 单次流式读取；完整重连/心跳由上层 runner 编排。
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url, headers=self._headers()) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        log.error(
                            "event stream http error",
                            extra={"zy": {"status": resp.status_code, "body": body[:500]}},
                        )
                        raise RuntimeError(f"event-stream failed: HTTP {resp.status_code}")
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            log.warning("非 JSON 的 NDJSON 行", extra={"zy": {"line": line[:300]}})
                            continue
                        envelope = events_adapter.normalize(obj)
                        if envelope is not None and envelope.cursor >= after_cursor:
                            yield envelope
        except httpx.HTTPError as exc:
            log.warning("event stream disconnected", extra={"zy": {"session_id": session_id, "error": str(exc)}})
            # 断线后交由上层 recovery 决定是否续跑；此处结束迭代
            return
