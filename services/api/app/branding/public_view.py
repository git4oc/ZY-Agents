"""公开白名单视图（``branding/public_view.py``，``01`` §4.8 / ``00`` §12.3 / ``S-63``）。

★ 显式白名单：未认证端点（``GET /api/v1/branding``）只返回此处列出的字段。
**默认不外露**——以后加了内部开关字段，不显式加进这里就不会被公开。

- 前端不硬编码产品名/公司名/logo，一律走 ``GET /api/v1/branding``（``S-62``）。
- 不返回内部配置、密钥或未启用的开关（``00`` §12.3）。
"""

from __future__ import annotations

from typing import Any

from app.branding.settings import BrandingSettings


def public_payload(s: BrandingSettings) -> dict[str, Any]:
    """返回对**未认证客户端**公开的品牌字段（显式白名单）。

    返回字段：
    - ``display_name`` / ``short_name``：公司名（登录页、浏览器 title）。
    - ``theme``：主题色（CSS 变量）。
    - ``logo_wide`` / ``logo_square`` / ``favicon``：媒体引用元数据（content_type 等，不含内部路径）。
    - ``vendor_badge_enabled``：页脚厂商标识开关（前端据此显示页脚）。

    其余字段（updated_by 等内部信息）一律不外露。
    """
    assets = {
        slot: _public_asset(getattr(s, slot))
        for slot in ("logo_wide", "logo_square", "favicon")
        if getattr(s, slot) is not None
    }

    return {
        "display_name": s.display_name,
        "short_name": s.short_name,
        "theme": {
            "primary": s.theme.primary,
            "accent": s.theme.accent,
        },
        # 阶段 2 文案覆盖：仅公开已配置的（None 不出现，前端用默认文案）
        "login_title": s.login_title,
        "login_welcome": s.login_welcome,
        "footer_copyright": s.footer_copyright,
        "help_url": s.help_url,
        "support_contact": s.support_contact,
        "assets": assets,
        "vendor_badge_enabled": s.vendor_badge_enabled,
    }


def _public_asset(assert_ref: dict[str, Any] | None) -> dict[str, Any] | None:
    """把内部 AssetRef 收窄为可公开的媒体元数据（不含 storage_key 等内部路径）。"""
    if assert_ref is None:
        return None
    return {
        "content_type": assert_ref.get("content_type"),
        "bytes": assert_ref.get("bytes"),
        "width": assert_ref.get("width"),
        "height": assert_ref.get("height"),
    }
