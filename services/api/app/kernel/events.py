"""内核事件出口（``kernel/events.py``）—— kernel 层的**唯一**事件发布口。

**为什么需要本模块**：``01`` §3 分层规则规定 ``kernel/ → collaboration/`` **禁止**
（内核回调走 ``inbound/`` 独立端点）。因此 ``kernel/`` 内的任何模块（含
``kernel/inbound/tool_endpoint.py``）都**不能**直接 import
``collaboration/events.py`` 的 ``persist_envelope``。

解法沿用本仓库既有的**回调注入**模式（与 ``kernel/turn/driver.py`` 的
``set_event_handler`` 完全一致）：本模块持有一个可注入的 sink，装配期（``main.py``
lifespan）由 ``app.collaboration.events.persist_envelope`` 注入，kernel 侧只负责
**构造契约事件并交给 sink**，不感知持久化落在哪个集合。

事件类型命名沿用 ``02`` §3 已声明的类型（``tool.call.requested`` /
``tool.call.completed``）以复用既有脱敏规则；``tool.call.failed`` 与
``tool.approval.*`` 为本轮新增，已同步登记进 ``collaboration/sensitivity.py``
的声明表——否则 viewer 会因"未声明字段按 secret 处理"而完全看不到这些事件。
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.kernel.contract.events import KernelEventEnvelope, KernelEventSource

log = get_logger("kernel.events")

#: 事件出口回调（装配期注入）。签名：async (org_unit_id, conversation_id, *, envelope) -> Any
_sink: Callable[..., Awaitable[Any]] | None = None


def set_sink(sink: Callable[..., Awaitable[Any]] | None) -> None:
    """注入事件出口（由 ``main.py`` lifespan 调用）。

    传入 ``None`` 可解绑（测试用）。**kernel 层只依赖本函数签名，不 import
    collaboration**（``01`` §3）。
    """
    global _sink
    _sink = sink


def has_sink() -> bool:
    """是否已装配事件出口（未装配时 emit 退化为只记日志）。"""
    return _sink is not None


def make_envelope(
    *,
    event_type: str,
    payload: dict[str, Any],
    session_id: str = "",
    runtime_id: str = "",
    profile_version: str = "",
    native_event_type: str | None = None,
) -> KernelEventEnvelope:
    """构造一条契约事件（``zy.kernel-event.v1``）。

    ``cursor`` 在此处填 0——真正的会话内单调 cursor 由
    ``collaboration/events.py`` 的计数器分配后回写（持久化层拥有分配权）。
    """
    return KernelEventEnvelope(
        event_id=uuid.uuid4().hex,
        runtime_id=runtime_id,
        session_id=session_id,
        profile_version=profile_version,
        cursor=0,
        type=event_type,
        occurred_at=utcnow(),
        payload=payload,
        source=KernelEventSource(
            kernel="dsh",
            kernel_version="",
            native_event_type=native_event_type,
        ),
    )


async def emit(
    *,
    org_unit_id: str,
    conversation_id: str | None,
    event_type: str,
    payload: dict[str, Any],
    session_id: str = "",
    runtime_id: str = "",
    profile_version: str = "",
) -> bool:
    """发布一条事件到 kernel_events（经注入的 sink）。

    **降级策略（conversation_id 可能为 None）**：工具调用不一定绑定会话
    （例如后台任务直接经工具网关回调）。``kernel_events`` 的唯一索引是
    ``{conversation_id, cursor}``，且 SSE 订阅以会话为维度，因此
    **无 conversation_id 时不落事件流**，只记 debug 日志并返回 ``False``——
    绝不硬造一个假会话 id 污染事件流（那会让 SSE 消费者收到不属于任何会话的事件）。
    审计（``audit_logs``）不依赖会话，仍然照常落库，所以该场景**不丢审计**。

    返回是否成功投递到 sink。
    """
    if not conversation_id:
        log.debug(
            "event skipped: no conversation_id",
            extra={"zy": {"type": event_type, "org_unit_id": org_unit_id}},
        )
        return False
    if _sink is None:
        log.debug(
            "event skipped: sink not wired",
            extra={"zy": {"type": event_type, "conversation_id": conversation_id}},
        )
        return False
    envelope = make_envelope(
        event_type=event_type,
        payload=payload,
        session_id=session_id or conversation_id,
        runtime_id=runtime_id,
        profile_version=profile_version,
    )
    try:
        await _sink(org_unit_id=org_unit_id, conversation_id=conversation_id, envelope=envelope)
        return True
    except Exception as exc:  # noqa: BLE001 - 事件落库失败不阻断工具返回
        log.warning(
            "event emit failed",
            extra={"zy": {"type": event_type, "error": str(exc)}},
        )
        return False
