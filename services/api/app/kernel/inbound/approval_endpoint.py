"""审批网关端点（``kernel/inbound/approval_endpoint.py``，``00`` §8 / ``03`` §4.3）。

``/internal/kernel/tools/approval``：内核发起工具审批（人在环）。

**审批只保留一条生效路径**（``00`` §8）：内核发起的工具审批走本端点。
不要再挂一套平行的自研审批运行时（历史上出现过这类全仓无引用的死代码）。
一个机制两条路径，半年后没人说得清走哪条。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import GatewayTokenError
from app.core.logging import get_logger
from app.governance import approval as approval_mod
from app.kernel.inbound.credentials import AUD_TOOL, GatewayClaims

log = get_logger("kernel.inbound.approval_endpoint")


async def handle_approval_request(
    claims: GatewayClaims,
    request_body: dict[str, Any],
) -> dict[str, Any]:
    """处理一次工具审批请求（内核经工具网关/token 反向调用）。

    参数
    ----
    claims:
        已校验的网关声明（aud=tool）。
    request_body:
        含 ``tool_name`` / ``arguments`` / ``conversation_id`` / ``call_id`` / ``timeout_seconds``。

    返回审批决策（或 "waiting" 表示已创建审批单等待人工决策）。
    """
    if claims.aud != AUD_TOOL:
        raise GatewayTokenError("审批端点要求 tool token", details={"got": claims.aud})

    tool_name = request_body.get("tool_name") or ""
    arguments = request_body.get("arguments") or {}
    conversation_id = request_body.get("conversation_id")
    call_id = request_body.get("call_id") or ""
    timeout_seconds = int(request_body.get("timeout_seconds") or 120)
    session_id = request_body.get("session_id")

    from app.identity.context import RequestContext

    ctx = RequestContext(
        user_id=claims.user_id,
        org_unit_id=claims.org_unit_id,
        roles=frozenset(claims.scopes),
        device_id=claims.runtime_id or None,
    )

    try:
        decision = await approval_mod.request(
            ctx,
            conversation_id=conversation_id,
            tool_name=tool_name,
            args=arguments,
            call_id=call_id,
            timeout_seconds=timeout_seconds,
            session_id=session_id,
        )
    except approval_mod.ApprovalTimeout as exc:
        return {
            "ok": False,
            "decision": "timeout",
            "reason": str(exc),
            "receipt": {"call_id": call_id, "tool_name": tool_name},
        }

    return {
        "ok": True,
        "decision": "approved" if decision.approved else "rejected",
        "approval_id": decision.approval_id,
        "reason": decision.reason,
        "receipt": {"call_id": call_id, "tool_name": tool_name},
    }
