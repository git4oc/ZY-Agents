"""模型适配（``kernel/dsh/adapters/models.py``）。

宿主 HTTP 协议（``zy.dsh-host.v1``，``03`` §5）与契约模型之间的转换。
上游请求/响应字段映射变化只改这里。
"""

from __future__ import annotations

from typing import Any

from app.kernel.contract.commands import ContentBlock, SendRequest, SessionSpec
from app.kernel.contract.handles import RuntimeHandle, SessionHandle, SessionStatus


def runtime_handle_from_host(body: dict[str, Any]) -> RuntimeHandle:
    """把宿主返回的 runtime 描述转为契约 RuntimeHandle。

    宿主可能返回 ``{ok: true, runtime: {...}}`` 包裹结构，也可能直接返回 runtime
    对象——此处统一解包 ``runtime`` 键（若无则视 body 本身为 runtime 描述）。
    """
    inner = body.get("runtime") if isinstance(body, dict) and isinstance(body.get("runtime"), dict) else body
    return RuntimeHandle(
        runtime_id=str(
            inner.get("runtimeId") or inner.get("runtime_id") or inner.get("id") or ""
        ),
        kernel="dsh",
        kernel_version=str(
            inner.get("kernelVersion")
            or inner.get("kernel_version")
            or inner.get("version")
            or ""
        ),
        profile_version=str(
            inner.get("profileVersion") or inner.get("profile_version") or ""
        ),
        model_instance_id=inner.get("modelInstanceId") or inner.get("model_instance_id"),
        isolation_key=str(inner.get("isolationKey") or inner.get("isolation_key") or ""),
    )


def session_spec_to_host(spec: SessionSpec) -> dict[str, Any]:
    """把契约 SessionSpec 转为宿主建会话请求体。"""
    return {
        "conversationId": spec.conversation_id,
        "orgUnitId": spec.org_unit_id,
        "userId": spec.user_id,
        "profileVersion": spec.profile_version,
        "presetId": spec.preset_id,
        "executionLocation": spec.execution_location,
        "metadata": spec.metadata,
    }


def session_handle_from_host(body: dict[str, Any]) -> SessionHandle:
    """把宿主返回的会话描述转为契约 SessionHandle。

    宿主可能返回 ``{ok: true, session: {...}}`` 包裹结构，也可能直接返回 session
    对象——此处统一解包 ``session`` 键（若无则视 body 本身为会话描述）。
    """
    inner = body.get("session") if isinstance(body, dict) and isinstance(body.get("session"), dict) else body
    status_raw = str(inner.get("status") or "creating")
    try:
        status = SessionStatus(status_raw)
    except ValueError:
        status = SessionStatus.CREATING
    return SessionHandle(
        runtime_id=str(inner.get("runtimeId") or inner.get("runtime_id") or ""),
        session_id=str(inner.get("sessionId") or inner.get("session_id") or inner.get("id") or ""),
        conversation_id=str(inner.get("conversationId") or inner.get("conversation_id") or ""),
        profile_version=str(inner.get("profileVersion") or inner.get("profile_version") or ""),
        preset_id=str(inner.get("presetId") or inner.get("preset_id") or "zy-enterprise"),
        status=status,
    )


def send_request_to_host(request: SendRequest) -> dict[str, Any]:
    """把契约 SendRequest 转为宿主 send 请求体。"""
    return {
        "requestId": request.request_id,
        "mode": request.mode.value if hasattr(request.mode, "value") else request.mode,
        "content": [_content_block_to_host(c) for c in request.content],
        "temporalContext": _temporal_context_to_host(request.temporal_context),
        "turnContext": request.turn_context,
    }


def _content_block_to_host(block: ContentBlock) -> dict[str, Any]:
    return {"type": block.type, "data": block.data}


def _temporal_context_to_host(tc: Any) -> dict[str, Any] | None:
    if tc is None:
        return None
    return {
        "capturedAtUtc": tc.captured_at_utc.isoformat() if hasattr(tc.captured_at_utc, "isoformat") else str(tc.captured_at_utc),
        "userLocalTime": tc.user_local_time.isoformat() if hasattr(tc.user_local_time, "isoformat") else str(tc.user_local_time),
        "userTimezone": tc.user_timezone,
    }
