"""内核事件持久化（``collaboration/events.py``，#18）。

**持久化优先**：事件先落 ``kernel_events`` 集合，SSE 推送只是读侧投影；
推送失败不影响正确性，断线重连凭 ``cursor``（单调递增）续传。

- ``cursor`` 由 ``kernel_event_counters`` 计数集合原子 ``$inc`` 分配，会话内严格单调；
- ``persist_envelope`` 是 main.py 装配的 ``kernel/turn/driver`` 事件回调终点：
  ``kernel → collaboration`` 禁止直连，经回调注入（``01`` §3 分层）。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core import mongo
from app.core.clock import utcnow
from app.core.logging import get_logger

log = get_logger("collaboration.events")

KERNEL_EVENTS_COLLECTION = "kernel_events"
COUNTERS_COLLECTION = "kernel_event_counters"


async def next_cursor(org_unit_id: str, conversation_id: str) -> int:
    """原子分配会话内下一个 cursor（从 1 开始，单调递增）。"""
    coll = mongo.get_collection(COUNTERS_COLLECTION)
    from pymongo import ReturnDocument

    doc = await coll.find_one_and_update(
        {"org_unit_id": org_unit_id, "conversation_id": conversation_id},
        {"$inc": {"next_cursor": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc.get("next_cursor", 1))


async def persist_envelope(
    org_unit_id: str,
    conversation_id: str,
    *,
    envelope: Any,
) -> int:
    """把一条契约事件（``KernelEventEnvelope``）持久化到 ``kernel_events``。

    cursor 由本模块重新分配（上游 envelope.cursor 不信任）；返回分配的 cursor。
    """
    coll = mongo.get_collection(KERNEL_EVENTS_COLLECTION)
    cursor = await next_cursor(org_unit_id, conversation_id)
    source = envelope.source
    doc = {
        "org_unit_id": org_unit_id,
        "conversation_id": conversation_id,
        "event_id": envelope.event_id or str(uuid.uuid4()),
        "runtime_id": envelope.runtime_id,
        "session_id": envelope.session_id or conversation_id,
        "profile_version": envelope.profile_version,
        "cursor": cursor,
        "type": envelope.type,
        "occurred_at": envelope.occurred_at or utcnow(),
        "payload": envelope.payload or {},
        "source": source.model_dump() if hasattr(source, "model_dump") else dict(source),
    }
    await coll.insert_one(doc)
    log.debug(
        "kernel event persisted",
        extra={"zy": {"conversation_id": conversation_id, "type": envelope.type, "cursor": cursor}},
    )
    return cursor
