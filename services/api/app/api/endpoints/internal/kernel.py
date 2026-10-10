"""内部内核回调端点（``api/endpoints/internal/kernel.py``，``02`` §4.9）。

路由（HMAC 鉴权，独立前缀 + 独立中间件；nginx 层 location /internal/ { deny all; }）：

- POST /internal/kernel/model/generate：模型推理（流式 NDJSON）。
- POST /internal/kernel/tools/execute：工具执行。
- POST /internal/kernel/tools/approval：审批请求。
- GET  /internal/kernel/health：宿主探活（也走 HMAC）。

这些端点是内核唯一的出口，不出现在公开路由表里（``00`` §4.1）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.logging import get_logger

log = get_logger("api.internal.kernel")

router = APIRouter(prefix="/internal/kernel", tags=["internal-kernel"])


@router.post("/model/generate")
async def model_generate(request: Request) -> StreamingResponse:
    """模型推理回调：注入品牌上下文并转发给真实模型提供商，返回流式 NDJSON。

    由 HmacAuthMiddleware 校验；``request.state.kernel_claims`` 是核验后的声明。
    响应体为 ``application/x-ndjson``：每行一个 DSH StreamChunk，供 DSH 内核
    的 ``GatewayLlmAdapter.stream()`` 逐行消费。

    配额预检在构造流式响应**之前**（``S-44``）——流式响应一旦开始返回，
    200 已发出，此时超额只能截断流而非 429；预检超额 → 429 JSON + 审计落库。
    """
    from app.kernel.inbound.model_endpoint import generate_stream, precheck_quota_or_reject

    claims = request.state.kernel_claims
    body = await request.json()
    await precheck_quota_or_reject(claims, body)
    return StreamingResponse(
        generate_stream(claims, body),
        media_type="application/x-ndjson",
    )


@router.post("/tools/execute")
async def tool_execute(request: Request) -> dict[str, Any]:
    """工具执行回调。"""
    from app.kernel.inbound.tool_endpoint import handle_tool_execute

    claims = request.state.kernel_claims
    body = await request.json()
    return await handle_tool_execute(claims, body)


@router.post("/tools/approval")
async def tool_approval(request: Request) -> dict[str, Any]:
    """审批请求回调（人在环）。"""
    from app.kernel.inbound.approval_endpoint import handle_approval_request

    claims = request.state.kernel_claims
    body = await request.json()
    return await handle_approval_request(claims, body)


@router.get("/health")
async def kernel_health(request: Request) -> dict[str, Any]:
    """宿主探活（也走 HMAC 校验）。"""
    return {"ok": True, "kernel": "dsh"}
