"""审计（``governance/audit.py``，``00`` §8 / ``01`` §4.7 / ``02`` §2.5 集合 ``audit_logs``）。

审计字段（``02`` §2.5）：actor_user_id / action / resource_type / resource_id /
conversation_id / session_id / turn_request_id / profile_version / device_id /
tool_name / execution_location / approval_id / approver_user_id / result /
latency_ms / error_code / trace_id / occurred_at。

**写操作必须落审计，不接受"这个操作不重要所以不审计"**（``02`` §1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core import mongo
from app.core.clock import utcnow
from app.identity.context import RequestContext

AUDIT_COLLECTION = "audit_logs"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """一条待落库的审计事件。"""

    actor_user_id: str | None  # 系统动作为 None
    action: str  # 如 tool.execute / model.generate / device.revoke
    org_unit_id: str = ""
    resource_type: str | None = None
    resource_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    turn_request_id: str | None = None
    profile_version: str | None = None
    device_id: str | None = None
    tool_name: str | None = None
    execution_location: str | None = None  # server / local
    approval_id: str | None = None
    approver_user_id: str | None = None
    result: str = "ok"  # ok / error / denied
    latency_ms: int | None = None
    error_code: str | None = None
    trace_id: str = ""
    occurred_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)


async def record(event: AuditEvent) -> None:
    """落库一条审计事件。"""
    coll = mongo.get_collection(AUDIT_COLLECTION)
    doc = {
        "org_unit_id": event.org_unit_id,
        "actor_user_id": event.actor_user_id,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "conversation_id": event.conversation_id,
        "session_id": event.session_id,
        "turn_request_id": event.turn_request_id,
        "profile_version": event.profile_version,
        "device_id": event.device_id,
        "tool_name": event.tool_name,
        "execution_location": event.execution_location,
        "approval_id": event.approval_id,
        "approver_user_id": event.approver_user_id,
        "result": event.result,
        "latency_ms": event.latency_ms,
        "error_code": event.error_code,
        "trace_id": event.trace_id,
        "occurred_at": event.occurred_at or utcnow(),
        "details": event.details,
    }
    await coll.insert_one(doc)


@dataclass(frozen=True, slots=True)
class AuditFilters:
    """审计查询过滤条件。"""

    action: str | None = None
    actor_user_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True, slots=True)
class PagedResult:
    """分页结果。"""

    items: list[dict[str, Any]]
    total: int


async def query(
    ctx: RequestContext,
    *,
    filters: AuditFilters,
    page: dict[str, Any],
) -> PagedResult:
    """查询审计记录（按 org 隔离 + 过滤条件 + 分页）。"""
    coll = mongo.get_collection(AUDIT_COLLECTION)
    query: dict[str, Any] = {"org_unit_id": ctx.org_unit_id}
    if filters.action:
        query["action"] = filters.action
    if filters.actor_user_id:
        query["actor_user_id"] = filters.actor_user_id
    if filters.conversation_id:
        query["conversation_id"] = filters.conversation_id
    if filters.session_id:
        query["session_id"] = filters.session_id
    if filters.tool_name:
        query["tool_name"] = filters.tool_name

    limit = int(page.get("limit") or 50)
    offset = int(page.get("offset") or 0)

    cursor = coll.find(query).sort("occurred_at", -1).skip(offset).limit(limit)
    rows = await cursor.to_list(length=limit)
    total = await coll.count_documents(query)
    return PagedResult(items=rows, total=total)
