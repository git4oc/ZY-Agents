"""用户侧模型目录端点（``/api/v1/models``）。

阶段 1：读 Runtime Profile 编译输入里的可见模型（``load_model_for_user``，
只读静态配置），返回当前用户可用的聊天模型列表；模型选择器据此渲染，
不再依赖前端桩的空态。写操作（provider / instance 管理）在 admin 域
（``admin.py``），api_key 不经此端点下发（``00`` §3.2 不变量）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("")
async def list_models(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """当前用户可见的聊天模型（Profile 编译输入，静态配置，不含 api_key）。"""
    from app.kernel.profile.catalog import load_model_for_user

    model = await load_model_for_user(ctx.org_unit_id, ctx.user_id)
    items: list[dict[str, Any]] = []
    if model is not None:
        items.append(
            {
                "id": model.model_instance_id,
                "displayName": model.display_name or model.model_name,
                "modelName": model.model_name,
                "providerName": model.provider_name,
                "providerType": model.provider_type,
                "capabilities": model.capabilities,
                "isDefault": True,
                "healthStatus": "active",
            }
        )
    return {"items": items}
