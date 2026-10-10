"""消息仓储（``collaboration/messages.py``，``01`` §4.2 / ``02`` §2.4 集合 ``messages``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection

from app.core import mongo
from app.core.clock import utcnow
from app.identity.context import RequestContext

COLLECTION = "messages"
PARTICIPANTS_COLLECTION = "session_participants"

MessageRole = Literal["user", "assistant", "system", "tool"]
#: 内容块类型（``02`` §2.4）：{type: text|image|document|artifact_ref, data}
ContentBlock = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Message:
    """会话内的一条消息。"""

    id: str
    conversation_id: str
    seq: int
    role: str
    content: list[ContentBlock]
    meta: dict[str, Any]
    created_at: Any


class MessagesRepository:
    """消息数据访问。"""

    def __init__(
        self,
        *,
        collection: AsyncIOMotorCollection | None = None,
        parts_collection: AsyncIOMotorCollection | None = None,
    ) -> None:
        self._coll = collection or mongo.get_collection(COLLECTION)
        self._parts = parts_collection or mongo.get_collection(PARTICIPANTS_COLLECTION)

    async def _assert_participant(self, ctx: RequestContext, conversation_id: str) -> None:
        from app.core.errors import Forbidden

        doc = await self._parts.find_one(
            {
                "conversation_id": conversation_id,
                "org_unit_id": ctx.org_unit_id,
                "user_id": _to_objid(ctx.user_id),
                "removed_at": None,
            }
        )
        if doc is None:
            raise Forbidden("您不是该会话的参与者")

    async def append(
        self,
        ctx: RequestContext,
        conversation_id: str,
        *,
        seq: int,
        role: MessageRole,
        content: list[ContentBlock],
        meta: dict[str, Any],
    ) -> Message:
        """向会话追加一条消息。"""
        await self._assert_participant(ctx, conversation_id)
        now = utcnow()
        doc = {
            "org_unit_id": ctx.org_unit_id,
            "conversation_id": conversation_id,
            "seq": seq,
            "role": role,
            "content": content,
            "meta": meta,
            "created_at": now,
        }
        result = await self._coll.insert_one(doc)
        return Message(
            id=str(result.inserted_id),
            conversation_id=conversation_id,
            seq=seq,
            role=role,
            content=content,
            meta=meta,
            created_at=now,
        )

    async def list(
        self,
        ctx: RequestContext,
        conversation_id: str,
        *,
        before_seq: int | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """列出会话消息（分页，按 seq 降序取最近若干条后倒序返回）。"""
        await self._assert_participant(ctx, conversation_id)
        query: dict[str, Any] = {"conversation_id": conversation_id}
        if before_seq is not None:
            query["seq"] = {"$lt": before_seq}
        cursor = self._coll.find(query).sort("seq", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        rows.reverse()
        return [
            Message(
                id=str(r.get("_id")),
                conversation_id=conversation_id,
                seq=int(r.get("seq", 0)),
                role=str(r.get("role", "")),
                content=r.get("content") or [],
                meta=r.get("meta") or {},
                created_at=r.get("created_at"),
            )
            for r in rows
        ]

    async def next_seq(self, conversation_id: str) -> int:
        """返回会话内下一个 seq（当前最大 +1；无消息则为 0）。"""
        doc = await self._coll.find_one(
            {"conversation_id": conversation_id}, sort=[("seq", -1)]
        )
        return int(doc.get("seq", -1)) + 1 if doc else 0


def _to_objid(value: str) -> Any:
    try:
        return ObjectId(value)
    except Exception:
        return value
