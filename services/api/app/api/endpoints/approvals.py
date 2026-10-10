"""审批决策端点（``api/endpoints/approvals.py``，``02`` §4.4）。

- POST /api/v1/approvals/{approval_id}/decision：提交人工审批决策。
- GET  /api/v1/conversations/{id}/approvals：待决列表（已在 conversations.py，保持兼容）。

**审批只有一条生效路径**（``00`` §8）：内核发起的工具审批走
``kernel/inbound/approval_endpoint.py`` → ``governance/approval.py``；本端点是
**人工决策的入口**（把 pending 单据变成 approved/rejected 并唤醒阻塞中的工具调用），
不是第二条审批机制。

鉴权：与其它 ``/api/v1/*`` 端点一致（Bearer JWT → RequestContext，``get_ctx``）；
越权/重复决策由 ``governance/approval.py::decide`` 抛 Forbidden / NotFound。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


class DecisionRequest(BaseModel):
    """审批决策请求体（``02`` §4.4：``{approved, reason}``）。"""

    approved: bool
    reason: str = ""


@router.post("/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    body: DecisionRequest,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """提交审批决策（通过 / 拒绝）。

    决策成功后会唤醒阻塞在 ``governance/approval.request()`` 的工具调用；
    该单据若已决策或已超时，``decide()`` 抛 Forbidden（重复决策被拒）。
    """
    from app.governance import approval as approval_mod

    await approval_mod.decide(
        ctx, approval_id, approved=body.approved, reason=body.reason
    )
    return {
        "ok": True,
        "approval_id": approval_id,
        "approved": body.approved,
        "status": "approved" if body.approved else "rejected",
    }
