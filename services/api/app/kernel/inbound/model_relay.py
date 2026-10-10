"""模型提供商转发适配层（``kernel/inbound/model_relay.py``，任务 #13）。

当 DSH 内核经网关回调 ``/internal/kernel/model/generate`` 时，本模块把请求体转发给
真实的 OpenAI 兼容 LLM 提供商（``chat/completions`` 流式），把流式 SSE 响应翻译成
DSH 内核消费的 **StreamChunk NDJSON** 逐行吐回（``block-start`` / ``text-delta`` /
``reasoning-delta`` / ``tool-call-delta`` / ``block-end`` / ``usage`` / ``finish``）。

分层纪律（``docs/01 §3.1``）：本模块属于 ``app.kernel.inbound.*``，可 import
``app.core.*`` 与 ``app.settings``，但**禁止** import ``app.collaboration`` 与
``app.kernel.dsh``。为此本模块保持**零 DB / 零 DSH 依赖**（只用 stdlib + pydantic +
``httpx``），测试亦无需桩。

key 纪律：``model_provider_api_key`` 只进 ``.env``，绝不进库 / 日志 / 进程内存持久化。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from app.settings import Settings, get_settings

#: DSH 文本/推理块索引（本适配层只处理单一内容块序列）。
_TEXT_INDEX = 0


def _settings() -> Settings:
    """返回进程级缓存的 Settings 单例。"""
    return get_settings()


def _is_configured(base_url: str, api_key: str) -> bool:
    """纯判断：提供商基址与 key 是否均已配置（供测试直接调用）。"""
    return bool(base_url and api_key)


def enabled() -> bool:
    """提供商转发是否启用（基址与 key 均已配置）。"""
    s = _settings()
    return _is_configured(s.model_provider_base_url, s.model_provider_api_key)


class RelayNotConfiguredError(RuntimeError):
    """提供商未配置时抛出，提示管理员补齐 ``.env``。"""

    def __init__(self) -> None:
        super().__init__(
            "模型提供商未配置：请在 .env 设置 MODEL_PROVIDER_BASE_URL / "
            "MODEL_PROVIDER_API_KEY（#13 provider 适配层）"
        )


def _ensure_enabled() -> None:
    """未启用则抛 ``RelayNotConfiguredError``。"""
    if not enabled():
        raise RelayNotConfiguredError()


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文/混合内容约 2 字符/token，取保守值。"""
    return max(1, len(text) // 2)


def _count_message_tokens(messages: list[dict[str, Any]]) -> int:
    """估算整个 messages 列表的 token 数（用于 context shrink 预算判断）。"""
    return sum(_estimate_tokens(str(m.get("content", ""))) for m in messages)


def _shrink_messages(
    messages: list[dict[str, Any]], budget_tokens: int
) -> list[dict[str, Any]]:
    """上下文裁剪：在 token 预算内尽量保住「系统块 + 最近对话」。

    策略（确定性、最小改动，按序执行）：
      1. 从预算中扣除输出预留（``model_provider_max_output_tokens``）得 prompt 预算；
      2. 恒保留 messages[0]（引导 system/品牌块）与所有 ``role=='system'`` 的消息；
         对 0 号之后非 system 的历史消息，按「最老的先丢」裁剪，直到估算 prompt 装下；
         至少保留 messages[0] + 最后一条 user 消息（保证还有问题可答）；
      3. 兜底：若「最后 user + 系统块」仍超预算，则按字符比例截断最后 user 内容。
    绝不返回空列表；无需裁剪时原样返回（不做多余拷贝）。
    """
    if not messages:
        return messages

    settings = _settings()
    max_output = settings.model_provider_max_output_tokens
    prompt_budget = max(1, budget_tokens - max_output)

    # 无需裁剪：整个列表已装下 → 原样返回（不拷贝）
    if _count_message_tokens(messages) <= prompt_budget:
        return messages

    # 恒保留的系统部分：messages[0] + 0 号之后所有 system
    head = [messages[0]]
    kept_system = [m for i, m in enumerate(messages) if i > 0 and m.get("role") == "system"]
    keeps: list[dict[str, Any]] = head + kept_system

    # 0 号之后的非 system 消息（旧的在前）
    rest = [m for i, m in enumerate(messages) if i > 0 and m.get("role") != "system"]

    # 最后一条 user（优先级最高，保证有可答问题）
    last_user = next((m for m in reversed(rest) if m.get("role") == "user"), None)
    last = last_user if last_user is not None else (rest[-1] if rest else None)

    budget_left = prompt_budget - _count_message_tokens(keeps)
    selected: list[dict[str, Any]] = []
    if last is not None and _estimate_tokens(str(last.get("content", ""))) <= budget_left:
        selected.append(last)
        budget_left -= _estimate_tokens(str(last.get("content", "")))
        # 从最新往前填充更多历史（保留近因）
        for m in reversed(rest):
            if m is last:
                continue
            t = _estimate_tokens(str(m.get("content", "")))
            if t <= budget_left:
                selected.append(m)
                budget_left -= t
            else:
                break
    selected.reverse()  # 保持时间正序（旧在前，最近的 user 在末尾）

    trimmed = keeps + selected if selected else keeps
    # 若仍无任何 user 内容 → 兜底：追加最后一条并截断其 content 到预算
    if not any(m.get("role") == "user" for m in trimmed):
        if last is not None:
            leftover = prompt_budget - _count_message_tokens(keeps)
            if leftover >= 1:
                content = str(last.get("content", ""))
                cut = max(1, leftover * 2)  # 字符≈2×token
                last_copy = dict(last)
                last_copy["content"] = content[:cut]
                trimmed = trimmed + [last_copy]
    return trimmed or [messages[0]]


def _provider_url() -> str:
    """构造提供商 ``chat/completions`` 完整 URL。"""
    s = _settings()
    base = s.model_provider_base_url.rstrip("/")
    return f"{base}/chat/completions"


def _build_provider_request(
    request_body: dict[str, Any], messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """把 DSH 回传体映射为 OpenAI 兼容 ``chat/completions`` 请求体。"""
    s = _settings()
    model = s.model_provider_model or request_body.get("model")
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    for key in ("temperature", "top_p", "stop"):
        if request_body.get(key) is not None:
            body[key] = request_body[key]
    max_tokens = request_body.get("max_tokens")
    body["max_tokens"] = max_tokens if max_tokens else s.model_provider_max_output_tokens
    if request_body.get("tools") is not None:
        body["tools"] = request_body["tools"]
    if request_body.get("tool_choice") is not None:
        body["tool_choice"] = request_body["tool_choice"]
    return body


def _parse_sse_line(line: str) -> Any:
    """解析一行 SSE/NDJSON：返回 dict 或 ``[DONE]``；畸形行返回 None 跳过。"""
    trimmed = line.strip()
    if not trimmed:
        return None
    if trimmed.startswith("data:"):
        data = trimmed[len("data:"):].strip()
        if data == "[DONE]":
            return "[DONE]"
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        return None


def _map_finish_reason(reason: Any) -> str:
    """OpenAI finish_reason → DSH finish reason。"""
    r = str(reason or "stop").lower()
    if r == "stop":
        return "stop"
    if r in ("tool_calls", "tool_call"):
        return "tool_use"
    if r == "end_turn":
        return "end_turn"
    if r == "length":
        return "length"
    if r == "error":
        return "error"
    if r == "aborted":
        return "aborted"
    return r or "stop"


def translate_sse_chunk(parsed: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    """把一条 OpenAI 流式 chunk 翻译成一串 DSH StreamChunk dict。

    ``state`` 是可变累积字典，跨 chunk 保存：
    ``started`` / ``block_ended`` / ``finished`` / ``text_buffer`` /
    ``tool_buffers`` / ``usage`` / ``last_reason`` / ``any_tool``。

    返回本次要 yield 的 DSH chunk 列表；never 重复发 block-start / block-end。
    """
    out: list[dict[str, Any]] = []

    # usage（可能出现在任意块，通常最后一块）——必须先于 finish 发出
    usage = parsed.get("usage")
    if usage and not state.get("usage_seen"):
        state["usage_seen"] = True
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        total = usage.get("total_tokens") or (prompt + completion)
        out.append(
            {
                "type": "usage",
                "usage": {
                    "inputTokens": prompt,
                    "outputTokens": completion,
                    "totalTokens": total,
                },
            }
        )
        state["usage"] = usage

    choices = parsed.get("choices") or []
    choice = choices[0] if choices else {}
    delta = choice.get("delta") or {}

    # 首块：只发一次 block-start
    if not state.get("started"):
        state["started"] = True
        out.append({"type": "block-start", "index": _TEXT_INDEX, "blockType": "text"})

    # reasoning 内容（兼容 reasoning_content / reasoning）
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        out.append({"type": "reasoning-delta", "index": _TEXT_INDEX, "text": reasoning})

    # 文本内容
    content = delta.get("content")
    if isinstance(content, str) and content:
        state["text_buffer"] += content
        out.append({"type": "text-delta", "index": _TEXT_INDEX, "text": content})

    # 工具调用
    tool_calls = delta.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            t_index = tc.get("index", 0)
            fn = tc.get("function") or {}
            t_id = tc.get("id") or ""
            fn_name = fn.get("name") or ""
            args_delta = fn.get("arguments") or ""
            if not t_id and not fn_name and not args_delta:
                continue
            state["any_tool"] = True
            buf = state.setdefault("tool_buffers", {}).setdefault(
                t_index, {"id": "", "name": "", "arguments": ""}
            )
            if t_id:
                buf["id"] = t_id
            if fn_name:
                buf["name"] = fn_name
            if args_delta:
                buf["arguments"] += args_delta
            out.append(
                {
                    "type": "tool-call-delta",
                    "index": t_index,
                    "id": buf["id"] or f"call_{t_index}",
                    **({"name": buf["name"]} if buf["name"] else {}),
                    "argumentsDelta": args_delta or "",
                }
            )

    # finish_reason
    finish_reason = choice.get("finish_reason")
    if finish_reason and not state.get("finished"):
        state["last_reason"] = _map_finish_reason(finish_reason)
        out.extend(_emit_block_end(state))
        out.append({"type": "finish", "reason": state["last_reason"]})
        state["finished"] = True

    return out


def _emit_block_end(state: dict[str, Any]) -> list[dict[str, Any]]:
    """按累积结果发出 block-end；同一状态只发一次（守卫 block_ended）。"""
    if state.get("block_ended"):
        return []
    state["block_ended"] = True
    out: list[dict[str, Any]] = []
    if state.get("any_tool"):
        for ti, buf in state.get("tool_buffers", {}).items():
            out.append(
                {
                    "type": "block-end",
                    "index": ti,
                    "block": {
                        "type": "tool-call",
                        "id": buf["id"] or f"call_{ti}",
                        "name": buf["name"] or "",
                        "arguments": buf["arguments"] or "",
                    },
                }
            )
    else:
        out.append(
            {
                "type": "block-end",
                "index": _TEXT_INDEX,
                "block": {
                    "type": "text",
                    "text": state.get("text_buffer", ""),
                },
            }
        )
    return out


async def stream_to_provider(
    request_body: dict[str, Any], messages: list[dict[str, Any]]
) -> AsyncIterator[dict[str, Any]]:
    """核心 async 生成器：转发给提供商，逐行翻译成 DSH StreamChunk 并 yield。

    - 未配置：yield 单个 ``finish/error``（而非抛异常，保持 NDJSON 流有效）。
    - 非 2xx：yield 单个 ``finish/error``，并在最后补 finish 收尾。
    - 逐行解析 SSE 并翻译；流自然耗尽时补齐 block-end + finish。
    """
    import httpx

    try:
        _ensure_enabled()
    except RelayNotConfiguredError:
        yield {"type": "finish", "reason": "error"}
        return

    s = _settings()
    url = _provider_url()
    payload = _build_provider_request(request_body, messages)
    headers = {
        "Authorization": f"Bearer {s.model_provider_api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    timeout = httpx.Timeout(s.model_provider_timeout_seconds)

    state: dict[str, Any] = {
        "started": False,
        "block_ended": False,
        "finished": False,
        "text_buffer": "",
        "tool_buffers": {},
        "usage": None,
        "usage_seen": False,
        "last_reason": "stop",
        "any_tool": False,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code < 200 or resp.status_code >= 300:
                    await resp.aread()
                    yield {"type": "finish", "reason": "error"}
                    return
                async for line in resp.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk is None:
                        continue
                    if chunk == "[DONE]":
                        if not state.get("finished"):
                            out = _emit_block_end(state)
                            for c in out:
                                yield c
                            yield {"type": "finish", "reason": state["last_reason"]}
                            state["finished"] = True
                        return
                    for c in translate_sse_chunk(chunk, state):
                        yield c
                    if state.get("finished"):
                        return
                # 流自然耗尽未 finish：补齐收尾（reason 取决于是否异常前正常）
                if not state.get("finished"):
                    for c in _emit_block_end(state):
                        yield c
                    yield {"type": "finish", "reason": state["last_reason"]}
    except Exception:  # pragma: no cover - 网络/超时异常兜底
        yield {"type": "finish", "reason": "error"}
