"""技能分享端点（``api/endpoints/skill_sharing.py``）：分享链接 + 直分享 + 收件箱。

前端 ``apps/web/src/api/skillSharing.ts``（我的技能页分享流）所需端点。
**登录用户即可用**；所有查询带 ``org_unit_id`` 隔离。分享是**快照语义**：
创建时冻结技能的 key/名称/描述/版本/指令正文，收件人接受时按快照落一份
自己的 skills（key 冲突即 duplicate），源技能后续修改不影响已创建的分享。

- ``POST   /skills/{id}/shares``：创建分享链接（快照 + token）
- ``DELETE /skills/{id}/shares/{shareId}``：吊销
- ``POST   /skills/{id}/share-with-users``：直分享给指定成员（建 deliveries）
- ``GET    /skill-shares/{token}``：按 token 预览（installed/conflict 按查看者算）
- ``POST   /skill-shares/{token}/install``：按 token 安装
- ``GET    /skill-share-directory``：可分享成员搜索（本 org）
- ``GET    /skill-share-inbox`` / ``/count``：收件箱列表 / 徽标计数
- ``POST   /skill-share-inbox/{deliveryId}/accept`` / ``/decline``：接受 / 拒绝
- ``GET    /skill-updates``：更新通知（阶段 3+，恒空态）

集合：``skill_shares``（分享快照）/ ``skill_share_deliveries``（直分享投递）。
"""

from __future__ import annotations

import uuid
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import mongo
from app.core.clock import utcnow
from app.core.errors import Forbidden, NotFound
from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(tags=["skill-sharing"])

_SHARES = "skill_shares"
_DELIVERIES = "skill_share_deliveries"


def _db() -> Any:
    return mongo.get_collection(_SHARES).database


def _oid(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except Exception:
        return None


def _skill_oid(value: str) -> Any:
    """skills 集合 _id 有 ObjectId 与 str 两种形态（同 admin.py）。"""
    return _oid(value) or value


def _share_preview(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "shareId": str(row.get("share_id") or ""),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "type": str(row.get("type") or "ordinary"),
        "version": str(row.get("version") or "0.0.0"),
        "fileCount": int(row.get("file_count") or 0),
        "childCount": int(row.get("child_count") or 0),
        "expiresAt": (row.get("expires_at") or utcnow()).isoformat(),
    }


def _member_body(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "userId": str(row["_id"]),
        "displayName": str(row.get("display_name") or ""),
        "username": str(row.get("username") or ""),
        "email": str(row.get("email") or ""),
    }


async def _snapshot_skill(database: Any, skill: dict[str, Any]) -> dict[str, Any]:
    """skills 文档 → 分享快照（冻结指令正文：取最新 version 的 instructions）。"""
    version_row = await database["skill_versions"].find_one(
        {"skill_id": skill["_id"]}, {"definition": 1}, sort=[("created_at", -1)]
    )
    definition = (version_row or {}).get("definition") or {}
    config = skill.get("config") or {}
    return {
        "skill_key": str(skill.get("key") or ""),
        "name": str(skill.get("name") or ""),
        "description": str(skill.get("description") or ""),
        "kind": str(skill.get("kind") or "skill"),
        "type": str(skill.get("type") or "ordinary"),
        "scenario": str(skill.get("scenario") or ""),
        "version": str(skill.get("current_version") or "0.0.0"),
        "file_count": int(config.get("file_count") or 0),
        "digest": str(config.get("digest") or ""),
        "instructions": str(definition.get("instructions") or ""),
    }


async def _install_shared_skill(
    database: Any, ctx: RequestContext, share: dict[str, Any]
) -> dict[str, Any]:
    """按分享快照给接收者落一份 skills + skill_versions（key 冲突 → duplicate）。"""
    skill_key = str(share.get("skill_key") or "")
    existing = await database["skills"].find_one(
        {"key": skill_key, "org_unit_id": ctx.org_unit_id}
    )
    if existing is not None:
        return {
            "id": str(existing["_id"]),
            "name": str(existing.get("name") or ""),
            "type": str(existing.get("type") or "ordinary"),
            "version": str(existing.get("current_version") or ""),
            "duplicate": True,
            "enabled": str(existing.get("status") or "active") == "active",
        }
    now = utcnow()
    result = await database["skills"].insert_one(
        {
            "key": skill_key,
            "name": str(share.get("name") or skill_key),
            "description": str(share.get("description") or ""),
            "kind": str(share.get("kind") or "skill"),
            "type": str(share.get("type") or "ordinary"),
            "scenario": str(share.get("scenario") or ""),
            "config": {
                "source": "share",
                "version": str(share.get("version") or ""),
                "file_count": int(share.get("file_count") or 0),
                "digest": str(share.get("digest") or ""),
            },
            "requires_tools": [],
            "current_version": str(share.get("version") or "0.0.0"),
            "status": "active",
            "org_unit_id": ctx.org_unit_id,
            "created_at": now,
            "updated_at": now,
        }
    )
    await database["skill_versions"].insert_one(
        {
            "skill_id": result.inserted_id,
            "version": str(share.get("version") or "0.0.0"),
            "definition": {
                "instructions": str(share.get("instructions") or ""),
                "source": "share",
                "files": [],
                "sha256": str(share.get("digest") or ""),
            },
            "created_at": now,
        }
    )
    from app.kernel.profile.store import invalidate

    await invalidate(ctx.org_unit_id)
    return {
        "id": str(result.inserted_id),
        "name": str(share.get("name") or skill_key),
        "type": str(share.get("type") or "ordinary"),
        "version": str(share.get("version") or ""),
        "duplicate": False,
        "enabled": True,
    }


async def _load_share_for_viewer(
    database: Any, ctx: RequestContext, share: dict[str, Any]
) -> dict[str, Any]:
    preview = _share_preview(share)
    existing = await database["skills"].find_one(
        {"key": str(share.get("skill_key") or ""), "org_unit_id": ctx.org_unit_id},
        {"current_version": 1},
    )
    preview["alreadyInstalled"] = bool(
        existing and str(existing.get("current_version")) == preview["version"]
    )
    preview["hasConflict"] = existing is not None
    preview["existingSkillId"] = str(existing["_id"]) if existing else ""
    return preview


# ---------------------------------------------------------------------------
# 创建分享 / 吊销 / 直分享
# ---------------------------------------------------------------------------


class ShareCreateBody(BaseModel):
    expiresInDays: int = 7


@router.post("/skills/{skill_id}/shares")
async def create_share(
    skill_id: str,
    body: ShareCreateBody,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """创建分享链接（快照语义：冻结创建时刻的技能内容）。"""
    database = _db()
    skill = await database["skills"].find_one(
        {"_id": _skill_oid(skill_id), "org_unit_id": ctx.org_unit_id}
    )
    if skill is None:
        raise NotFound("技能不存在")
    snapshot = await _snapshot_skill(database, skill)
    now = utcnow()
    doc = {
        "share_id": uuid.uuid4().hex,
        "token": uuid.uuid4().hex,
        "org_unit_id": ctx.org_unit_id,
        "owner_user_id": ctx.user_id,
        "skill_id": skill["_id"],
        **snapshot,
        "child_count": 0,
        "expires_at": now + __import__("datetime").timedelta(days=max(1, body.expiresInDays)),
        "revoked": False,
        "created_at": now,
    }
    await database[_SHARES].insert_one(doc)
    return {
        **_share_preview(doc),
        "token": str(doc["token"]),
    }


@router.delete("/skills/{skill_id}/shares/{share_id}")
async def revoke_share(
    skill_id: str,
    share_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """吊销分享（源 owner 或 admin；吊销后 install 拒绝）。"""
    database = _db()
    share = await database[_SHARES].find_one({"share_id": share_id, "org_unit_id": ctx.org_unit_id})
    if share is None:
        raise NotFound("分享不存在")
    if str(share.get("owner_user_id") or "") != ctx.user_id:
        raise Forbidden("仅分享创建者可吊销")
    await database[_SHARES].update_one(
        {"_id": share["_id"]}, {"$set": {"revoked": True, "updated_at": utcnow()}}
    )
    return {"ok": True}


class ShareWithUsersBody(BaseModel):
    recipientUserIds: list[str] = []


@router.post("/skills/{skill_id}/share-with-users")
async def share_with_users(
    skill_id: str,
    body: ShareWithUsersBody,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """直分享：创建快照 + 每个接收者一条 delivery（pending）。"""
    database = _db()
    skill = await database["skills"].find_one(
        {"_id": _skill_oid(skill_id), "org_unit_id": ctx.org_unit_id}
    )
    if skill is None:
        raise NotFound("技能不存在")
    snapshot = await _snapshot_skill(database, skill)
    now = utcnow()
    share_doc = {
        "share_id": uuid.uuid4().hex,
        "token": "",
        "org_unit_id": ctx.org_unit_id,
        "owner_user_id": ctx.user_id,
        "skill_id": skill["_id"],
        **snapshot,
        "child_count": 0,
        "expires_at": now + __import__("datetime").timedelta(days=7),
        "revoked": False,
        "created_at": now,
    }
    await database[_SHARES].insert_one(share_doc)
    recipients: list[dict[str, Any]] = []
    for recipient_id in dict.fromkeys(body.recipientUserIds):
        if not recipient_id or recipient_id == ctx.user_id:
            continue
        user = await database["users"].find_one(
            {"_id": _oid(recipient_id) or recipient_id, "org_unit_id": ctx.org_unit_id}
        )
        if user is None:
            continue
        await database[_DELIVERIES].insert_one(
            {
                "org_unit_id": ctx.org_unit_id,
                "share_id": share_doc["share_id"],
                "sender_user_id": ctx.user_id,
                "recipient_user_id": str(user["_id"]),
                "status": "pending",
                "created_at": now,
                "decided_at": None,
            }
        )
        recipients.append(_member_body(user))
    return {"shareId": share_doc["share_id"], "recipientCount": len(recipients), "recipients": recipients}


# ---------------------------------------------------------------------------
# token 预览 / 安装
# ---------------------------------------------------------------------------


@router.get("/skill-shares/{token}")
async def preview_by_token(
    token: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """按 token 预览（installed/conflict 按查看者算）。"""
    database = _db()
    share = await database[_SHARES].find_one({"token": token, "org_unit_id": ctx.org_unit_id})
    if share is None or share.get("revoked"):
        raise NotFound("分享不存在或已吊销")
    return await _load_share_for_viewer(database, ctx, share)


class InstallBody(BaseModel):
    replaceExisting: bool = False


@router.post("/skill-shares/{token}/install")
async def install_by_token(
    token: str,
    body: InstallBody,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """按 token 安装（key 冲突需 replaceExisting 确认）。"""
    database = _db()
    share = await database[_SHARES].find_one({"token": token, "org_unit_id": ctx.org_unit_id})
    if share is None or share.get("revoked"):
        raise NotFound("分享不存在或已吊销")
    # Mongo 返回的 datetime 是 naive（pymongo 默认去 tzinfo）——补 UTC 后才能比较
    expires_at = share.get("expires_at")
    if expires_at:
        from datetime import timezone as _tz

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=_tz.utc)
        if expires_at < utcnow():
            raise Forbidden("分享已过期")
    existing = await database["skills"].find_one(
        {"key": str(share.get("skill_key") or ""), "org_unit_id": ctx.org_unit_id}
    )
    if existing is not None:
        # org 已有同 key 技能：源技能本体（同 org 分享）→ duplicate；
        # 跨 org 场景下同版 duplicate / 异版需 replaceExisting 确认覆盖
        if existing["_id"] == share.get("skill_id") or str(existing.get("current_version")) == str(share.get("version") or ""):
            return await _install_shared_skill(database, ctx, share)
        if not body.replaceExisting:
            raise Forbidden("已存在同名技能，需确认后覆盖")
    return await _install_shared_skill(database, ctx, share)


# ---------------------------------------------------------------------------
# 成员目录
# ---------------------------------------------------------------------------


@router.get("/skill-share-directory")
async def search_members(
    keyword: str = "",
    cursor: str = "",
    limit: int = 20,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """可分享成员搜索（本 org，排除自己；username/display_name/email 模糊）。"""
    database = _db()
    query: dict[str, Any] = {"org_unit_id": ctx.org_unit_id, "status": "active"}
    if keyword.strip():
        import re as _re

        pattern = {"$regex": _re.escape(keyword.strip()), "$options": "i"}
        query["$or"] = [
            {"username": pattern},
            {"display_name": pattern},
            {"email": pattern},
        ]
    cursor_oid = _oid(cursor)
    if cursor_oid is not None:
        query["_id"] = {"$gt": cursor_oid}
    fetch = max(1, min(limit, 100))
    rows = await (
        database["users"].find(query, {"display_name": 1, "username": 1, "email": 1})
        .sort("_id", 1)
        .limit(fetch + 1)
        .to_list(length=fetch + 1)
    )
    has_more = len(rows) > fetch
    rows = rows[:fetch]
    return {
        "items": [_member_body(r) for r in rows],
        "nextCursor": str(rows[-1]["_id"]) if rows and has_more else "",
        "hasMore": has_more,
    }


# ---------------------------------------------------------------------------
# 收件箱
# ---------------------------------------------------------------------------


@router.get("/skill-share-inbox")
async def inbox(
    cursor: str = "",
    limit: int = 20,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """收件箱（我的投递，最新在前；pendingCount 为待处理数）。"""
    database = _db()
    query: dict[str, Any] = {"org_unit_id": ctx.org_unit_id, "recipient_user_id": ctx.user_id}
    cursor_oid = _oid(cursor)
    if cursor_oid is not None:
        query["_id"] = {"$gt": cursor_oid}
    fetch = max(1, min(limit, 100))
    delivery_rows = await (
        database[_DELIVERIES].find(query).sort("_id", 1).limit(fetch + 1).to_list(length=fetch + 1)
    )
    has_more = len(delivery_rows) > fetch
    delivery_rows = delivery_rows[:fetch]

    items: list[dict[str, Any]] = []
    for d in delivery_rows:
        share = await database[_SHARES].find_one({"share_id": d.get("share_id")})
        if share is None:
            continue
        sender = await database["users"].find_one(
            {"_id": _oid(d.get("sender_user_id")) or d.get("sender_user_id")},
            {"display_name": 1, "username": 1, "email": 1},
        )
        items.append(
            {
                **_share_preview(share),
                "deliveryId": str(d["_id"]),
                "sender": _member_body(sender or {"_id": d.get("sender_user_id")}),
                "sharedAt": (d.get("created_at") or utcnow()).isoformat(),
            }
        )
    pending = await database[_DELIVERIES].count_documents(
        {**{k: v for k, v in query.items() if k != "_id"}, "status": "pending"}
    )
    return {
        "items": items,
        "nextCursor": str(delivery_rows[-1]["_id"]) if delivery_rows and has_more else "",
        "hasMore": has_more,
        "pendingCount": pending,
    }


@router.get("/skill-share-inbox/count")
async def inbox_counts(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """徽标计数：pending / 我创建的活跃分享 / 更新（阶段 3+）/ 反馈未读。"""
    database = _db()
    pending = await database[_DELIVERIES].count_documents(
        {"org_unit_id": ctx.org_unit_id, "recipient_user_id": ctx.user_id, "status": "pending"}
    )
    shared = await database[_SHARES].count_documents(
        {"org_unit_id": ctx.org_unit_id, "owner_user_id": ctx.user_id, "revoked": False}
    )
    feedback = await database["resource_feedback_notifications"].count_documents(
        {"org_unit_id": ctx.org_unit_id, "target_user_id": ctx.user_id, "read": False}
    )
    return {"pendingCount": pending, "sharedCount": shared, "updateCount": 0, "feedbackCount": feedback}


class AcceptBody(BaseModel):
    replaceExisting: bool = False


@router.post("/skill-share-inbox/{delivery_id}/accept")
async def accept_share(
    delivery_id: str,
    body: AcceptBody,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """接受分享：delivery 置 accepted + 按快照落技能。

    skills 集合是 org 共享的（无 user 维度）：源技能本就在同 org 下，同 key
    已存在即**就是这条分享的源技能**（或此前已装过）→ 视为 duplicate，不再落新份。
    """
    database = _db()
    delivery = await database[_DELIVERIES].find_one(
        {"_id": _oid(delivery_id), "org_unit_id": ctx.org_unit_id, "recipient_user_id": ctx.user_id}
    )
    if delivery is None:
        raise NotFound("投递不存在")
    share = await database[_SHARES].find_one({"share_id": delivery.get("share_id")})
    if share is None or share.get("revoked"):
        raise NotFound("分享不存在或已吊销")
    existing = await database["skills"].find_one(
        {"key": str(share.get("skill_key") or ""), "org_unit_id": ctx.org_unit_id}
    )
    if existing is not None:
        result = {
            "id": str(existing["_id"]),
            "name": str(existing.get("name") or ""),
            "type": str(existing.get("type") or "ordinary"),
            "version": str(existing.get("current_version") or ""),
            "duplicate": True,
            "enabled": str(existing.get("status") or "active") == "active",
        }
    else:
        result = await _install_shared_skill(database, ctx, share)
    if delivery.get("status") != "accepted":
        await database[_DELIVERIES].update_one(
            {"_id": delivery["_id"]},
            {"$set": {"status": "accepted", "decided_at": utcnow()}},
        )
    return result


@router.post("/skill-share-inbox/{delivery_id}/decline")
async def decline_share(
    delivery_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """拒绝分享。"""
    database = _db()
    delivery = await database[_DELIVERIES].find_one(
        {"_id": _oid(delivery_id), "org_unit_id": ctx.org_unit_id, "recipient_user_id": ctx.user_id}
    )
    if delivery is None:
        raise NotFound("投递不存在")
    await database[_DELIVERIES].update_one(
        {"_id": delivery["_id"]}, {"$set": {"status": "declined", "decided_at": utcnow()}}
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# 更新通知（阶段 3+：恒空态）
# ---------------------------------------------------------------------------


@router.get("/skill-updates")
async def skill_updates(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """已装技能的版本更新通知（阶段 3+；当前恒空态）。"""
    return {"items": [], "pendingCount": 0}
