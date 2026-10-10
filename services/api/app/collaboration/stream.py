"""SSE 推送 + 断线续传（``collaboration/stream.py``，``00`` §7.6 / ``01`` §4.2）。

- 每个参与者一条 SSE：``GET /api/v1/conversations/{id}/stream``。
- 事件带 ``cursor``（单调递增），断线重连带 ``Last-Event-ID`` 续传。
- **持久化优先**：先落库再推送，推送失败不影响正确性。
- turn 结束推终态事件，前端据此结束 loading。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from bson import ObjectId

from app.core import mongo
from app.core.clock import utcnow
from app.core.logging import get_logger
from app.core.errors import Forbidden
from app.collaboration.projection import ViewerEvent, project
from app.identity.context import RequestContext
from app.kernel.contract.events import KernelEventEnvelope

log = get_logger("collaboration.stream")

KERNEL_EVENTS_COLLECTION = "kernel_events"
PARTICIPANTS_COLLECTION = "session_participants"


class StreamSubscription:
    """一次 SSE 订阅。

    参数
    ----
    ctx:
        订阅者上下文（指定 viewer_role 用于投影）。
    conversation_id:
        订阅的会话。
    after_cursor:
        断线续传起点（Last-Event-ID）。
    """

    def __init__(
        self,
        ctx: RequestContext,
        conversation_id: str,
        *,
        after_cursor: int = 0,
    ) -> None:
        self._ctx = ctx
        self._conversation_id = conversation_id
        self._after_cursor = after_cursor
        self._viewer_role = "viewer"

    async def _resolve_role(self) -> None:
        parts = mongo.get_collection(PARTICIPANTS_COLLECTION)
        doc = await parts.find_one(
            {
                "conversation_id": self._conversation_id,
                "org_unit_id": self._ctx.org_unit_id,
                "user_id": _to_objid(self._ctx.user_id),
                "removed_at": None,
            }
        )
        if doc is None:
            raise Forbidden("您不是该会话的参与者")
        self._viewer_role = doc.get("role", "viewer")

    async def read_events(
        self, *, batch_size: int = 50
    ) -> list[ViewerEvent]:
        """读取从 ``after_cursor`` 之后的持久化事件并按角色投影。

        返回脱敏后的 viewer 事件列表（用于 SSE 发送）。
        """
        await self._resolve_role()
        coll = mongo.get_collection(KERNEL_EVENTS_COLLECTION)
        query: dict[str, Any] = {
            "org_unit_id": self._ctx.org_unit_id,
            "conversation_id": self._conversation_id,
        }
        if self._after_cursor > 0:
            query["cursor"] = {"$gt": self._after_cursor}
        cursor = coll.find(query).sort("cursor", 1).limit(batch_size)
        rows = await cursor.to_list(length=batch_size)

        result: list[ViewerEvent] = []
        for row in rows:
            envelope = _row_to_envelope(row)
            if envelope is not None:
                ev = project(envelope, viewer_role=self._viewer_role)
                if ev is not None:
                    result.append(ev)
        if result:
            self._after_cursor = result[-1].cursor
        return result


def subscribe(
    ctx: RequestContext,
    conversation_id: str,
    *,
    after_cursor: int = 0,
) -> StreamSubscription:
    """创建一次 SSE 订阅（``01`` §4.2 签名）。"""
    return StreamSubscription(ctx, conversation_id, after_cursor=after_cursor)


async def latest_cursor(ctx: RequestContext, conversation_id: str) -> int:
    """会话事件流当前尾部 cursor（无 ``Last-Event-ID`` 的 SSE 从尾部起，不重放历史）。

    从 0 起订会把全部历史事件重放给本轮气泡，且历史里的终态事件（run.completed）
    让前端读流提前收束——新 turn 的真实回复永远不显示。
    """
    coll = mongo.get_collection(KERNEL_EVENTS_COLLECTION)
    row = await coll.find_one(
        {
            "org_unit_id": ctx.org_unit_id,
            "conversation_id": conversation_id,
        },
        sort=[("cursor", -1)],
    )
    return int(row["cursor"]) if row else 0


def _to_objid(value: str) -> Any:
    try:
        return ObjectId(value)
    except Exception:
        return value


def _aware_utc(value: Any) -> Any:
    """Mongo 读回的 naive datetime 语义为 UTC（BSON Date 无 tzinfo），补齐 tzinfo。

    不补会让 ISO 序列化缺时区后缀，前端 ``Date.parse`` 按本地时区解析，
    run 时长恰好虚增一个 UTC 偏移量（东八区即 8 小时）。
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _row_to_envelope(row: dict[str, Any]) -> KernelEventEnvelope | None:
    """把 ``kernel_events`` 行反序列化为契约事件（供续传投影）。"""
    try:
        return KernelEventEnvelope(
            event_id=str(row.get("event_id", "")),
            runtime_id=str(row.get("runtime_id", "")),
            session_id=str(row.get("session_id", "")),
            parent_session_id=row.get("parent_session_id"),
            profile_version=str(row.get("profile_version", "")),
            cursor=int(row.get("cursor", 0)),
            type=str(row.get("type", "")),
            occurred_at=_aware_utc(row.get("occurred_at")) or utcnow(),
            payload=row.get("payload") or {},
            source=_source_from_row(row.get("source")),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("kernel_events 行反序列化失败", extra={"zy": {"error": str(exc)}})
        return None


def _source_from_row(source: Any) -> "KernelEventSource":
    from app.kernel.contract.events import KernelEventSource

    if isinstance(source, dict):
        return KernelEventSource(
            kernel=source.get("kernel", "dsh"),
            kernel_version=str(source.get("kernel_version", "")),
            native_event_type=source.get("native_event_type"),
        )
    return KernelEventSource(kernel="dsh", kernel_version="", native_event_type=None)
