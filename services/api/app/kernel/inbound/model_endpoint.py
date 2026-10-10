"""模型网关端点（``kernel/inbound/model_endpoint.py``，``03`` §4.1 / ``00`` §4）。

``/internal/kernel/model/generate``：内核回调模型推理时，主服务在转发给模型提供商**之前**，
读取 ``branding_settings`` 并把品牌信息拼成一段**turn 级运行时上下文**注入本次请求，
随后经 ``model_relay`` 把请求转发给真实模型提供商，并把流式响应转成 DSH 内核消费的
NDJSON StreamChunk 逐行吐回。

关键（``00`` §12.4 / ``03`` §4.1 / ``S-60``）：
- 品牌注入**不进入 Runtime Profile 的内容寻址**（不影响 profile_version），不破坏会话可续性；
- 品牌随每次 turn 重新读取，``branding`` 模块对 kernel 的依赖方向与 collaboration 一致
  （经 kernel/inbound 调用，不破坏分层）。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.kernel.inbound import credentials
from app.kernel.inbound.credentials import GatewayClaims

log = get_logger("kernel.inbound.model_endpoint")

#: 品牌运行时上下文的系统块 key（注入到 messages 前的独立 context 块）
_RUNTIME_CONTEXT_SYSTEM = "runtime_context"

#: 品牌注入模块名（避免硬编码，供 dependency 解析）
BRANDING_INJECT_KEY = "zy_branding_context"


async def _load_branding_runtime_context() -> dict[str, Any]:
    """读取品牌设置并构造 turn 级运行时上下文块（仅公开白名单字段）。

    随每次 turn 重新读取，客户改品牌后下一个 turn 即生效（``00`` §12.4）。
    """
    from app.branding import settings as branding_settings

    try:
        bs = await branding_settings.load()
    except Exception as exc:  # pragma: no cover - 品牌不可用时不阻塞模型调用
        log.warning("branding load failed for runtime context", extra={"zy": {"error": str(exc)}})
        return {}
    from app.branding.public_view import public_payload

    return {"branding": public_payload(bs)}


def _check_model_claim(claims: GatewayClaims) -> None:
    """校验网关声明确为 model 域（aud=model）。"""
    from app.core.errors import GatewayTokenError

    if claims.aud != credentials.AUD_MODEL:
        raise GatewayTokenError("模型端点要求 model token", details={"got": claims.aud})


async def _prepare_messages(claims: GatewayClaims, request_body: dict[str, Any]) -> list[dict[str, Any]]:
    """注入品牌 turn 级运行时上下文，返回处理后的 messages。

    品牌注入不进入 Runtime Profile 的内容寻址（``00`` §12.4 / ``S-60``），
    随每次 turn 重新读取。
    """
    branding_ctx = await _load_branding_runtime_context()
    messages = list(request_body.get("messages") or [])
    if branding_ctx:
        messages.insert(0, {"role": "system", "content": _render_context_block(branding_ctx)})
    return messages


async def _charge_quota(claims: GatewayClaims, request_body: dict[str, Any]) -> int:
    """按估算 prompt token 扣减配额。返回估算 prompt token 数。

    超额（QuotaExceeded）必须穿透——"超额调用被拒并留审计"是验收项
    （``S-44``/``S-45``/``00`` §11 验收③）；其他异常（Mongo 临时不可用等
    基础设施故障）尽力而为，不阻断模型调用本身。
    """
    prompt = _estimate_prompt_tokens(request_body)
    from app.core.errors import QuotaExceeded
    from app.governance import quota as quota_mod

    try:
        await quota_mod.charge_model_tokens_from_claims(
            claims,
            prompt=prompt,
            completion=0,
            session_id=request_body.get("session_id", ""),
        )
    except QuotaExceeded:
        raise
    except Exception:  # pragma: no cover - 配额基础设施异常不应阻断模型调用本身
        log.warning("quota charge failed", exc_info=True)
    return prompt


async def precheck_quota_or_reject(
    claims: GatewayClaims, request_body: dict[str, Any]
) -> None:
    """配额预检：超额抛 QuotaExceeded（预留审计），不扣减。

    供 kernel.py 端点在构造 StreamingResponse **之前**调用——流式响应一旦
    开始返回，200 已发出，此时再拒绝只能截断流而非给出 429（``S-44``）。
    """
    from app.governance import quota as quota_mod

    await quota_mod.ensure_model_quota_from_claims(
        claims,
        session_id=request_body.get("session_id", ""),
        estimated_prompt=_estimate_prompt_tokens(request_body),
    )


async def handle_model_generate(
    claims: GatewayClaims,
    request_body: dict[str, Any],
) -> dict[str, Any]:
    """处理一次模型推理回调（非流式契约，向后兼容）。

    鉴权 + 品牌上下文注入 + 配额勾稽后，返回注入品牌上下文后的模型请求体，
    交由模型网关执行（真实流式转发见 :func:`generate_stream`）。
    """
    _check_model_claim(claims)
    messages = await _prepare_messages(claims, request_body)
    request_body["messages"] = messages
    await _charge_quota(claims, request_body)
    return request_body


async def generate_stream(
    claims: GatewayClaims,
    request_body: dict[str, Any],
) -> AsyncIterator[str]:
    """流式处理一次模型推理回调，产出 NDJSON 字符串行（每行一个 DSH StreamChunk）。

    流程：
      1. 鉴权（aud=model）；
      2. ``relay.enabled()`` 未启用 → 吐单行 ``{"type":"finish","reason":"error"}`` 后返回；
      3. 品牌上下文注入 messages；
      4. 配额扣减（勾稽）；
      5. ``model_relay._shrink_messages`` 做上下文裁剪（防 ``context_length_exceeded``）；
      6. 遍历 ``model_relay.stream_to_provider``，把每个 StreamChunk dict 序列化为
         JSON + ``\\n`` 逐行 yield（DSH 内核按 NDJSON 逐行消费）。
    """
    from app.kernel.inbound import model_relay

    _check_model_claim(claims)
    if not model_relay.enabled():
        yield json.dumps({"type": "finish", "reason": "error"}, ensure_ascii=False) + "\n"
        return

    messages = await _prepare_messages(claims, request_body)
    request_body["messages"] = messages
    await _charge_quota(claims, request_body)

    settings = model_relay._settings()
    shrunk = model_relay._shrink_messages(
        messages, settings.model_provider_max_context_tokens
    )

    async for chunk in model_relay.stream_to_provider(request_body, shrunk):
        yield json.dumps(chunk, ensure_ascii=False) + "\n"


def _render_context_block(branding_ctx: dict[str, Any]) -> str:
    """把品牌上下文渲染为一段自然语言 system 块。"""
    branding = branding_ctx.get("branding") or {}
    lines = ["[系统信息]"]
    if branding.get("display_name"):
        lines.append(f"当前服务的企业：{branding['display_name']}")
    if branding.get("short_name"):
        lines.append(f"简称：{branding['short_name']}")
    if branding.get("theme"):
        lines.append(f"品牌主题色：{branding['theme']}")
    return "\n".join(lines)


def _estimate_prompt_tokens(body: dict[str, Any]) -> int:
    """粗略估算 prompt token 数（用于配额记录）。"""
    # 简化估算：按字符数折算（中文约 1.5 char/token，取 2 char/token 保守）
    messages = body.get("messages") or []
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    return max(1, total_chars // 2)
