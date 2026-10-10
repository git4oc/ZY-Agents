"""资源反馈端点（``api/endpoints/resource_feedback.py``）：评论 + 点赞 + 通知。

前端 ``apps/web/src/api/resourceFeedback.ts``（SkillFeedbackPanel.vue，知识页/技能页
共用）所需端点。**登录用户即可用**（无 admin 守卫）；所有查询带 ``org_unit_id``
隔离。资源类型通用（``personal_knowledge`` / ``skill`` ...），契约对齐前端
``FeedbackComment`` / ``FeedbackThread`` 形状。

- ``GET    /resource-feedback/{type}/{id}``：评论线程（cursor 分页）
- ``POST   /resource-feedback/{type}/{id}/comments``：加评论（可带 parentId 回复）
- ``DELETE /resource-feedback/comments/{id}``：删评论（仅作者）
- ``POST   /resource-feedback/{type}/{id}/like``：资源点赞 toggle
- ``POST   /resource-feedback/comments/{id}/like``：评论点赞 toggle
- ``GET    /resource-feedback-notifications``：我的通知（他人对我资源的评论/点赞）

集合：``resource_feedback``（评论）/ ``resource_feedback_likes``（资源点赞）/
``resource_feedback_notifications``（通知）。评论用 ObjectId 游标分页（单调递增）。
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import mongo
from app.core.clock import utcnow
from app.core.errors import Forbidden, NotFound
from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/resource-feedback", tags=["resource-feedback"])
notifications_router = APIRouter(prefix="/resource-feedback-notifications", tags=["resource-feedback"])

_COMMENTS = "resource_feedback"
_RES_LIKES = "resource_feedback_likes"
_NOTIFICATIONS = "resource_feedback_notifications"

#: 资源类型 → 资源所属集合（通知需要找资源 owner 与名称）
_RESOURCE_COLLECTIONS: dict[str, tuple[str, str, str]] = {
    # type: (collection, owner_field, name_field)
    "personal_knowledge": ("knowledge_documents", "owner_user_id", "name"),
}


def _oid(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except Exception:
        return None


async def _display_name(db: Any, user_id: str) -> str:
    if not user_id:
        return ""
    row = await db.users.find_one({"_id": _oid(user_id) or user_id}, {"display_name": 1, "username": 1})
    if row is None:
        return ""
    return str(row.get("display_name") or row.get("username") or "")


async def _resource_owner(db: Any, resource_type: str, resource_id: str) -> str:
    """资源 owner（通知收件人）；未知类型返回空串（无通知）。"""
    meta = _RESOURCE_COLLECTIONS.get(resource_type)
    if meta is None:
        return ""
    coll, owner_field, _ = meta
    row = await db[coll].find_one({"_id": resource_id}, {owner_field: 1, "created_by": 1})
    if row is None:
        return ""
    return str(row.get(owner_field) or row.get("created_by") or "")


async def _resource_name(db: Any, resource_type: str, resource_id: str) -> str:
    meta = _RESOURCE_COLLECTIONS.get(resource_type)
    if meta is None:
        return ""
    coll, _, name_field = meta
    row = await db[coll].find_one({"_id": resource_id}, {name_field: 1})
    if row is None:
        return ""
    return str(row.get(name_field) or "")


def _comment_body(row: dict[str, Any], viewer_id: str) -> dict[str, Any]:
    liked_by = list(row.get("liked_by") or [])
    return {
        "id": str(row["_id"]),
        "content": str(row.get("content") or ""),
        "parentId": str(row.get("parent_id") or ""),
        "rootId": str(row.get("root_id") or ""),
        "replyTo": {
            "userId": str(row.get("reply_to_user_id") or ""),
            "displayName": str(row.get("reply_to_name") or ""),
        },
        "author": {
            "userId": str(row.get("user_id") or ""),
            "displayName": str(row.get("display_name") or ""),
        },
        "likes": len(liked_by),
        "likedByMe": viewer_id in liked_by,
        "mine": str(row.get("user_id") or "") == viewer_id,
        "createdAt": (row.get("created_at") or utcnow()).isoformat(),
    }


async def _resource_likes(db: Any, ctx: RequestContext, resource_type: str, resource_id: str) -> dict[str, Any]:
    row = await db[_RES_LIKES].find_one(
        {
            "org_unit_id": ctx.org_unit_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
        {"user_ids": 1},
    )
    user_ids = list(row.get("user_ids") or []) if row else []
    return {"likes": len(user_ids), "likedByMe": ctx.user_id in user_ids}


async def _notify(
    db: Any,
    ctx: RequestContext,
    resource_type: str,
    resource_id: str,
    target_user_id: str,
    actor_name: str,
) -> None:
    """给资源 owner 发通知（自己操作自己资源不发）。"""
    if not target_user_id or target_user_id == ctx.user_id:
        return
    await db[_NOTIFICATIONS].insert_one(
        {
            "org_unit_id": ctx.org_unit_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "name": await _resource_name(db, resource_type, resource_id),
            "actor_user_id": ctx.user_id,
            "actor_name": actor_name,
            "target_user_id": target_user_id,
            "read": False,
            "created_at": utcnow(),
        }
    )


# ---------------------------------------------------------------------------
# 评论线程 / 点赞
# ---------------------------------------------------------------------------


def _db() -> Any:
    """数据库句柄（沿用 mongo 单例；集合延迟解析，避免启动顺序耦合）。"""
    return mongo.get_collection(_COMMENTS).database


@router.get("/{resource_type}/{resource_id}")
async def get_feedback(
    resource_type: str,
    resource_id: str,
    cursor: str = "",
    limit: int = 30,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """评论线程（按 _id 升序，cursor 为最后一条评论 id）。"""
    database = _db()
    query: dict[str, Any] = {
        "org_unit_id": ctx.org_unit_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
    }
    cursor_oid = _oid(cursor)
    if cursor_oid is not None:
        query["_id"] = {"$gt": cursor_oid}
    fetch = max(1, min(limit, 100))
    rows = await (
        database[_COMMENTS].find(query).sort("_id", 1).limit(fetch + 1).to_list(length=fetch + 1)
    )
    has_more = len(rows) > fetch
    rows = rows[:fetch]
    items = [_comment_body(r, ctx.user_id) for r in rows]
    comment_count = await database[_COMMENTS].count_documents(
        {
            "org_unit_id": ctx.org_unit_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
        }
    )
    next_cursor = str(rows[-1]["_id"]) if rows and has_more else ""
    return {
        "items": items,
        "commentCount": comment_count,
        **await _resource_likes(database, ctx, resource_type, resource_id),
        "hasMore": has_more,
        "nextCursor": next_cursor,
        "focus": None,
    }


class CommentBody(BaseModel):
    content: str
    parentId: str = ""


@router.post("/{resource_type}/{resource_id}/comments")
async def add_comment(
    resource_type: str,
    resource_id: str,
    body: CommentBody,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """加评论（带 parentId 即回复：root/replyTo 从父评论推导）。"""
    content = body.content.strip()
    if not content:
        raise Forbidden("评论内容不能为空")
    database = mongo.get_collection(_COMMENTS).database
    parent: dict[str, Any] | None = None
    if body.parentId:
        parent = await database[_COMMENTS].find_one(
            {
                "_id": _oid(body.parentId),
                "org_unit_id": ctx.org_unit_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
            }
        )
        if parent is None:
            raise NotFound("父评论不存在")

    display_name = await _display_name(database, ctx.user_id)
    root_id = ""
    reply_to_user = ""
    reply_to_name = ""
    if parent is not None:
        root_id = str(parent.get("root_id") or parent["_id"])
        reply_to_user = str(parent.get("user_id") or "")
        reply_to_name = str(parent.get("display_name") or "")
    doc = {
        "org_unit_id": ctx.org_unit_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "user_id": ctx.user_id,
        "display_name": display_name,
        "content": content,
        "parent_id": body.parentId,
        "root_id": root_id,
        "reply_to_user_id": reply_to_user,
        "reply_to_name": reply_to_name,
        "liked_by": [],
        "created_at": utcnow(),
    }
    result = await database[_COMMENTS].insert_one(doc)
    row = await database[_COMMENTS].find_one({"_id": result.inserted_id})

    # 通知：资源 owner + 被回复人（不通知自己）
    actor_name = display_name
    owner = await _resource_owner(database, resource_type, resource_id)
    await _notify(database, ctx, resource_type, resource_id, owner, actor_name)
    if reply_to_user and reply_to_user != owner:
        await _notify(database, ctx, resource_type, resource_id, reply_to_user, actor_name)
    return _comment_body(row, ctx.user_id)


@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """删评论（仅作者；连同其子评论）。"""
    database = mongo.get_collection(_COMMENTS).database
    row = await database[_COMMENTS].find_one(
        {"_id": _oid(comment_id), "org_unit_id": ctx.org_unit_id}
    )
    if row is None:
        raise NotFound("评论不存在")
    if str(row.get("user_id") or "") != ctx.user_id:
        raise Forbidden("仅作者可删除评论")
    await database[_COMMENTS].delete_one({"_id": row["_id"]})
    await database[_COMMENTS].delete_many({"parent_id": comment_id})
    return {"ok": True}


@router.post("/{resource_type}/{resource_id}/like")
async def toggle_like(
    resource_type: str,
    resource_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """资源点赞 toggle（liked → 取消；未 liked → 点赞 + 通知 owner）。"""
    database = mongo.get_collection(_COMMENTS).database
    current = await _resource_likes(database, ctx, resource_type, resource_id)
    if current["likedByMe"]:
        await database[_RES_LIKES].update_one(
            {
                "org_unit_id": ctx.org_unit_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
            {"$pull": {"user_ids": ctx.user_id}},
        )
    else:
        await database[_RES_LIKES].update_one(
            {
                "org_unit_id": ctx.org_unit_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
            {"$addToSet": {"user_ids": ctx.user_id}, "$setOnInsert": {"created_at": utcnow()}},
            upsert=True,
        )
        owner = await _resource_owner(database, resource_type, resource_id)
        await _notify(
            database, ctx, resource_type, resource_id, owner,
            await _display_name(database, ctx.user_id),
        )
    return await _resource_likes(database, ctx, resource_type, resource_id)


@router.post("/comments/{comment_id}/like")
async def toggle_comment_like(
    comment_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """评论点赞 toggle。"""
    database = mongo.get_collection(_COMMENTS).database
    row = await database[_COMMENTS].find_one(
        {"_id": _oid(comment_id), "org_unit_id": ctx.org_unit_id}
    )
    if row is None:
        raise NotFound("评论不存在")
    liked_by = list(row.get("liked_by") or [])
    if ctx.user_id in liked_by:
        await database[_COMMENTS].update_one(
            {"_id": row["_id"]}, {"$pull": {"liked_by": ctx.user_id}}
        )
        liked_by.remove(ctx.user_id)
    else:
        await database[_COMMENTS].update_one(
            {"_id": row["_id"]}, {"$addToSet": {"liked_by": ctx.user_id}}
        )
        liked_by.append(ctx.user_id)
    return {"likes": len(liked_by), "likedByMe": ctx.user_id in liked_by}


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------


@notifications_router.get("")
async def my_notifications(
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """我的通知（最新 50 条；unreadCount 为未读数）。"""
    database = mongo.get_collection(_COMMENTS).database
    query = {"org_unit_id": ctx.org_unit_id, "target_user_id": ctx.user_id}
    rows = (
        await database[_NOTIFICATIONS]
        .find(query)
        .sort("created_at", -1)
        .limit(50)
        .to_list(length=50)
    )
    unread = await database[_NOTIFICATIONS].count_documents({**query, "read": False})
    return {
        "items": [
            {
                "id": str(r["_id"]),
                "resourceType": str(r.get("resource_type") or ""),
                "resourceId": str(r.get("resource_id") or ""),
                "name": str(r.get("name") or ""),
                "actor": {"displayName": str(r.get("actor_name") or "")},
                "createdAt": (r.get("created_at") or utcnow()).isoformat(),
            }
            for r in rows
        ],
        "unreadCount": unread,
    }
