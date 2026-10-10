"""事件适配（``kernel/dsh/adapters/events.py``）。

把宿主的**原生事件**归一化为契约 ``KernelEventEnvelope``（``03`` §2.4）。

``source.native_event_type`` 是唯一允许把上游类型名带进产品层的位置，且只用于排查。
产品逻辑与前端永远只看 ``type``——这就是"上游事件变化不波及上层"的实现方式。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.clock import utcnow
from app.kernel.contract.events import KernelEventEnvelope, KernelEventSource

#: 上游原生事件类型 → 归一化类型（自己定义）。新增事件类型在此登记。
#:
#: ``turn_end``（→ ``turn.finished``）与 ``session_error``（→ ``error``）**必须**
#: 落进 ``TERMINAL_TYPES``——驱动层的事件消费循环靠它终止（否则 keep-alive 流挂起）。
_NATIVE_TO_NORMALIZED: dict[str, str] = {
    "message": "assistant.message",
    "tool_call_requested": "tool.call.requested",
    "tool_call_completed": "tool.call.completed",
    "tool_progress": "tool.progress",
    "artifact_created": "artifact.created",
    "model_request": "model.request",
    "error": "error",
    "auth_required": "localagent.auth_required",
    "turn_finished": "turn.finished",
    # kernel-host（cordis 4.0.4，zy.dsh-host.v1）实际发射的原生类型
    "model_call": "model.request",
    "tool_call": "tool.call.requested",
    "tool_result": "tool.call.completed",
    "turn_start": "turn.started",
    "turn_end": "turn.finished",
    "session_state": "session.state",
    "session_error": "error",
}

#: 终态事件（用于判断 turn 结束）
TERMINAL_TYPES = {"turn.finished", "error"}


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return utcnow()


def normalize(native: dict[str, Any]) -> KernelEventEnvelope | None:
    """把宿主侧一条原生事件归一化为契约事件。

    参数
    ----
    native:
        宿主 NDJSON 的一行对象。

    返回 KernelEventEnvelope（无法归一化时返回 None，交由上层决定是否 ignorable）。
    """
    native_type = native.get("type") or ""
    normalized_type = _NATIVE_TO_NORMALIZED.get(native_type, native_type)

    payload = dict(native.get("payload") or {})
    envelope = KernelEventEnvelope(
        event_id=str(native.get("eventId") or native.get("event_id") or ""),
        runtime_id=str(native.get("runtimeId") or native.get("runtime_id") or ""),
        session_id=str(native.get("sessionId") or native.get("session_id") or ""),
        parent_session_id=native.get("parentSessionId") or native.get("parent_session_id"),
        profile_version=str(native.get("profileVersion") or native.get("profile_version") or ""),
        cursor=int(native.get("cursor") or 0),
        type=normalized_type,
        occurred_at=_parse_dt(native.get("occurredAt") or native.get("occurred_at")),
        payload=payload,
        source=KernelEventSource(
            kernel="dsh",
            kernel_version=str(native.get("kernelVersion") or native.get("kernel_version") or ""),
            native_event_type=native_type or None,
        ),
    )
    return envelope
