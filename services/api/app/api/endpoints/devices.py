"""设备端点（``api/endpoints/devices.py``，``02`` §4.6）。

- GET    /api/v1/devices：我的设备（JWT）。
- DELETE /api/v1/devices/{device_id}：解绑 + 吊销凭据 + 断连。

阶段 1：设备台账数据模型落库；本地 Agent 桥（配对/ticket/WS）属阶段 3，此处提供数据视图。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core import mongo
from app.core.clock import utcnow
from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])

DEVICES_COLLECTION = "devices"


@router.get("")
async def list_devices(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """我的设备列表。"""
    coll = mongo.get_collection(DEVICES_COLLECTION)
    cursor = coll.find(
        {"org_unit_id": ctx.org_unit_id, "user_id": _oid(ctx.user_id), "status": "active"}
    )
    rows = await cursor.to_list(length=None)
    return {
        "items": [
            {
                "device_id": r.get("device_id"),
                "display_name": r.get("display_name"),
                "os": r.get("os"),
                "agent_version": r.get("agent_version"),
                "capabilities": r.get("capabilities", []),
                "status": r.get("status"),
                "bound_at": r.get("bound_at"),
                "last_seen_at": r.get("last_seen_at"),
            }
            for r in rows
        ]
    }


@router.delete("/{device_id}")
async def revoke_device(
    device_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """解绑设备：吊销凭据 + 撤销在线状态。"""
    coll = mongo.get_collection(DEVICES_COLLECTION)
    await coll.update_one(
        {"org_unit_id": ctx.org_unit_id, "user_id": _oid(ctx.user_id), "device_id": device_id},
        {"$set": {"status": "revoked", "revoked_at": utcnow()}},
    )
    # 吊销凭据
    creds = mongo.get_collection("device_credentials")
    await creds.update_many(
        {"org_unit_id": ctx.org_unit_id, "device_id": device_id, "revoked_at": None},
        {"$set": {"revoked_at": utcnow()}},
    )
    # 移除在线标记（阶段 1 无 Redis 在线表则忽略）
    try:
        from app.core import redis as redis_core

        rc = redis_core.client()
        await rc.hdel(redis_core.localagent_online_hash(ctx.org_unit_id), ctx.user_id)
    except Exception:  # pragma: no cover
        pass
    return {"ok": True}


def _oid(value: str):
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return value
