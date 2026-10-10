"""断线续跑（``kernel/dsh/recovery.py``，``01`` §2 目录树）。

会话事件持久化优先（``00`` §7.6）：先落库再推送，推送失败不影响正确性。
断线重连时按 ``after_cursor`` 续传（``kernel_events.cursor`` 单调递增）。
"""

from __future__ import annotations

from typing import Any

from app.core import mongo
from app.core.clock import utcnow
from app.core.logging import get_logger
from app.kernel.contract.events import KernelEventEnvelope

log = get_logger("kernel.dsh.recovery")

KERNEL_EVENTS_COLLECTION = "kernel_events"


async def last_cursor(*, org_unit_id: str, conversation_id: str) -> int:
    """读取某会话已持久化的最大 cursor（供断线续传起点）。"""
    coll = mongo.get_collection(KERNEL_EVENTS_COLLECTION)
    doc = await coll.find_one(
        {"org_unit_id": org_unit_id, "conversation_id": conversation_id},
        sort=[("cursor", -1)],
    )
    return int(doc.get("cursor")) if doc else 0


async def persist_event(
    *, org_unit_id: str, conversation_id: str, envelope: KernelEventEnvelope
) -> None:
    """把一条归一化事件持久化到 ``kernel_events``（全量一份，``02`` §2.4）。

    ``{conversation_id, cursor}`` 唯一——重复投递时幂等（``$setOnInsert``）。
    """
    coll = mongo.get_collection(KERNEL_EVENTS_COLLECTION)
    await coll.update_one(
        {"org_unit_id": org_unit_id, "conversation_id": conversation_id, "cursor": envelope.cursor},
        {
            "$setOnInsert": {
                "session_id": envelope.session_id,
                "runtime_id": envelope.runtime_id,
                "profile_version": envelope.profile_version,
                "event_id": envelope.event_id,
                "type": envelope.type,
                "occurred_at": envelope.occurred_at,
                "payload": envelope.payload,
                "source": envelope.source.model_dump(),
            }
        },
        upsert=True,
    )
