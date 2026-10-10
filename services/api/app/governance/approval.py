"""审批（``governance/approval.py``，``00`` §8 / ``01`` §4.7）。

**审批只保留一条生效路径**（``00`` §8）：内核发起的工具审批走 ``inbound/approval_endpoint.py``，
本模块是其落地实现。阻塞等待人工决策，**超时抛 ``ApprovalTimeout``（勿静默放行）**。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core import mongo
from app.core.clock import utcnow
from app.core.errors import ApprovalTimeout, Forbidden, NotFound
from app.identity.context import RequestContext

APPROVALS_COLLECTION = "approvals"
#: 本轮等待结果的事件（进程内，供 decide 唤醒 waiter）
_approval_events: dict[str, asyncio.Event] = {}
_approval_ready: dict[str, bool] = {}


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """一次审批决策结果。"""

    approval_id: str
    approved: bool
    reason: str = ""


def _arguments_digest(arguments: dict[str, Any]) -> str:
    raw = str(arguments)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def request(
    ctx: RequestContext,
    *,
    conversation_id: str,
    tool_name: str,
    args: dict[str, Any],
    call_id: str,
    timeout_seconds: int,
    session_id: str | None = None,
) -> ApprovalDecision:
    """发起一次工具审批并阻塞等待人工决策。

    参数
    ----
    ctx:
        发起者上下文（requester）。
    conversation_id / tool_name / args / call_id:
        turn 定位与工具调用详情（arguments 全量入库供审计，``02`` §2.5）。
    timeout_seconds:
        等待上限。超时抛 ``ApprovalTimeout``（不静默放行）。

    返回 ApprovalDecision。
    """
    coll = mongo.get_collection(APPROVALS_COLLECTION)
    digest = _arguments_digest(args)
    now = utcnow()
    approval_doc = {
        "org_unit_id": ctx.org_unit_id,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": args,
        "arguments_digest": digest,
        "requester_user_id": ctx.user_id,
        "status": "pending",
        "decided_by": None,
        "decided_at": None,
        "reason": None,
        "expires_at": _add_seconds(now, timeout_seconds),
        "created_at": now,
    }
    result = await coll.insert_one(approval_doc)
    approval_id = str(result.inserted_id)

    # 建立等待事件
    event = asyncio.Event()
    key = _event_key(ctx.org_unit_id, approval_id)
    _approval_events[key] = event
    _approval_ready[key] = False

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        # 超时：标记为 timeout（勿静默放行）
        await coll.update_one(
            {"_id": result.inserted_id},
            {"$set": {"status": "timeout", "decided_at": utcnow(), "reason": "approval_timeout"}},
        )
        raise ApprovalTimeout(
            f"审批超时: {tool_name}", details={"approval_id": approval_id}
        ) from exc
    finally:
        _approval_events.pop(key, None)
        _approval_ready.pop(key, None)

    # 读取最终决策
    doc = await coll.find_one({"_id": result.inserted_id})
    status = doc.get("status") if doc else "cancelled"
    approved = status == "approved"
    return ApprovalDecision(
        approval_id=approval_id,
        approved=approved,
        reason=str(doc.get("reason") or "") if doc else "",
    )


async def decide(
    ctx: RequestContext,
    approval_id: str,
    *,
    approved: bool,
    reason: str,
) -> None:
    """提交审批决策（人在环）。"""
    from bson import ObjectId

    coll = mongo.get_collection(APPROVALS_COLLECTION)
    doc = await coll.find_one({"_id": ObjectId(approval_id), "org_unit_id": ctx.org_unit_id})
    if doc is None:
        raise NotFound("审批单不存在")
    if doc.get("status") != "pending":
        raise Forbidden("该审批单已决策或已超时")

    status = "approved" if approved else "rejected"
    await coll.update_one(
        {"_id": ObjectId(approval_id)},
        {
            "$set": {
                "status": status,
                "decided_by": ctx.user_id,
                "decided_at": utcnow(),
                "reason": reason,
            }
        },
    )
    # 唤醒等待方
    key = _event_key(ctx.org_unit_id, approval_id)
    event = _approval_events.get(key)
    if event is not None:
        _approval_ready[key] = True
        event.set()


def _event_key(org_unit_id: str, approval_id: str) -> str:
    return f"{org_unit_id}:{approval_id}"


def _add_seconds(dt: datetime, seconds: int) -> datetime:
    from datetime import timedelta

    return dt + timedelta(seconds=seconds)
