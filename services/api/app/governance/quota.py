"""配额（``governance/quota.py``，``00`` §8 / ``01`` §4.7 / ``02`` §2.5 集合 ``quota_usage``）。

按组织单元 / 用户限制 token 与调用次数。模型网关入口统一扣减（``00`` §8），
``$inc + upsert``，**不用读改写**（``02`` §2.5）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core import mongo
from app.core.clock import utcnow
from app.core.errors import QuotaExceeded
from app.identity.context import RequestContext
from app.kernel.inbound.credentials import GatewayClaims

QUOTA_COLLECTION = "quota_usage"


def _period(dt: datetime | None = None) -> str:
    """返回当前的 ``YYYY-MM`` 配额周期。"""
    now = dt or utcnow()
    return now.strftime("%Y-%m")


async def _audit_denied(
    org_unit_id: str,
    user_id: str,
    *,
    session_id: str,
    attempted: int,
    quota: int,
) -> None:
    """超额拒绝留审计（S-45：**拒绝也是必须落审计的写事件**，验收③"超额调用被拒并留审计"）。"""
    from app.governance.audit import AuditEvent, record

    await record(
        AuditEvent(
            actor_user_id=user_id,
            action="model.generate",
            org_unit_id=org_unit_id,
            resource_type="model_instance",
            session_id=session_id,
            result="denied",
            error_code="quota_exceeded",
            details={"attempted_tokens": attempted, "quota_monthly": quota},
        )
    )


async def charge_model_tokens(
    ctx: RequestContext,
    *,
    session_id: str,
    prompt: int,
    completion: int,
) -> None:
    """扣减模型 token 配额（``$inc + upsert``），超额抛 QuotaExceeded 并留审计。"""
    coll = mongo.get_collection(QUOTA_COLLECTION)
    key = {
        "org_unit_id": ctx.org_unit_id,
        "user_id": ctx.user_id,
        "period": _period(),
    }
    # 先读当前用量（供超额判断）
    usage = await coll.find_one(key)
    prompt_total_current = int((usage or {}).get("model_tokens_prompt") or 0) + max(prompt, 0)
    completion_total_current = int((usage or {}).get("model_tokens_completion") or 0) + max(completion, 0)

    # 默认月配额（0 = 不限）
    from app.settings import get_settings

    quota = get_settings().default_model_token_quota_monthly
    if quota and quota > 0 and (prompt_total_current + completion_total_current) > quota:
        await _audit_denied(
            ctx.org_unit_id,
            ctx.user_id,
            session_id=session_id,
            attempted=prompt_total_current + completion_total_current,
            quota=quota,
        )
        raise QuotaExceeded("模型 token 月配额超限")

    await coll.update_one(
        key,
        {
            "$inc": {
                "model_tokens_prompt": max(prompt, 0),
                "model_tokens_completion": max(completion, 0),
            },
            "$set": {"updated_at": utcnow()},
        },
        upsert=True,
    )


async def ensure_model_quota_from_claims(
    claims: GatewayClaims,
    *,
    session_id: str,
    estimated_prompt: int,
) -> None:
    """配额**预检**（不扣减）：超额抛 QuotaExceeded 并留审计（S-44/S-45）。

    供 kernel gateway 端点在构造流式响应**之前**调用——流式响应一旦开始返回，
    200 已发出，此时再拒绝只能截断流而非给出 429。
    """
    from app.settings import get_settings

    quota = get_settings().default_model_token_quota_monthly
    if not quota or quota <= 0:
        return
    coll = mongo.get_collection(QUOTA_COLLECTION)
    usage = await coll.find_one(
        {"org_unit_id": claims.org_unit_id, "user_id": claims.user_id, "period": _period()}
    )
    total = int((usage or {}).get("model_tokens_prompt") or 0) + int(
        (usage or {}).get("model_tokens_completion") or 0
    ) + max(estimated_prompt, 0)
    if total > quota:
        await _audit_denied(
            claims.org_unit_id,
            claims.user_id,
            session_id=session_id,
            attempted=total,
            quota=quota,
        )
        raise QuotaExceeded("模型 token 月配额超限")


async def charge_tool_call(ctx: RequestContext, *, tool_name: str) -> None:
    """扣减一次工具调用计数（``$inc + upsert``）。"""
    coll = mongo.get_collection(QUOTA_COLLECTION)
    await coll.update_one(
        {
            "org_unit_id": ctx.org_unit_id,
            "user_id": ctx.user_id,
            "period": _period(),
        },
        {"$inc": {"tool_calls": 1}, "$set": {"updated_at": utcnow()}},
        upsert=True,
    )


async def charge_model_tokens_from_claims(
    claims: GatewayClaims,
    *,
    session_id: str,
    prompt: int,
    completion: int,
) -> None:
    """从网关声明构造 RequestContext 后扣减模型 token 配额。

    模型网关入口统一调用（``00`` §8 / ``03`` §4.1）。网关 token 已含 org/user，无需再解析身份。
    """
    ctx = RequestContext(
        user_id=claims.user_id,
        org_unit_id=claims.org_unit_id,
        roles=frozenset(claims.scopes),
    )
    await charge_model_tokens(
        ctx, session_id=session_id, prompt=prompt, completion=completion
    )


async def current_usage(
    ctx: RequestContext,
) -> dict[str, Any]:
    """返回当前用户本月的配额用量（供管理端/个人查看）。"""
    coll = mongo.get_collection(QUOTA_COLLECTION)
    usage = await coll.find_one(
        {"org_unit_id": ctx.org_unit_id, "user_id": ctx.user_id, "period": _period()}
    )
    if usage is None:
        return {
            "model_tokens_prompt": 0,
            "model_tokens_completion": 0,
            "tool_calls": 0,
        }
    # Mongo 原始文档含 _id（ObjectId），FastAPI JSON 编码不了 → 必须剥掉再下发
    usage.pop("_id", None)
    # 契约归一化：只扣过 model token 的行没有 tool_calls 字段 → 补齐三计数字段
    for key in ("model_tokens_prompt", "model_tokens_completion", "tool_calls"):
        usage.setdefault(key, 0)
    return usage
