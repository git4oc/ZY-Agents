"""参与者管理（``collaboration/participants.py``，``01`` §4.2）。

仅 owner 可管理参与者。移除参与者时设 ``removed_at``，同时吊销其已签发文件 URL
（``00`` §7.4：撤销后立刻失效）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection

from app.core import mongo
from app.core.clock import utcnow
from app.core.errors import Forbidden, NotFound
from app.identity.context import RequestContext

PARTICIPANTS_COLLECTION = "session_participants"
CONVERSATIONS_COLLECTION = "conversations"

Role = Literal["owner", "editor", "viewer"]


@dataclass(frozen=True, slots=True)
class Participant:
    """参与者视图。"""

    conversation_id: str
    user_id: str
    role: str
    joined_at: Any


class ParticipantsRepository:
    """参与者关系数据访问。"""

    def __init__(
        self,
        *,
        parts_collection: AsyncIOMotorCollection | None = None,
        conv_collection: AsyncIOMotorCollection | None = None,
    ) -> None:
        self._parts = parts_collection or mongo.get_collection(PARTICIPANTS_COLLECTION)
        self._conv = conv_collection or mongo.get_collection(CONVERSATIONS_COLLECTION)

    async def _assert_owner(self, ctx: RequestContext, conversation_id: str) -> None:
        """确认当前用户是该会话的 owner；否则 Forbidden。"""
        doc = await self._parts.find_one(
            {
                "conversation_id": conversation_id,
                "org_unit_id": ctx.org_unit_id,
                "user_id": _to_objid(ctx.user_id),
                "role": "owner",
                "removed_at": None,
            }
        )
        if doc is None:
            raise Forbidden("仅会话 owner 可管理参与者")

    async def add(
        self,
        ctx: RequestContext,
        conversation_id: str,
        *,
        user_id: str,
        role: Role,
    ) -> Participant:
        """新增参与者（仅 owner 可调用）。"""
        await self._assert_owner(ctx, conversation_id)
        now = utcnow()
        await self._parts.update_one(
            {
                "conversation_id": conversation_id,
                "org_unit_id": ctx.org_unit_id,
                "user_id": _to_objid(user_id),
            },
            {
                "$set": {
                    "role": role,
                    "removed_at": None,
                    "joined_at": now,
                }
            },
            upsert=True,
        )
        return Participant(
            conversation_id=conversation_id, user_id=user_id, role=role, joined_at=now
        )

    async def remove(self, ctx: RequestContext, conversation_id: str, *, user_id: str) -> None:
        """移除参与者（设 removed_at），并吊销其文件 URL（``00`` §7.4）。"""
        await self._assert_owner(ctx, conversation_id)
        await self._parts.update_one(
            {
                "conversation_id": conversation_id,
                "org_unit_id": ctx.org_unit_id,
                "user_id": _to_objid(user_id),
            },
            {"$set": {"removed_at": utcnow()}},
        )
        # 吊销该用户在该会话下的签名文件（配合 share 模块）
        from app.collaboration import share

        await share.revoke_files_for_participant(ctx, conversation_id, user_id)

    async def role_of(self, ctx: RequestContext, conversation_id: str) -> Role:
        """返回当前用户在会话中的角色；非参与者 Forbidden。"""
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
        return doc.get("role", "viewer")

    async def list(self, ctx: RequestContext, conversation_id: str) -> list[Participant]:
        """列出会话参与者（当前用户须为参与者）。"""
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
        cursor = self._parts.find(
            {"conversation_id": conversation_id, "org_unit_id": ctx.org_unit_id, "removed_at": None}
        )
        rows = await cursor.to_list(length=None)
        return [
            Participant(
                conversation_id=conversation_id,
                user_id=str(r.get("user_id", "")),
                role=str(r.get("role", "viewer")),
                joined_at=r.get("joined_at"),
            )
            for r in rows
        ]


def _to_objid(value: str) -> Any:
    try:
        return ObjectId(value)
    except Exception:
        return value
