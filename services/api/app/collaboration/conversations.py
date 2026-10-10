"""会话仓储（``collaboration/conversations.py``，``01`` §4.2 / ``02`` §2.4 集合 ``conversations``）。

所有方法第一个参数为 ``ctx: RequestContext``。**非参与者访问 get 返回 Forbidden 而非 404**
（``01`` §4.2，避免探测存在性）。
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

COLLECTION = "conversations"
PARTICIPANTS_COLLECTION = "session_participants"

Visibility = Literal["private", "shared", "org"]
Role = Literal["owner", "editor", "viewer"]


@dataclass(frozen=True, slots=True)
class Conversation:
    """会话聚合（对外视图）。"""

    id: str
    org_unit_id: str
    owner_user_id: str
    title: str
    visibility: str
    device_id: str | None
    profile_version: str | None
    runtime_id: str | None
    session_id: str | None
    active_run: dict[str, Any] | None
    created_at: Any
    last_active_at: Any


@dataclass(frozen=True, slots=True)
class Page:
    """分页容器。"""

    items: list[Any]
    next_cursor: str | None


class ConversationsRepository:
    """会话数据访问（含参与者关系协同）。"""

    def __init__(
        self,
        *,
        collection: AsyncIOMotorCollection | None = None,
        participants_collection: AsyncIOMotorCollection | None = None,
    ) -> None:
        self._coll = collection or mongo.get_collection(COLLECTION)
        self._parts = participants_collection or mongo.get_collection(PARTICIPANTS_COLLECTION)

    # ---------- 参与者相关 ----------

    async def _is_participant(self, ctx: RequestContext, conversation_id: str) -> bool:
        from bson import ObjectId

        doc = await self._parts.find_one(
            {
                "conversation_id": conversation_id,
                "user_id": ObjectId(ctx.user_id) if ObjectId.is_valid(ctx.user_id) else ctx.user_id,
                "removed_at": None,
            }
        )
        return doc is not None

    async def get(self, ctx: RequestContext, conversation_id: str) -> Conversation:
        """查询会话；**非参与者 → Forbidden**（不返回 404，避免探测存在性）。"""
        if not await self._is_participant(ctx, conversation_id):
            raise Forbidden(
                "您不是该会话的参与者",
                details={"conversation_id": conversation_id},
            )
        doc = await self._coll.find_one(
            {"_id": _to_objid(conversation_id), "org_unit_id": ctx.org_unit_id}
        )
        if doc is None:
            raise NotFound("会话不存在")
        return _row_to_conversation(doc)

    async def create(
        self,
        ctx: RequestContext,
        *,
        title: str,
        visibility: Visibility,
        device_id: str | None = None,
    ) -> Conversation:
        """创建会话并自动把创建者登记为 owner 参与者。"""
        now = utcnow()
        doc: dict[str, Any] = {
            "org_unit_id": ctx.org_unit_id,
            "owner_user_id": _to_objid(ctx.user_id),
            "title": title,
            "visibility": visibility,
            "device_id": device_id,
            "profile_version": None,
            "runtime_id": None,
            # session_id 省略不写：conversations_session 是 sparse 唯一索引，
            # 只豁免"字段缺失"；显式 null 会入索引，第二个新会话即撞 E11000
            "active_run": None,
            "created_at": now,
            "last_active_at": now,
        }
        result = await self._coll.insert_one(doc)
        cid = str(result.inserted_id)
        # 登记 owner 参与者
        await self._parts.insert_one(
            {
                "org_unit_id": ctx.org_unit_id,
                "conversation_id": cid,
                "user_id": _to_objid(ctx.user_id),
                "role": "owner",
                "joined_at": now,
                "removed_at": None,
                "last_read_seq": 0,
            }
        )
        return Conversation(
            id=cid,
            org_unit_id=ctx.org_unit_id,
            owner_user_id=ctx.user_id,
            title=title,
            visibility=visibility,
            device_id=device_id,
            profile_version=None,
            runtime_id=None,
            session_id=None,
            active_run=None,
            created_at=now,
            last_active_at=now,
        )

    async def list_for_user(
        self,
        ctx: RequestContext,
        *,
        cursor: str | None = None,
        limit: int = 50,
        scope: str = "participating",
    ) -> Page[Conversation]:
        """列出当前用户参与的会话（分页）。scope ∈ mine|participating。"""
        # 先取用户参与的会话 id
        part_cursor = self._parts.find(
            {
                "org_unit_id": ctx.org_unit_id,
                "user_id": _to_objid(ctx.user_id),
                "removed_at": None,
            }
        )
        part_rows = await part_cursor.to_list(length=None)
        conv_ids = [r["conversation_id"] for r in part_rows]

        query: dict[str, Any] = {
            "org_unit_id": ctx.org_unit_id,
            "_id": {"$in": [_to_objid(c) for c in conv_ids]},
        }
        if cursor:
            query["last_active_at"] = {"$lt": ObjectId(cursor).generation_time} if ObjectId.is_valid(cursor) else {"$lt": cursor}

        rows_cursor = self._coll.find(query).sort("last_active_at", -1).limit(limit + 1)
        rows = await rows_cursor.to_list(length=limit + 1)
        items = [_row_to_conversation(r) for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            next_cursor = str(rows[limit]["_id"])
        return Page(items=items, next_cursor=next_cursor)

    async def update_active_run(
        self,
        ctx: RequestContext,
        conversation_id: str,
        *,
        active_run: dict[str, Any] | None,
    ) -> None:
        """更新会话的 active_run 字段。"""
        await self._coll.update_one(
            {"_id": _to_objid(conversation_id), "org_unit_id": ctx.org_unit_id},
            {"$set": {"active_run": active_run, "last_active_at": utcnow()}},
        )

    async def set_kernel_ids(
        self,
        ctx: RequestContext,
        conversation_id: str,
        *,
        profile_version: str,
        runtime_id: str,
        session_id: str,
    ) -> None:
        """绑定内核侧标识（会话与发起者 Profile 绑定，参与者不改变它）。"""
        await self._coll.update_one(
            {"_id": _to_objid(conversation_id), "org_unit_id": ctx.org_unit_id},
            {
                "$set": {
                    "profile_version": profile_version,
                    "runtime_id": runtime_id,
                    "session_id": session_id,
                }
            },
        )


def _to_objid(value: str) -> Any:
    try:
        return ObjectId(value)
    except Exception:
        return value


def _row_to_conversation(doc: dict[str, Any]) -> Conversation:
    return Conversation(
        id=str(doc.get("_id")),
        org_unit_id=str(doc.get("org_unit_id", "")),
        owner_user_id=str(doc.get("owner_user_id", "")),
        title=str(doc.get("title", "")),
        visibility=str(doc.get("visibility", "private")),
        device_id=doc.get("device_id"),
        profile_version=doc.get("profile_version"),
        runtime_id=doc.get("runtime_id"),
        session_id=doc.get("session_id"),
        active_run=doc.get("active_run"),
        created_at=doc.get("created_at"),
        last_active_at=doc.get("last_active_at"),
    )
