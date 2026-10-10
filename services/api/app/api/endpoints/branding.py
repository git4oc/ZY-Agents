"""品牌端点（``api/endpoints/branding.py``，``02`` §4.2 / §4.8）。

- GET    /api/v1/branding：★ 公开端点，只返回白名单字段（``public_payload``）。
- GET/PATCH /api/v1/admin/branding：读改 branding_settings（``brand.manage``）；不触发 Profile 失效。
- PUT/DELETE /api/v1/admin/branding/assets/{slot}：上传 / 移除 logo/favicon。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from app.branding.settings import BrandingPatch, load, update
from app.branding.public_view import public_payload
from app.deps import get_ctx
from app.identity.acl import require_permission
from app.identity.context import RequestContext

#: 公开品牌端点（免鉴权，由 middleware 白名单放行）
public_router = APIRouter(prefix="/api/v1", tags=["branding"])
#: 管理端品牌端点（JWT + brand.manage）
admin_router = APIRouter(prefix="/api/v1/admin/branding", tags=["admin-branding"])


@public_router.get("/branding")
async def get_public_branding() -> dict[str, Any]:
    """公开品牌视图（登录页渲染用）。只返回白名单字段（``S-63``）。"""
    settings = await load()
    return public_payload(settings)


class PatchBrandingBody(BaseModel):
    """品牌部分更新请求体。

    ★ 必须是**真 Pydantic 模型**并作为唯一参数声明——早前这里写成 `body: Any`，
    FastAPI 无法据 `Any` 推断出它是 body，于是当成 **query 参数**处理，
    `client.patch(url, json={...})` 永远收不到 body → 422
    ``{"loc": ["query","body"]}``。签名错了功能就等于不可用，而只测
    ``settings.update()`` 的单元测试永远发现不了（它不经过 HTTP 层）。
    """

    display_name: str | None = None
    short_name: str | None = None
    vendor_badge_enabled: bool | None = None
    theme: dict[str, Any] | None = None  # {primary, accent}

    # ⚠️ 以下 5 个阶段 2 字段（login_title / login_welcome / footer_copyright /
    #    help_url / support_contact）**当前显式忽略是设计而非遗漏**：
    #    apps/admin 的 BrandingView 已把它们做成只读展示 + 注释说明，
    #    UI 与后端有意对齐；阶段 2 连同端点一起接线（见 02 §4.8）。
    #    所以这里既不声明、也不转发——不是"忘了"，是"还没到"。
    model_config = {"extra": "ignore"}


@admin_router.get("")
async def get_admin_branding(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """管理端读取品牌配置（完整视图，非白名单）。"""
    require_permission(ctx, "brand.manage")
    settings = await load()
    return settings.model_dump()


@admin_router.patch("")
async def patch_admin_branding(
    body: PatchBrandingBody,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """管理端更新品牌配置（**不触发任何 Profile 失效** —— 改品牌不使会话失效）。"""
    require_permission(ctx, "brand.manage")
    d = body.model_dump()
    theme = d.get("theme")
    patch = BrandingPatch(
        display_name=d.get("display_name"),
        short_name=d.get("short_name"),
        vendor_badge_enabled=d.get("vendor_badge_enabled"),
        theme=theme if isinstance(theme, dict) else None,
    )
    updated = await update(ctx, patch)
    return updated.model_dump()


@admin_router.put("/assets/{slot}")
async def put_asset(
    slot: str,
    file: UploadFile = File(...),
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """上传品牌资产（内容嗅探 + 尺寸校验 + 服务端命名）。"""
    require_permission(ctx, "brand.manage")
    from app.branding.assets import put_asset as do_put
    from app.settings import get_settings

    s = get_settings()
    asset = await do_put(
        ctx,
        slot=slot,  # type: ignore[arg-type]
        upload=file,
        max_bytes=s.branding_asset_max_bytes,
        max_pixels=s.branding_asset_max_pixels,
        storage_root=s.storage_root / "branding-assets",
    )
    return {"slot": slot, "asset": asset}


@admin_router.delete("/assets/{slot}")
async def delete_asset(
    slot: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """移除品牌资产。"""
    require_permission(ctx, "brand.manage")
    from app.branding.assets import delete_asset as do_delete

    await do_delete(ctx, slot=slot)
    return {"ok": True}
