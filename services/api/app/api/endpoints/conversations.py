"""会话与协同端点（``api/endpoints/conversations.py``，``02`` §4.3）。

路由：会话 CRUD、消息、参与者、审批列表、事件回放、SSE 流。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core import mongo
from app.core.errors import NotFound
from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    title: str
    visibility: str = "private"  # private | shared | org
    device_id: str | None = None


class PatchConversationRequest(BaseModel):
    title: str | None = None
    visibility: str | None = None
    device_id: str | None = None


class AddParticipantBody(BaseModel):
    """``POST /{conversation_id}/participants`` 请求体（``02`` §4.3）。

    ``user_id`` **必填且非空**：早前端点用 ``body: Any`` + ``isinstance`` 硬凑，
    HTTP 层直接 422（``loc=["query","body"]``），缺 user_id 时又抛 ``NotFound``(404)
    ——两种错误风格并存。改真模型后，**缺字段由 FastAPI 统一回 422**
    （不再有 404 那种"字段缺失"误报）。
    """

    user_id: str = Field(min_length=1)
    role: str = "viewer"  # viewer | editor


@router.get("")
async def list_conversations(
    ctx: RequestContext = Depends(get_ctx),
    cursor: str | None = None,
    limit: int = 50,
    scope: str = "participating",
) -> dict[str, Any]:
    """我参与的会话列表（分页）。"""
    from app.collaboration.conversations import ConversationsRepository

    repo = ConversationsRepository()
    page = await repo.list_for_user(ctx, cursor=cursor, limit=limit, scope=scope)
    return {
        "items": [_conv_payload(c) for c in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("")
async def create_conversation(
    body: CreateConversationRequest,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """创建会话。"""
    from app.collaboration.conversations import ConversationsRepository

    repo = ConversationsRepository()
    conv = await repo.create(
        ctx, title=body.title, visibility=body.visibility, device_id=body.device_id
    )
    return _conv_payload(conv)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """查询会话（非参与者 Forbidden）。"""
    from app.collaboration.conversations import ConversationsRepository

    repo = ConversationsRepository()
    conv = await repo.get(ctx, conversation_id)
    return _conv_payload(conv)


@router.patch("/{conversation_id}")
async def patch_conversation(
    conversation_id: str,
    body: PatchConversationRequest,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """修改会话（标题 / visibility / 绑定设备），仅 owner。"""
    from app.collaboration.conversations import ConversationsRepository
    from app.collaboration.participants import ParticipantsRepository
    from app.core.errors import Forbidden

    parts = ParticipantsRepository()
    role = await parts.role_of(ctx, conversation_id)
    if role != "owner":
        raise Forbidden("仅 owner 可修改会话")

    coll = mongo.get_collection("conversations")
    update: dict[str, Any] = {}
    if body.title is not None:
        update["title"] = body.title
    if body.visibility is not None:
        update["visibility"] = body.visibility
    if body.device_id is not None:
        update["device_id"] = body.device_id
    # ★ 空更新不再"静默跳过 + 仍返回 200"——apps/web 会调这个端点，空提交必须
    # 明确 422，否则前端以为改成功（与 admin 5 个 PATCH 同一类假成功）。
    if not update:
        from app.core.errors import DomainError

        raise DomainError(
            "未提供任何可更新字段（请求体为空或全部为 null）",
            code="empty_patch_body",
            status=422,
            details={"writable_fields": ["title", "visibility", "device_id"]},
        )
    await coll.update_one({"_id": _oid(conversation_id)}, {"$set": update})
    repo = ConversationsRepository()
    conv = await repo.get(ctx, conversation_id)
    return _conv_payload(conv)


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """归档会话（软删除），仅 owner。"""
    from app.collaboration.participants import ParticipantsRepository
    from app.core.errors import Forbidden

    parts = ParticipantsRepository()
    role = await parts.role_of(ctx, conversation_id)
    if role != "owner":
        raise Forbidden("仅 owner 可归档会话")
    coll = mongo.get_collection("conversations")
    await coll.update_one(
        {"_id": _oid(conversation_id), "org_unit_id": ctx.org_unit_id},
        {"$set": {"archived": True, "last_active_at": _utcnow()}},
    )
    return {"ok": True}


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
    before_seq: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """会话消息分页。"""
    from app.collaboration.messages import MessagesRepository

    repo = MessagesRepository()
    msgs = await repo.list(ctx, conversation_id, before_seq=before_seq, limit=limit)
    return {
        "items": [
            {
                "id": m.id,
                "seq": m.seq,
                "role": m.role,
                "content": m.content,
                "meta": m.meta,
                "created_at": m.created_at,
            }
            for m in msgs
        ]
    }


@router.get("/{conversation_id}/stream")
async def stream_events(
    request: Request,
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> Any:
    """SSE 事件流（支持 Last-Event-ID 续传）。"""
    from fastapi.responses import StreamingResponse

    from app.collaboration.stream import StreamSubscription

    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id and last_event_id.isdigit():
        after_cursor = int(last_event_id)
    else:
        # 无 Last-Event-ID：从当前尾部起订（live-only）。从 0 起会把全部历史
        # 事件重放给本轮气泡，且历史里的终态事件让前端读流提前收束。
        from app.collaboration.stream import latest_cursor

        after_cursor = await latest_cursor(ctx, conversation_id)
    sub = StreamSubscription(ctx, conversation_id, after_cursor=after_cursor)

    async def event_gen():
        import asyncio

        # 第一次读取（续传）
        events = await sub.read_events()
        for ev in events:
            yield _sse_event(ev)

        # 轮询推送新事件（#18：持久化优先，SSE 读侧投影）
        # 无事件时发 SSE 注释行保活；连续 120 次无事件（约 60s）则收束连接。
        idle = 0
        while idle < 120:
            await asyncio.sleep(0.5)
            try:
                events = await sub.read_events()
            except Exception:
                break
            if events:
                idle = 0
                for ev in events:
                    yield _sse_event(ev)
            else:
                idle += 1
                if idle % 30 == 0:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{conversation_id}/participants")
async def list_participants(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """列出参与者（附 display_name；role 投影为 owner/participant 两态）。"""
    from app.collaboration.participants import ParticipantsRepository

    parts = ParticipantsRepository()
    rows = await parts.list(ctx, conversation_id)
    # users 批量查 display_name（一次查询，避免 N+1）
    user_ids = [p.user_id for p in rows]
    users_coll = mongo.get_collection("users")
    display_names: dict[str, str] = {}
    if user_ids:
        from bson import ObjectId

        oid_list = [ObjectId(u) for u in user_ids if ObjectId.is_valid(u)]
        str_list = [u for u in user_ids if not ObjectId.is_valid(u)]
        query: dict[str, Any] = {"$or": [{"_id": {"$in": oid_list}} if oid_list else None, {"_id": {"$in": str_list}} if str_list else None]}
        query = {"$or": [q for q in query["$or"] if q is not None]}
        async for u in users_coll.find(query, {"display_name": 1, "username": 1}):
            display_names[str(u["_id"])] = str(u.get("display_name") or u.get("username") or "")
    return {
        "items": [
            {
                "user_id": p.user_id,
                "display_name": display_names.get(p.user_id, ""),
                "role": "owner" if p.role == "owner" else "participant",
                "joined_at": p.joined_at,
            }
            for p in rows
        ]
    }


@router.post("/{conversation_id}/participants")
async def add_participant(
    conversation_id: str,
    body: AddParticipantBody,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """新增参与者（仅 owner）。

    ★ body 必须是**真 Pydantic 模型**：早前写成 ``body: Any``，FastAPI 无法据 `Any`
    判定它是 body，于是**当成 query 参数**处理——``client.post(url, json={...})``
    的 body 永远收不到 → 422 ``{"loc": ["query","body"]}``，端端点完全不可用。
    与 ``api/endpoints/branding.py`` 的同款缺陷（P0-2 复发）。

    原先函数体里的 ``isinstance(body, dict) ... getattr(body, ...)`` 兼容分支
    已**删除**：那是在用 isinstance 硬凑绕过签名错误，而 HTTP 层根本走不到函数体
    （签名就 422 了）。改真模型后该分支没有存在意义，留着只会误导后人。
    """
    from app.collaboration.participants import ParticipantsRepository

    parts = ParticipantsRepository()
    participant = await parts.add(
        ctx, conversation_id, user_id=body.user_id, role=body.role
    )
    return {"user_id": participant.user_id, "role": participant.role}


@router.delete("/{conversation_id}/participants/{uid}")
async def remove_participant(
    conversation_id: str,
    uid: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """移除参与者（仅 owner），并吊销其文件 URL。"""
    from app.collaboration.participants import ParticipantsRepository

    parts = ParticipantsRepository()
    await parts.remove(ctx, conversation_id, user_id=uid)
    return {"ok": True}


@router.get("/{conversation_id}/approvals")
async def list_approvals(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """待决审批列表（含参数，按角色投影）。"""
    from app.api.endpoints.admin import _jsonable_items

    coll = mongo.get_collection("approvals")
    cursor = coll.find(
        {"org_unit_id": ctx.org_unit_id, "conversation_id": conversation_id, "status": "pending"}
    ).sort("created_at", -1)
    rows = await cursor.to_list(length=50)
    # approvals 文档含 ObjectId（requester_user_id / decided_by）→ 必须转字符串，
    # 否则 FastAPI 序列化 500（与 admin 列表端点同一类缺陷）。
    return {"items": _jsonable_items(rows)}


@router.get("/{conversation_id}/events")
async def list_events(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
    cursor: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """事件回放（按 cursor 分页），走投影脱敏。"""
    from app.collaboration.stream import StreamSubscription
    from app.core.errors import Forbidden

    sub = StreamSubscription(ctx, conversation_id, after_cursor=cursor)
    try:
        events = await sub.read_events()
    except Forbidden as exc:
        raise exc
    return {"items": [_viewer_payload(e) for e in events[:limit]]}


def _sse_event(ev: Any) -> str:
    import json as _json

    data = _json.dumps(_viewer_payload(ev), ensure_ascii=False)
    return f"id: {ev.cursor}\ndata: {data}\n\n"


def _viewer_payload(ev: Any) -> dict[str, Any]:
    return {
        "event_id": ev.event_id,
        "cursor": ev.cursor,
        "type": ev.type,
        "occurred_at": ev.occurred_at.isoformat() if hasattr(ev.occurred_at, "isoformat") else str(ev.occurred_at),
        "payload": ev.payload,
        "redacted": ev.redacted,
    }


def _conv_payload(c: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "owner_user_id": c.owner_user_id,
        "title": c.title,
        "visibility": c.visibility,
        "device_id": c.device_id,
        "profile_version": c.profile_version,
        "runtime_id": c.runtime_id,
        "session_id": c.session_id,
        "active_run": c.active_run,
        "created_at": c.created_at,
        "last_active_at": c.last_active_at,
    }


def _oid(value: str):
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return value


def _utcnow():
    from app.core.clock import utcnow

    return utcnow()


def _aware_utc(value: Any) -> Any:
    """Mongo 返回的 datetime 是 naive（pymongo 默认去 tzinfo）——补 UTC 后才能比较。"""
    from datetime import datetime, timezone

    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ---------------------------------------------------------------------------
# 会话分享（token 链接语义，``02`` §4.3 协同段）：
#   owner 创建分享链接（快照 participant_count + token）→ 接收者按 token 加入
#   （落 session_participants，role=participant）→ 使用 = 参与者对会话的
#   消息 / SSE / 产物访问权（``collaboration/`` 参与者校验内建）。
# 集合：``session_shares``（token + 吊销/过期）。
# ---------------------------------------------------------------------------

_SHARES_COLLECTION = "session_shares"


class ShareCreateRequest(BaseModel):
    expires_in_days: int = 7


async def _count_active_participants(ctx: RequestContext, conversation_id: str) -> int:
    from app.collaboration.participants import PARTICIPANTS_COLLECTION

    return await mongo.get_collection(PARTICIPANTS_COLLECTION).count_documents(
        {
            "conversation_id": conversation_id,
            "org_unit_id": ctx.org_unit_id,
            "removed_at": None,
        }
    )


@router.post("/{conversation_id}/shares")
async def create_session_share(
    conversation_id: str,
    body: ShareCreateRequest,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """创建会话分享链接（仅 owner；同时只保留一条活跃链接）。"""
    import uuid

    from app.collaboration.participants import ParticipantsRepository
    from app.core.errors import Forbidden

    parts = ParticipantsRepository()
    role = await parts.role_of(ctx, conversation_id)
    if role != "owner":
        raise Forbidden("仅 owner 可分享会话")

    from app.core.clock import utcnow

    now = utcnow()
    coll = mongo.get_collection(_SHARES_COLLECTION)
    # 同一会话只保留一条活跃链接：旧的活跃分享全部置 revoked
    await coll.update_many(
        {"conversation_id": conversation_id, "org_unit_id": ctx.org_unit_id, "revoked": False},
        {"$set": {"revoked": True, "updated_at": now}},
    )
    from datetime import timedelta

    expires_at = now + timedelta(days=max(1, body.expires_in_days))
    doc = {
        "share_id": uuid.uuid4().hex,
        "token": uuid.uuid4().hex,
        "conversation_id": conversation_id,
        "org_unit_id": ctx.org_unit_id,
        "owner_user_id": ctx.user_id,
        "expires_at": expires_at,
        "revoked": False,
        "created_at": now,
    }
    await coll.insert_one(doc)
    return {
        "token": doc["token"],
        "expires_at": expires_at.isoformat(),
        "participant_count": await _count_active_participants(ctx, conversation_id),
    }


@router.delete("/{conversation_id}/shares")
async def revoke_session_share(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """吊销该会话的活跃分享链接（仅 owner）。"""
    from app.core import mongo as _mongo
    from app.core.errors import Forbidden

    from app.collaboration.participants import ParticipantsRepository

    parts = ParticipantsRepository()
    role = await parts.role_of(ctx, conversation_id)
    if role != "owner":
        raise Forbidden("仅 owner 可吊销分享链接")
    await _mongo.get_collection(_SHARES_COLLECTION).update_many(
        {"conversation_id": conversation_id, "org_unit_id": ctx.org_unit_id, "revoked": False},
        {"$set": {"revoked": True, "updated_at": _utcnow()}},
    )
    return {"session_id": conversation_id}


@router.post("/{conversation_id}/leave")
async def leave_session(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """参与者自助退出会话（owner 不可退出自己的会话）。"""
    from app.core.errors import Forbidden

    from app.collaboration import participants as parts_mod

    parts = parts_mod.ParticipantsRepository()
    role = await parts.role_of(ctx, conversation_id)
    if role == "owner":
        raise Forbidden("会话 owner 不能退出自己的会话")
    await parts._parts.update_one(
        {
            "conversation_id": conversation_id,
            "org_unit_id": ctx.org_unit_id,
            "user_id": parts_mod._to_objid(ctx.user_id),
        },
        {"$set": {"removed_at": _utcnow()}},
    )
    from app.collaboration import share as share_mod

    await share_mod.revoke_files_for_participant(ctx, conversation_id, ctx.user_id)
    return {"session_id": conversation_id}


session_share_router = APIRouter(prefix="/api/v1/session-shares", tags=["session-sharing"])


@session_share_router.post("/{token}/join")
async def join_by_token(token: str, ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """按 token 加入会话（登录用户即可；org 隔离 + 未吊销 + 未过期）。

    使用段闭环：加入即落 session_participants（role=participant），
    消息 / SSE / 产物访问走既有参与者校验，无需额外授权。
    """
    from app.core.errors import Forbidden, NotFound

    shares = mongo.get_collection(_SHARES_COLLECTION)
    share = await shares.find_one({"token": token, "org_unit_id": ctx.org_unit_id})
    if share is None or share.get("revoked"):
        raise NotFound("分享链接不存在或已吊销")
    expires_at = _aware_utc(share.get("expires_at"))
    if expires_at and expires_at < _utcnow():
        raise Forbidden("分享链接已过期")

    conversation_id = str(share.get("conversation_id") or "")
    from app.collaboration import participants as parts_mod

    parts = parts_mod.ParticipantsRepository()
    existing_role = None
    try:
        existing_role = await parts.role_of(ctx, conversation_id)
    except Exception:
        existing_role = None
    if existing_role is None:
        # 新参与者：落参与者关系（role=participant）
        await parts._parts.update_one(
            {
                "conversation_id": conversation_id,
                "org_unit_id": ctx.org_unit_id,
                "user_id": parts_mod._to_objid(ctx.user_id),
            },
            {
                "$set": {"role": "participant", "removed_at": None, "joined_at": _utcnow()},
            },
            upsert=True,
        )
    # 已是参与者（含 owner 加入自己的会话）：幂等返回
    return {"session_id": conversation_id}
