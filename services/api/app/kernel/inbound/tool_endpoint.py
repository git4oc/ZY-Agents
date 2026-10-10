"""工具网关端点（``kernel/inbound/tool_endpoint.py``，``00`` §4.4 / ``03`` §4.3）。

``/internal/kernel/tools/execute``：内核决定调用工具时反向回调主服务。

**S-17 五步链**（本模块按此顺序实现，缺一不可）::

    1) 准入      Profile 是否声明该工具？用户是否有资源权限？（fail-closed）
    2) 审批      approval_required → governance/approval.py 阻塞等待；不静默放行
    3) 分类派发  server → capability/tools；local → 本地 Agent；hybrid → 先服务端
    4) 效果核验  按 output_schema 校验响应结构，三态判定（ok / failed / unverified）
    5) 审计      audit_logs 落库（参数、结果、耗时、审批人、执行位置）

**事件化**：步骤 3/4 的关键节点经 ``kernel/events.py`` 的注入 sink 写 ``kernel_events``，
前端 SSE 才能看到"正在调什么工具、成没成、批没批"。类型沿用 ``02`` §3 已声明的
``tool.call.requested`` / ``tool.call.completed``，新增 ``tool.call.failed`` 与
``tool.approval.*``（已同步登记进 ``collaboration/sensitivity.py``）。

**分层**：本模块位于 ``kernel/``，**禁止** import ``collaboration/``（``01`` §3）。
事件落库经 ``kernel.events`` 回调注入的 sink（装配期由 main.py 接
``collaboration.events.persist_envelope``），与 ``kernel/turn/driver.py`` 同一模式。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.clock import utcnow
from app.core.errors import ApprovalTimeout, GatewayTokenError
from app.core.logging import get_logger
from app.kernel.events import emit as emit_event
from app.kernel.inbound.credentials import AUD_TOOL, GatewayClaims

log = get_logger("kernel.inbound.tool_endpoint")

#: 审批默认等待上限（秒）——工具定义未声明 approval_timeout 时的兜底
_DEFAULT_APPROVAL_TIMEOUT = 120
#: 效果核验：结果摘要的最大字符数（事件里只放摘要，不放完整结果——完整结果是 secret）
_RESULT_SUMMARY_MAX = 200


async def handle_tool_execute(
    claims: GatewayClaims,
    request_body: dict[str, Any],
) -> dict[str, Any]:
    """处理一次工具执行回调（S-17 五步链）。

    参数
    ----
    claims:
        已校验的网关声明（aud=tool）。
    request_body:
        含 ``tool_name`` / ``arguments`` / ``conversation_id`` / ``call_id`` 等。

    返回执行回执 ``{"ok", "result"|"error", "receipt"}``。
    """
    if claims.aud != AUD_TOOL:
        raise GatewayTokenError("工具端点要求 tool token", details={"got": claims.aud})

    tool_name = request_body.get("tool_name") or request_body.get("name")
    arguments = request_body.get("arguments") or {}
    conversation_id = request_body.get("conversation_id")
    call_id = request_body.get("call_id") or ""
    session_id = request_body.get("session_id")

    if not tool_name:
        raise GatewayTokenError("缺少 tool_name")

    from app.identity.context import RequestContext

    ctx = RequestContext(
        user_id=claims.user_id,
        org_unit_id=claims.org_unit_id,
        roles=frozenset(claims.scopes),
        device_id=claims.runtime_id or None,
    )

    from app.capability import registry as tool_registry

    tool = tool_registry.get(tool_name)

    # ---- 步骤 1：准入（fail-closed）----
    admission = await _admit(ctx, tool_name=tool_name, tool=tool, call_id=call_id)
    if admission is not None:
        return admission  # 准入失败，已返回结构化错误

    # ---- 步骤 2：审批（不静默放行）----
    if await _resolve_approval_required(ctx.org_unit_id, tool_name, tool):
        gate = await _approval_gate(
            ctx,
            claims=claims,
            tool_name=tool_name,
            arguments=arguments,
            call_id=call_id,
            conversation_id=conversation_id,
            session_id=session_id,
        )
        if gate is not None:
            return gate  # 拒绝 / 超时 → 已返回，且未执行工具

    # ---- 步骤 3：分类派发（执行前后写事件）----
    await _emit_tool_event(
        claims=claims,
        conversation_id=conversation_id,
        session_id=session_id,
        event_type="tool.call.requested",
        payload={
            "tool_name": tool_name,
            "arguments": arguments,
            "call_id": call_id,
            "category": getattr(tool, "category", "unknown"),
        },
    )

    started = time.monotonic()
    result = await _dispatch(
        tool_name, arguments, call_id, claims, conversation_id, session_id
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    # ---- 步骤 4：效果核验（三态判定，失败也落事件）----
    verdict = _verify_result(tool, result)
    if result.get("ok"):
        # ★ status 必须反映核验结果（P1-2）：核验失败/不确定时不能报 "ok"，
        # 否则 API 消费者按 status 判定成功就会误判。两个字段由 _status_for 统一派生，
        # 结构上杜绝"status=ok 与 verified=failed 并存"的自相矛盾。
        await _emit_tool_event(
            claims=claims,
            conversation_id=conversation_id,
            session_id=session_id,
            event_type="tool.call.completed",
            payload={
                "tool_name": tool_name,
                "call_id": call_id,
                "status": _status_for(verdict),
                "latency_ms": latency_ms,
                "result": result.get("result"),
                "result_summary": _summarize(result.get("result")),
                "verified": verdict,
            },
        )
    else:
        err = result.get("error") or {}
        await _emit_tool_event(
            claims=claims,
            conversation_id=conversation_id,
            session_id=session_id,
            event_type="tool.call.failed",
            payload={
                "tool_name": tool_name,
                "call_id": call_id,
                "code": err.get("code", "tool_execution_error"),
                "message": err.get("message", ""),
                "status": "error",
                "latency_ms": latency_ms,
                "verified": verdict,
            },
        )

    # ---- 步骤 5：审计落库 ----
    from app.governance import audit as audit_mod

    await audit_mod.record(
        audit_mod.AuditEvent(
            actor_user_id=claims.user_id,
            org_unit_id=claims.org_unit_id,
            action="tool.execute",
            resource_type="tool",
            resource_id=tool_name,
            conversation_id=conversation_id,
            session_id=session_id,
            profile_version=claims.profile_version,
            device_id=claims.runtime_id or None,
            tool_name=tool_name,
            execution_location=(result.get("receipt") or {}).get("location", "server"),
            result="ok" if result.get("ok") else "error",
            latency_ms=latency_ms,
            error_code=None if result.get("ok") else (result.get("error") or {}).get("code"),
            trace_id="",
            occurred_at=utcnow(),
            details={"verified": verdict},
        )
    )

    return result


# ==========================================================================
# 步骤 1：准入
# ==========================================================================


async def _admit(
    ctx: Any,
    *,
    tool_name: str,
    tool: Any,
    call_id: str,
) -> dict[str, Any] | None:
    """准入：ACL 资源权限（fail-closed）。失败返回错误回执，通过返回 None。"""
    from app.core.errors import Forbidden
    from app.identity import acl

    if tool is not None and getattr(tool, "category", None) == "server":
        try:
            await acl.ensure(ctx, resource="tool", resource_id=tool_name, action="use")
        except Forbidden as exc:
            return _error_receipt("forbidden", str(exc), call_id, tool_name)
    return None


# ==========================================================================
# 步骤 2：审批
# ==========================================================================


async def _resolve_approval_required(
    org_unit_id: str, tool_name: str, tool: Any
) -> bool:
    """判定该工具是否需要人工审批。

    **依据来源**（不臆造字段名）：``approval_required`` 是工具声明的既有字段，
    两处一致——

    1. ``kernel/profile/models.py`` 的 ``ProfileTool.approval_required: bool``，
       由 ``kernel/profile/tools.py::compile_tool`` 从 ``tools`` 集合文档的
       ``approval_required`` 读入（``02`` §2.3 集合定义）；
    2. ``capability/base.py`` 的 ``ServerTool.approval_required``（进程内已注册实现）。

    取值优先级：进程内注册的实现（权威、免 IO）→ ``tools`` 集合文档（覆盖 local/hybrid
    等没有服务端实现的工具）。两处都缺省 **False**（不因缺字段而误拦），
    但只要任一处显式为 True 即需审批。
    """
    flag = getattr(tool, "approval_required", None)
    if isinstance(flag, bool):
        return flag

    from app.core import mongo

    doc = await mongo.get_collection("tools").find_one(
        {"org_unit_id": org_unit_id, "name": tool_name},
        {"approval_required": 1},
    )
    return bool((doc or {}).get("approval_required", False))


async def _approval_gate(
    ctx: Any,
    *,
    claims: GatewayClaims,
    tool_name: str,
    arguments: dict[str, Any],
    call_id: str,
    conversation_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """审批闸门：阻塞等待人工决策。

    返回 ``None`` 表示**已批准、可继续执行**；返回错误回执表示拒绝或超时
    （两条路径都**不执行工具**，绝不静默放行——``00`` §8 / ``01`` §4.7）。
    """
    from app.governance import approval as approval_mod
    from app.governance import audit as audit_mod

    try:
        decision = await approval_mod.request(
            ctx,
            conversation_id=conversation_id,
            tool_name=tool_name,
            args=arguments,
            call_id=call_id,
            timeout_seconds=_DEFAULT_APPROVAL_TIMEOUT,
            session_id=session_id,
        )
    except ApprovalTimeout as exc:
        # 超时：按未获批处理（勿静默放行）
        await _audit_approval(
            claims=claims,
            conversation_id=conversation_id,
            session_id=session_id,
            tool_name=tool_name,
            call_id=call_id,
            approved=False,
            status="timeout",
            reason=str(exc),
        )
        await _emit_tool_event(
            claims=claims,
            conversation_id=conversation_id,
            session_id=session_id,
            event_type="tool.approval.decided",
            payload={
                "tool_name": tool_name,
                "call_id": call_id,
                "approved": False,
                "status": "timeout",
                "reason": "approval_timeout",
            },
        )
        return _error_receipt("approval_timeout", f"审批超时: {tool_name}", call_id, tool_name)

    if not decision.approved:
        await _audit_approval(
            claims=claims,
            conversation_id=conversation_id,
            session_id=session_id,
            tool_name=tool_name,
            call_id=call_id,
            approved=False,
            status="rejected",
            reason=decision.reason,
        )
        await _emit_tool_event(
            claims=claims,
            conversation_id=conversation_id,
            session_id=session_id,
            event_type="tool.approval.decided",
            payload={
                "tool_name": tool_name,
                "call_id": call_id,
                "approved": False,
                "status": "rejected",
                "reason": decision.reason,
            },
        )
        return _error_receipt(
            "approval_rejected", f"审批被拒绝: {tool_name}", call_id, tool_name
        )

    # 已批准：落审计（谁批的通过 approval_id 可追）
    await _audit_approval(
        claims=claims,
        conversation_id=conversation_id,
        session_id=session_id,
        tool_name=tool_name,
        call_id=call_id,
        approved=True,
        status="approved",
        reason=decision.reason,
        approval_id=decision.approval_id,
    )
    await _emit_tool_event(
        claims=claims,
        conversation_id=conversation_id,
        session_id=session_id,
        event_type="tool.approval.decided",
        payload={
            "tool_name": tool_name,
            "call_id": call_id,
            "approval_id": decision.approval_id,
            "approved": True,
            "status": "approved",
            "reason": decision.reason,
        },
    )
    return None


async def _audit_approval(
    *,
    claims: GatewayClaims,
    conversation_id: str | None,
    session_id: str | None,
    tool_name: str,
    call_id: str,
    approved: bool,
    status: str,
    reason: str,
    approval_id: str | None = None,
) -> None:
    """审批流转落审计（requested / decided 两种动作）。"""
    from app.governance import audit as audit_mod

    try:
        await audit_mod.record(
            audit_mod.AuditEvent(
                actor_user_id=claims.user_id,
                org_unit_id=claims.org_unit_id,
                action="tool.approval.requested",
                resource_type="tool",
                resource_id=tool_name,
                conversation_id=conversation_id,
                session_id=session_id,
                profile_version=claims.profile_version,
                tool_name=tool_name,
                approval_id=approval_id,
                result="ok" if approved else "denied",
                trace_id="",
                occurred_at=utcnow(),
                details={"call_id": call_id, "status": status, "reason": reason},
            )
        )
        await audit_mod.record(
            audit_mod.AuditEvent(
                actor_user_id=claims.user_id,
                org_unit_id=claims.org_unit_id,
                action="tool.approval.decided",
                resource_type="tool",
                resource_id=tool_name,
                conversation_id=conversation_id,
                session_id=session_id,
                profile_version=claims.profile_version,
                tool_name=tool_name,
                approval_id=approval_id,
                result="ok" if approved else "denied",
                trace_id="",
                occurred_at=utcnow(),
                details={"call_id": call_id, "status": status, "approved": approved},
            )
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败不阻断审批语义
        log.warning("approval audit failed", extra={"zy": {"error": str(exc)}})


# ==========================================================================
# 步骤 3：分类派发
# ==========================================================================


async def _dispatch(
    tool_name: str,
    arguments: dict[str, Any],
    call_id: str,
    claims: GatewayClaims,
    conversation_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """按工具类别分派执行。"""
    from app.capability import registry as tool_registry
    from app.capability.base import ToolContext
    from app.identity.context import RequestContext

    tool = tool_registry.get(tool_name)

    # server / hybrid：服务端直接执行
    if tool is not None and getattr(tool, "category", None) in ("server", "hybrid"):
        ctx = RequestContext(
            user_id=claims.user_id,
            org_unit_id=claims.org_unit_id,
            roles=frozenset(claims.scopes),
            device_id=claims.runtime_id or None,
        )
        tc = ToolContext(
            ctx=ctx,
            conversation_id=conversation_id,
            session_id=session_id,
            call_id=call_id,
            trace_id=claims.runtime_id,
        )
        try:
            tool_result = await tool.execute(arguments, tc)
        except Exception as exc:  # noqa: BLE001 - 工具异常统一转结构化错误
            log.exception("server tool failed", extra={"zy": {"tool": tool_name}})
            return _error_receipt("tool_execution_error", str(exc), call_id, tool_name)

        # 工具自身可能"没抛异常但报告失败"（ToolResult.ok=False，如 HTTP 抓取被拒）。
        # 这属于**执行失败**，必须走 error 回执——否则上层会把它当成功并落
        # tool.call.completed 事件，前端 SSE 会显示"成功"（静默失败，最危险的一类）。
        if getattr(tool_result, "ok", True) is False:
            err_text = str(getattr(tool_result, "error", None) or "工具报告失败")
            return _error_receipt("tool_execution_error", err_text, call_id, tool_name)

        return {
            "ok": True,
            "result": getattr(tool_result, "value", None),
            "receipt": {"call_id": call_id, "tool_name": tool_name, "location": "server"},
        }

    # local：转发本地 Agent（阶段 1 未接入 → 结构化离线错误，不静默失败）
    if tool is not None and getattr(tool, "category", None) == "local":
        return _local_offline_receipt(call_id, tool_name)

    # 未注册工具：fail-closed 拒绝
    return _error_receipt("tool_not_found", f"工具未注册: {tool_name}", call_id, tool_name)


# ==========================================================================
# 步骤 4：效果核验
# ==========================================================================


def _verify_result(tool: Any, result: dict[str, Any]) -> str:
    """效果核验：按工具声明的 ``output_schema`` 粗校验响应结构。

    **三态判定**（``00`` §5.5：判定必须是成功/失败/**不确定**，"不确定"不能当成功用）：

    - ``"failed"``：执行失败（``ok=False``）；
    - ``"unverified"``：成功但无 ``output_schema`` 可依（声明 ``null`` = 不做输出校验，
      此时"不确定"，不冒充成功）；
    - ``"ok"``：成功且通过 schema 的 ``required`` 字段存在性校验。

    刻意只做 ``required`` 存在性这一种最保守的校验：完整 JSON Schema 校验需要额外依赖，
    而"字段都在"已能拦住绝大多数"声称成功其实没给东西"的静默失败。
    """
    if not result.get("ok"):
        return "failed"
    schema = getattr(tool, "output_schema", None)
    if not isinstance(schema, dict):
        return "unverified"
    required = schema.get("required")
    if not isinstance(required, list) or not required:
        return "unverified"
    value = result.get("result")
    if not isinstance(value, dict):
        return "unverified"
    missing = [f for f in required if f not in value]
    if missing:
        log.warning(
            "effect verification failed: missing required fields",
            extra={"zy": {"tool": getattr(tool, "name", "?"), "missing": missing}},
        )
        return "failed"
    return "ok"


def _summarize(result: Any) -> str:
    """结果摘要（事件里只放摘要；完整 result 字段在事件里是 secret，viewer 看不到）。"""
    if result is None:
        return ""
    text = repr(result)
    if len(text) > _RESULT_SUMMARY_MAX:
        return text[:_RESULT_SUMMARY_MAX] + "…"
    return text


#: 核验判定 → 事件 status 的**唯一**映射表（P1-2）。
#: 消费者只认 ``status``；``verified`` 额外保留三态细节。
#: 关键：``unverified``（不确定）**不冒充 ok**——``00`` §5.5 要求三态判定，
#: "不确定"不能当成功用。
_STATUS_BY_VERDICT: dict[str, str] = {
    "ok": "ok",
    "unverified": "unverified",
    "failed": "failed",
}


def _status_for(verdict: str) -> str:
    """由核验判定派生事件 ``status``（杜绝 status 与 verified 自相矛盾）。

    - ``ok``         → ``"ok"``（执行成功且通过核验）
    - ``unverified`` → ``"unverified"``（执行成功但无 schema 可依，**不是成功**）
    - ``failed``     → ``"failed"``（核验发现缺字段等硬失败）
    """
    return _STATUS_BY_VERDICT.get(verdict, "unverified")


# ==========================================================================
# 事件与回执辅助
# ==========================================================================


async def _emit_tool_event(
    *,
    claims: GatewayClaims,
    conversation_id: str | None,
    session_id: str | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """经 kernel 层事件出口发布一条工具事件（无会话时降级为只记日志）。"""
    await emit_event(
        org_unit_id=claims.org_unit_id,
        conversation_id=conversation_id,
        event_type=event_type,
        payload=payload,
        session_id=session_id or "",
        runtime_id=claims.runtime_id or "",
        profile_version=claims.profile_version or "",
    )


def _error_receipt(
    code: str, message: str, call_id: str, tool_name: str
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message},
        "receipt": {"call_id": call_id, "tool_name": tool_name, "location": "server"},
    }


def _local_offline_receipt(call_id: str, tool_name: str) -> dict[str, Any]:
    """返回结构化 ``local_agent_offline`` 错误（``00`` §5.7），不静默失败。"""
    return {
        "ok": False,
        "error": {
            "code": "local_agent_offline",
            "message": "本地 Agent 离线",
            "details": {"tool_name": tool_name, "last_seen": None},
        },
        "receipt": {"call_id": call_id, "tool_name": tool_name, "location": "local"},
    }
