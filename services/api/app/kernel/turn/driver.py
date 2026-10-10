"""turn 驱动编排（``kernel/turn/driver.py``）。

把一次 turn 的完整驱动收敛在 ``kernel/`` 层内：编译 Profile → 构造 SendRequest →
经 DshKernelGateway（kernel/dsh）下发宿主 → 订阅事件流交上层持久化/投影。
（准入/claim 由端点层 ``api/endpoints/turns.py`` 完成，本模块不做二次准入。）

**分层关键**：本模块位于 ``kernel/`` 内，是唯一允许 import ``kernel/dsh/`` 的业务编排点
（``01`` §3：``app.* → kernel/dsh/`` 禁止；``kernel/dsh/`` 是内核接入层）。
上层 ``api/endpoints/turns.py`` 只调用本模块，不直接触碰 DSH 适配器。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

from app.core.errors import KernelUnavailable
from app.core.clock import utcnow
from app.identity.context import RequestContext
from app.kernel.contract.commands import ContentBlock, SendRequest
from app.kernel.dsh.adapters.events import TERMINAL_TYPES
from app.kernel.profile.compiler import compile_profile

#: turn 驱动回调（事件归一化后交给上层持久化/投影；由 app 装配时注入）
_on_event: Any = None


def set_event_handler(handler: Any) -> None:
    """注册事件回调（由 main.py 装配时注入，避免 kernel → collaboration 依赖）。"""
    global _on_event
    _on_event = handler


async def start_turn(
    ctx: RequestContext,
    *,
    conversation_id: str,
    request_id: str,
    content: list[ContentBlock],
    turn_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """驱动一次 turn 的完整流程（runtime 供给链，``03`` §6）。

    流程：编译 Profile → 预生成 runtime_id → 双凭证签发（HMAC 短时 token）→
    create_runtime（凭证 + Profile 快照一次性供给）→ create_session →
    先订阅事件流再 send（防终态事件早于订阅建立而丢失）→ 消费至终态 →
    dispose_runtime 清理。

    参数
    ----
    ctx:
        发起者上下文。
    conversation_id / request_id:
        会话与 turn 请求标识。
    content:
        消息内容块列表。
    turn_context:
        附加 turn 上下文（如 admission 信息）。

    返回 ``{request_id, profile_version, text, finish_reason, runtime_id, session_id}``
    （text / finish_reason 由宿主 turn_end 事件提取，app 层落 assistant 消息）。

    异常
    ----
    KernelUnavailable:
        宿主不可用（调用时明确报错，不静默）。
    """
    # 1) 编译 Profile（确认版本）。
    #    ★ 准入已由端点层（turns.py admit）完成：先抢 claim 再调用本方法——这里
    #    **不能**再次 admit，否则对同一把 claim 键重复 SET NX 必失败，DSH 路径
    #    所有 turn 都会 409 conversation_busy（turn_direct_relay=1 就是为绕开它设的）。
    profile = await compile_profile(ctx.org_unit_id, ctx.user_id)

    from app.settings import get_settings
    from app.kernel.dsh.gateway import DshKernelGateway

    settings = get_settings()
    gateway = DshKernelGateway(settings)

    # 2) runtime 供给链。runtime_id 由调用方预生成（不把 request_id 编进去——
    #    request_id 会进 URL path；token 载荷 rid 需在 create_runtime 前确定）。
    #    每 turn 一个 runtime：PUT 凭证端点不生效（token 在 runtime start 时烤进
    #    adapter），复用会让过期 token 毒化后续 turn。
    runtime_id = f"rt_{uuid.uuid4().hex}"

    # 双凭证（HMAC 短时 token，域分离密钥；gateway_url 必须是服务根地址——
    # 宿主侧 GatewayClient 自拼 /internal/kernel/{model/generate, tools/execute}，
    # 带路径会路径翻倍 404）。
    from app.kernel.inbound import credentials as gw_credentials

    callback_base = settings.app_base_url.rstrip("/")
    tool_names = frozenset(
        name for name in (getattr(t, "name", "") for t in profile.tools) if name
    )
    model_token = gw_credentials.issue(
        aud="model",
        secret=settings.kernel_model_gateway_secret,
        org_unit_id=ctx.org_unit_id,
        user_id=ctx.user_id,
        profile_version=profile.profile_version,
        runtime_id=runtime_id,
    )
    tool_token = gw_credentials.issue(
        aud="tool",
        secret=settings.kernel_tool_gateway_secret,
        org_unit_id=ctx.org_unit_id,
        user_id=ctx.user_id,
        profile_version=profile.profile_version,
        runtime_id=runtime_id,
        tool_names=tool_names,
    )

    from app.kernel.contract.commands import (
        CreateRuntimeRequest,
        CreateSessionRequest,
        SessionSpec,
    )

    rt = None
    pump: asyncio.Task[Any] | None = None
    returned_rid = ""
    host_session_id = ""
    final_text = ""
    finish_reason = "stop"

    try:
        rt = await gateway.create_runtime(
            CreateRuntimeRequest(
                org_unit_id=ctx.org_unit_id,
                profile_version=profile.profile_version,
                isolation_key=f"{ctx.org_unit_id}:{ctx.user_id}",
            ),
            runtime_id=runtime_id,
            profile=profile.model_dump(),
            model_credential={"gateway_url": callback_base, "token": model_token},
            tool_credential={"gateway_url": callback_base, "token": tool_token},
        )
        session = await gateway.create_session(
            CreateSessionRequest(
                runtime_id=rt.runtime_id,
                session_spec=SessionSpec(
                    conversation_id=conversation_id,
                    org_unit_id=ctx.org_unit_id,
                    user_id=ctx.user_id,
                    profile_version=profile.profile_version,
                ),
            )
        )
        host_session_id = session.session_id

        # 3) 先订阅事件流再 send：宿主 event-stream 是 live 广播（不回放），
        #    send 后才订阅会漏掉早于连接建立的终态事件。订阅按 session_id 定址
        #    （与 send URL 同名，即 conversation_id）。
        subscribe_sid = conversation_id
        queue: asyncio.Queue[Any] = asyncio.Queue()

        async def _pump_events() -> None:
            """订阅宿主事件流并入队（流关闭/断线后放哨兵，消费循环据此收尾）。"""
            try:
                async for envelope in gateway.subscribe(subscribe_sid):
                    await queue.put(envelope)
            except Exception:  # noqa: BLE001 - 断线/异常统一以哨兵收尾，不静默抛出
                pass
            finally:
                await queue.put(None)

        pump = asyncio.create_task(_pump_events())

        # 4) 构造 SendRequest 并经 gateway 下发（显式 runtime_id，不依赖宿主通配解析）
        # V3 线缆协议翻译：前端 useTurnStream 只认 run.* / item.* 词汇表（与直连
        # relay 同一协议，见 start_turn_direct_relay / EVENT_* 常量）。宿主归一化
        # 事件（assistant.message / turn.finished / ...）若不翻译，前端 mapper 会
        # 静默丢弃全部事件 → 时间线空、"思考中"永不收束。翻译件与原生件都落
        # kernel_events：原生件留审计（工具名在 payload），V3 件驱动前端。
        final_item_id = f"item_{request_id}"
        v3_opened = False
        v3_seq = 0
        handler = _on_event

        async def _emit_v3(ev_type: str, payload: dict[str, Any]) -> None:
            if handler is None:
                return
            await handler(
                org_unit_id=ctx.org_unit_id,
                conversation_id=conversation_id,
                envelope=_make_envelope(
                    conversation_id=conversation_id,
                    request_id=request_id,
                    profile_version=profile.profile_version,
                    ev_type=ev_type,
                    payload=payload,
                    runtime_id=rt.runtime_id if rt is not None else "relay-direct",
                ),
            )

        req = SendRequest(
            session_id=conversation_id,
            request_id=request_id,
            content=content,
            turn_context=turn_context or {"profile_version": profile.profile_version},
        )
        returned_rid = await gateway.send(req, runtime_id=rt.runtime_id)

        # send 已受理：立即发 run.started（kernel-host agent 循环不发射 turn_start，
        # run.started 在这里补齐，保证前端时间线有 run 起点事件）。
        await _emit_v3(
            EVENT_RUN_STARTED, {"run_id": request_id, "request_id": request_id}
        )

        # 5) 消费事件流至终态（terminal 后 break——宿主流是 keep-alive，不 break 挂起）

        while True:
            envelope = await queue.get()
            if envelope is None:
                break
            if handler is not None:
                await handler(
                    org_unit_id=ctx.org_unit_id,
                    conversation_id=conversation_id,
                    envelope=envelope,
                )
            # ---- 归一化事件 → V3 线缆协议（前端契约）----
            ntype = envelope.type
            npl = envelope.payload or {}
            if ntype == "assistant.message":
                delta = str(npl.get("delta", ""))
                if delta:
                    if not v3_opened:
                        v3_opened = True
                        await _emit_v3(
                            EVENT_ITEM_STARTED,
                            {"item_kind": "final_answer", "item_id": final_item_id, "revision": 0},
                        )
                    await _emit_v3(
                        EVENT_ITEM_DELTA,
                        {
                            "item_kind": "final_answer",
                            "item_id": final_item_id,
                            "text": delta,
                            "stream_seq": v3_seq,
                        },
                    )
                    v3_seq += 1
            elif ntype == "tool.call.requested":
                tool_item_id = f"tool_{npl.get('id') or npl.get('name') or v3_seq}"
                tool_name = str(npl.get("name", "tool"))
                args = str(npl.get("arguments", ""))
                await _emit_v3(
                    EVENT_ITEM_STARTED,
                    {
                        "item_kind": "tool",
                        "item_id": tool_item_id,
                        "revision": 0,
                        "name": tool_name,
                        "args": args,
                    },
                )
                await _emit_v3(
                    EVENT_ITEM_DELTA,
                    {
                        "item_kind": "tool",
                        "item_id": tool_item_id,
                        "text": f"{tool_name}({args[:120]})",
                        "name": tool_name,
                        "args": args,
                        "stream_seq": 0,
                    },
                )
            elif ntype == "tool.call.completed":
                ok = str(npl.get("ok", "")).lower() == "true"
                tool_name = str(npl.get("name", "tool"))
                await _emit_v3(
                    EVENT_ITEM_COMPLETED,
                    {
                        "item_kind": "tool",
                        "item_id": f"tool_{npl.get('id') or npl.get('name') or ''}",
                        "text": f"{tool_name} → {'ok' if ok else 'failed'}",
                        "name": tool_name,
                        "ok": ok,
                        "result": _tool_result_summary(npl.get("result"), npl.get("error")),
                    },
                )
            elif ntype == "turn.finished":
                host_text = str(npl.get("text", ""))
                if v3_opened:
                    await _emit_v3(
                        EVENT_ITEM_COMPLETED,
                        {"item_kind": "final_answer", "item_id": final_item_id, "text": host_text},
                    )
                else:
                    # 无任何 delta（空流）：仍补 item 生命周期，前端据此结束 loading
                    await _emit_v3(
                        EVENT_ITEM_STARTED,
                        {"item_kind": "final_answer", "item_id": final_item_id, "revision": 0},
                    )
                    await _emit_v3(
                        EVENT_ITEM_COMPLETED,
                        {"item_kind": "final_answer", "item_id": final_item_id, "text": host_text},
                    )
                await _emit_v3(
                    EVENT_RUN_COMPLETED,
                    {
                        "run_id": request_id,
                        "request_id": request_id,
                        "finish_reason": str(npl.get("finish_reason", "stop")),
                    },
                )
            elif ntype == "error":
                await _emit_v3(
                    EVENT_TURN_ERROR,
                    {"message": str(npl.get("message") or npl.get("error") or "内核错误")},
                )
                await _emit_v3(
                    EVENT_RUN_COMPLETED,
                    {"run_id": request_id, "request_id": request_id, "finish_reason": "error"},
                )
            if envelope.type in TERMINAL_TYPES:
                payload = envelope.payload or {}
                if envelope.type == "turn.finished":
                    final_text = str(payload.get("text", ""))
                    finish_reason = str(payload.get("finish_reason", "stop"))
                else:  # error 终态（宿主 turn_end 会紧随其后，不再等待）
                    finish_reason = "error"
                break
    except KernelUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 宿主异常统一映射为不可用
        raise KernelUnavailable(f"内核宿主调用失败: {exc}") from exc
    finally:
        if pump is not None:
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # 释放 runtime（清理失败不阻塞返回：宿主有 TTL 兜底）
        if rt is not None:
            with contextlib.suppress(Exception):
                await gateway.dispose_runtime(rt.runtime_id)

    return {
        "request_id": returned_rid or request_id,
        "profile_version": profile.profile_version,
        "text": final_text,
        "finish_reason": finish_reason,
        "runtime_id": rt.runtime_id if rt is not None else "",
        "session_id": host_session_id,
    }


async def health_check() -> dict[str, Any]:
    """探测内核宿主健康（kernel 层内封装，避免 app.* 直连 kernel/dsh）。

    宿主不可用时不阻塞启动（``01`` §5.1）：失败抛 KernelUnavailable，由调用方告警。
    """
    from app.settings import get_settings
    from app.kernel.dsh.gateway import DshKernelGateway

    settings = get_settings()
    gateway = DshKernelGateway(settings)
    return await gateway.health_check()


# --------------------------------------------------------------------------
# 直连 relay 路径（#18）：跳过 DSH 宿主，messages 直发模型提供商，
# DSH StreamChunk → V3 产品事件经 ``_on_event`` 交上层持久化。
# 分层：本模块不 import collaboration（禁止），消息组装/落库由 app 层完成。
# --------------------------------------------------------------------------

#: V3 产品事件类型（SSE data.type，前端只看这些）
EVENT_RUN_STARTED = "run.started"
EVENT_ITEM_STARTED = "item.started"
EVENT_ITEM_DELTA = "item.delta"
EVENT_ITEM_COMPLETED = "item.completed"
EVENT_RUN_COMPLETED = "run.completed"
EVENT_TURN_ERROR = "turn.error"

_RESULT_SUMMARY_LIMIT = 200


def _tool_result_summary(result: Any, error: Any) -> str:
    """工具结果的一行摘要（供前端时间线直接展示；截断到 200 字符）。"""
    if error:
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "tool failed")
        return str(error)
    if result is None:
        return ""
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    text = text.strip()
    if len(text) > _RESULT_SUMMARY_LIMIT:
        text = text[:_RESULT_SUMMARY_LIMIT] + "…"
    return text


def _make_envelope(
    *,
    conversation_id: str,
    request_id: str,
    profile_version: str,
    ev_type: str,
    payload: dict[str, Any],
    runtime_id: str = "relay-direct",
) -> Any:
    """构造一条契约事件（cursor=0 占位，持久化时由上层重新分配）。"""
    from app.kernel.contract.events import KernelEventEnvelope, KernelEventSource

    return KernelEventEnvelope(
        event_id=str(uuid.uuid4()),
        runtime_id=runtime_id,
        session_id=conversation_id,
        profile_version=profile_version,
        cursor=0,
        type=ev_type,
        occurred_at=utcnow(),
        payload=payload,
        source=KernelEventSource(
            kernel="dsh", kernel_version="relay-direct", native_event_type=None
        ),
    )


async def start_turn_direct_relay(
    ctx: RequestContext,
    *,
    conversation_id: str,
    request_id: str,
    messages: list[dict[str, Any]],
    profile_version: str = "",
) -> dict[str, Any]:
    """直连 relay 驱动一次 turn（不经 DSH 宿主）。

    流程：``model_relay.stream_to_provider`` 消费提供商流式响应 →
    DSH StreamChunk 翻译为 V3 产品事件（run.started / item.started /
    item.delta / item.completed / run.completed / turn.error）→
    经 ``_on_event``（若装配）交上层持久化到 ``kernel_events``。

    参数
    ----
    messages:
        OpenAI 风格消息列表（含品牌 system 块与历史），由 app 层组装。
        上下文裁剪由 relay 内部（``_shrink_messages``）承担。

    返回 ``{text, finish_reason, usage}``（文本由 app 层 append 为 assistant 消息）。
    """
    from app.kernel.inbound import model_relay
    from app.settings import get_settings

    settings = get_settings()
    request_body: dict[str, Any] = {
        "model": settings.model_provider_model,
        "session_id": conversation_id,
        "max_tokens": settings.model_provider_max_output_tokens,
    }

    # ★ S-44：配额在模型调用入口统一扣减（与 kernel gateway /internal/kernel/model/generate
    #   同策略）。用户侧直连 relay 是真实用户的模型调用路径，不扣减会让配额形同虚设。
    #   超额 QuotaExceeded 向上传播 → turns 端点 429；拒绝由 charge_model_tokens 留审计。
    from app.governance import quota as quota_mod
    from app.kernel.inbound.model_endpoint import _estimate_prompt_tokens

    await quota_mod.charge_model_tokens(
        ctx,
        session_id=conversation_id,
        prompt=_estimate_prompt_tokens({"messages": messages}),
        completion=0,
    )

    # 上下文裁剪（防 context_length_exceeded，与 inbound/generate_stream 同策略）
    shrunk = model_relay._shrink_messages(messages, settings.model_provider_max_context_tokens)

    async def emit(ev_type: str, payload: dict[str, Any]) -> None:
        if _on_event is None:
            return
        envelope = _make_envelope(
            conversation_id=conversation_id,
            request_id=request_id,
            profile_version=profile_version,
            ev_type=ev_type,
            payload=payload,
        )
        await _on_event(
            org_unit_id=ctx.org_unit_id,
            conversation_id=conversation_id,
            envelope=envelope,
        )

    await emit(EVENT_RUN_STARTED, {"run_id": request_id, "request_id": request_id})

    final_item_id = f"item_{request_id}"
    commentary_item_id = f"item_{request_id}_thought"
    text_buf = ""
    thought_buf = ""
    stream_seq = 0
    final_opened = False
    commentary_opened = False
    finish_reason = "stop"
    usage: dict[str, Any] | None = None

    try:
        async for chunk in model_relay.stream_to_provider(request_body, shrunk):
            ctype = chunk.get("type")
            if ctype == "block-start":
                # 首个内容块：开 final_answer item
                if not final_opened:
                    final_opened = True
                    await emit(
                        EVENT_ITEM_STARTED,
                        {"item_kind": "final_answer", "item_id": final_item_id, "revision": 0},
                    )
            elif ctype == "text-delta":
                delta_text = chunk.get("text", "")
                text_buf += delta_text
                await emit(
                    EVENT_ITEM_DELTA,
                    {
                        "item_kind": "final_answer",
                        "item_id": final_item_id,
                        "text": delta_text,
                        "stream_seq": stream_seq,
                    },
                )
                stream_seq += 1
            elif ctype == "reasoning-delta":
                delta_text = chunk.get("text", "")
                if not commentary_opened:
                    commentary_opened = True
                    await emit(
                        EVENT_ITEM_STARTED,
                        {"item_kind": "commentary", "item_id": commentary_item_id, "revision": 0},
                    )
                thought_buf += delta_text
                await emit(
                    EVENT_ITEM_DELTA,
                    {
                        "item_kind": "commentary",
                        "item_id": commentary_item_id,
                        "text": delta_text,
                        "stream_seq": stream_seq,
                    },
                )
                stream_seq += 1
            elif ctype == "usage":
                usage = chunk.get("usage")
            elif ctype == "finish":
                finish_reason = str(chunk.get("reason", "stop"))
                if finish_reason == "error":
                    await emit(EVENT_TURN_ERROR, {"message": "模型提供商返回错误"})
                    await emit(
                        EVENT_RUN_COMPLETED,
                        {"run_id": request_id, "request_id": request_id, "finish_reason": "error"},
                    )
                    return {"text": text_buf, "finish_reason": "error", "usage": usage}
    except Exception as exc:  # noqa: BLE001 - 流中断统一映射为 turn.error
        await emit(EVENT_TURN_ERROR, {"message": f"模型流中断: {exc}"})
        await emit(
            EVENT_RUN_COMPLETED,
            {"run_id": request_id, "request_id": request_id, "finish_reason": "error"},
        )
        return {"text": text_buf, "finish_reason": "error", "usage": usage}

    # 收尾：commentary / final_answer item.completed + run.completed
    if commentary_opened:
        await emit(
            EVENT_ITEM_COMPLETED,
            # 收尾帧必须带思考全文：前端 store 以 payload 合并，空 text 会
            # 把 delta 流式积累的思考内容清空，导致时间线展开为空白。
            {"item_kind": "commentary", "item_id": commentary_item_id, "text": thought_buf},
        )
    if final_opened:
        await emit(
            EVENT_ITEM_COMPLETED,
            {"item_kind": "final_answer", "item_id": final_item_id, "text": text_buf},
        )
    else:
        # 无任何内容块（空流）：仍补 item 生命周期，前端据此结束 loading
        await emit(
            EVENT_ITEM_STARTED,
            {"item_kind": "final_answer", "item_id": final_item_id, "revision": 0},
        )
        await emit(
            EVENT_ITEM_COMPLETED,
            {"item_kind": "final_answer", "item_id": final_item_id, "text": ""},
        )
    await emit(
        EVENT_RUN_COMPLETED,
        {"run_id": request_id, "request_id": request_id, "finish_reason": finish_reason},
    )
    return {"text": text_buf, "finish_reason": finish_reason, "usage": usage}
