"""turn 端点（``api/endpoints/turns.py``，``02`` §4.3）。

- POST /api/v1/conversations/{id}/turns：发起 turn；busy 时返回 409。
- POST /api/v1/conversations/{id}/turns/{request_id}/cancel：取消。
- POST /api/v1/conversations/{id}/takeover/request、/takeover/accept：接管。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import mongo
from app.core.errors import Forbidden
from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/api/v1/conversations", tags=["turns"])


class TurnRequest(BaseModel):
    content: list[dict[str, Any]]
    request_id: str


class CancelRequest(BaseModel):
    cause: str = "user_cancelled"


@router.post("/{conversation_id}/turns")
async def start_turn(
    conversation_id: str,
    body: TurnRequest,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """发起一次 turn（editor+ 可发起；busy 返回 409）。

    ``turn_direct_relay`` 开启且提供商已配置时走直连路径（#18：
    messages 直发提供商，不经 DSH 宿主）；否则走 DSH 宿主路径。
    """
    from app.collaboration.participants import ParticipantsRepository
    from app.core.errors import Forbidden, NotFound
    from app.kernel.turn.admission import admit
    from app.kernel.contract.commands import SendRequest, ContentBlock, TemporalContext
    from app.core.clock import utcnow

    parts = ParticipantsRepository()
    role = await parts.role_of(ctx, conversation_id)
    if role not in ("owner", "editor"):
        raise Forbidden("仅 owner/editor 可发起 turn")

    from app.settings import get_settings
    from app.kernel.inbound import model_relay as model_relay_mod

    settings = get_settings()
    use_direct = bool(
        getattr(settings, "turn_direct_relay", False) and model_relay_mod.enabled()
    )
    if use_direct:
        return await _start_turn_direct(
            ctx, conversation_id=conversation_id, body=body, settings=settings
        )

    # 准入（Profile 存在 + 并发 claim；busy → 409）
    admission = await admit(ctx, conversation_id, request_id=body.request_id)

    # claim 释放：正常完成/异常/断连一律释放（取消路径已由 finalize 释放，
    # release 按 token 比对幂等）。漏释放会让同会话新 turn 409 到 TTL（300s）过期。
    from app.kernel.turn import claims

    try:
        # 构造内容块并经 kernel/turn/driver 驱动（app.* 禁止 import kernel/dsh，由 driver 收敛）
        from app.kernel.contract.commands import ContentBlock
        from app.kernel.turn.driver import start_turn

        # 用户消息落库（content 与 ContentBlock 契约同形：{type, data}）
        from app.collaboration.messages import MessagesRepository

        repo = MessagesRepository()
        seq = await repo.next_seq(conversation_id)
        user_content = [
            {"type": str(block.get("type", "text")), "data": block.get("data") or {}}
            for block in body.content
        ]
        await repo.append(
            ctx,
            conversation_id,
            seq=seq,
            role="user",
            content=user_content,
            meta={"request_id": body.request_id, "source": "dsh_host"},
        )

        blocks = [
            ContentBlock(type=block.get("type", "text"), data=block.get("data") or {})
            for block in body.content
        ]
        result = await start_turn(
            ctx,
            conversation_id=conversation_id,
            request_id=body.request_id,
            content=blocks,
            turn_context={"admission": admission.profile_version},
        )
    finally:
        await claims.release_turn(conversation_id, body.request_id)

    # assistant 回复落库（text / finish_reason 由宿主 turn_end 事件提取）
    from app.collaboration.messages import MessagesRepository

    repo = MessagesRepository()
    seq = await repo.next_seq(conversation_id)
    await repo.append(
        ctx,
        conversation_id,
        seq=seq,
        role="assistant",
        content=[{"type": "text", "data": {"text": str(result.get("text", ""))}}],
        meta={
            "request_id": body.request_id,
            "source": "dsh_host",
            "finish_reason": result.get("finish_reason", "stop"),
            "runtime_id": result.get("runtime_id", ""),
            "kernel_session_id": result.get("session_id", ""),
        },
    )

    return {
        "ok": True,
        "request_id": result["request_id"],
        "conversation_id": conversation_id,
        "profile_version": admission.profile_version,
        "finish_reason": result.get("finish_reason", "stop"),
    }


async def _start_turn_direct(
    ctx: RequestContext,
    *,
    conversation_id: str,
    body: TurnRequest,
    settings: Any,
) -> dict[str, Any]:
    """直连 relay 路径（#18）：用户消息落库 → 品牌注入 + 历史组装 → 直发提供商 →
    V3 事件经回调持久化 → assistant 回复落库。"""
    from app.collaboration.messages import MessagesRepository
    from app.core.clock import utcnow
    from app.kernel.inbound.model_endpoint import (
        _load_branding_runtime_context,
        _render_context_block,
    )
    from app.kernel.turn.admission import admit
    from app.kernel.turn.driver import start_turn_direct_relay

    # 1) 准入（Profile 存在 + 并发 claim；busy → 409）
    admission = await admit(ctx, conversation_id, request_id=body.request_id)

    # claim 释放：正常完成/异常/断连一律释放（取消路径已由 finalize 释放，
    # release 按 token 比对幂等）。漏释放会让同会话新 turn 409 到 TTL（300s）过期。
    from app.kernel.turn import claims

    try:
        return await _direct_turn_body(
            ctx, conversation_id=conversation_id, body=body, admission=admission
        )
    finally:
        await claims.release_turn(conversation_id, body.request_id)


async def _direct_turn_body(
    ctx: RequestContext,
    *,
    conversation_id: str,
    body: TurnRequest,
    admission: Any,
) -> dict[str, Any]:
    """turn 主体（准入后）：用户消息落库 → 直发提供商 → assistant 回复落库。"""
    from app.collaboration.messages import MessagesRepository
    from app.kernel.inbound.model_endpoint import (
        _load_branding_runtime_context,
        _render_context_block,
    )
    from app.kernel.turn.driver import start_turn_direct_relay

    # 2) 用户消息落库（content 与 ContentBlock 契约同形：{type, data}）
    repo = MessagesRepository()
    seq = await repo.next_seq(conversation_id)
    user_content = [
        {"type": str(block.get("type", "text")), "data": block.get("data") or {}}
        for block in body.content
    ]
    await repo.append(
        ctx,
        conversation_id,
        seq=seq,
        role="user",
        content=user_content,
        meta={"request_id": body.request_id, "source": "turn_direct_relay"},
    )

    # 3) 组装 provider messages：品牌 system 块 + 全部历史（含刚落的 user 消息）
    branding_ctx = await _load_branding_runtime_context()
    system_block = _render_context_block(branding_ctx) if branding_ctx else ""
    provider_messages: list[dict[str, Any]] = []
    if system_block:
        provider_messages.append({"role": "system", "content": system_block})
    history = await repo.list(ctx, conversation_id)
    for m in history:
        if m.role not in ("user", "assistant", "system"):
            continue
        text = "".join(
            str(b.get("data", {}).get("text", ""))
            for b in m.content
            if isinstance(b, dict) and b.get("type") == "text"
        )
        if not text:
            continue
        provider_messages.append({"role": m.role, "content": text})

    # 4) 直连 relay 驱动（V3 事件经 _on_event 持久化到 kernel_events）
    result = await start_turn_direct_relay(
        ctx,
        conversation_id=conversation_id,
        request_id=body.request_id,
        messages=provider_messages,
        profile_version=admission.profile_version,
    )

    # 5) assistant 回复落库
    reply_text = str(result.get("text", ""))
    seq = await repo.next_seq(conversation_id)
    await repo.append(
        ctx,
        conversation_id,
        seq=seq,
        role="assistant",
        content=[{"type": "text", "data": {"text": reply_text}}],
        meta={
            "request_id": body.request_id,
            "source": "turn_direct_relay",
            "finish_reason": result.get("finish_reason", "stop"),
        },
    )

    return {
        "ok": True,
        "request_id": body.request_id,
        "conversation_id": conversation_id,
        "profile_version": admission.profile_version,
        "finish_reason": result.get("finish_reason", "stop"),
    }


@router.post("/{conversation_id}/turns/{request_id}/cancel")
async def cancel_turn(
    conversation_id: str,
    request_id: str,
    body: CancelRequest,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """取消进行中的 turn（持有者或 owner）。"""
    from app.collaboration.participants import ParticipantsRepository
    from app.core.errors import Forbidden
    from app.kernel.turn.cancellation import cancel_turn as do_cancel

    parts = ParticipantsRepository()
    role = await parts.role_of(ctx, conversation_id)
    if role not in ("owner", "editor"):
        raise Forbidden("仅持有者或 owner 可取消")

    await do_cancel(
        org_unit_id=ctx.org_unit_id,
        conversation_id=conversation_id,
        request_id=request_id,
        owner_user_id=ctx.user_id,
        cause=body.cause,
    )
    return {"ok": True}


@router.post("/{conversation_id}/takeover/request")
async def takeover_request(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """请求接管（editor+）。返回接管请求标识（原持有者会收到通知）。"""
    from app.collaboration.participants import ParticipantsRepository
    from app.core.errors import Forbidden

    parts = ParticipantsRepository()
    role = await parts.role_of(ctx, conversation_id)
    if role not in ("owner", "editor"):
        raise Forbidden("仅 editor+ 可请求接管")
    # 阶段 1：返回受理，真实通知由协作事件承接
    return {"ok": True, "status": "requested"}


@router.post("/{conversation_id}/takeover/accept")
async def takeover_accept(
    conversation_id: str,
    ctx: RequestContext = Depends(get_ctx),
    request_id: str | None = None,
) -> dict[str, Any]:
    """确认接管转移（原持有者）。"""
    from app.kernel.turn import claims

    if request_id:
        await claims.transfer_turn(conversation_id, to_request_id=request_id)
    return {"ok": True}
