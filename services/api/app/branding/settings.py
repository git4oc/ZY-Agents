"""品牌设置（``branding/settings.py``，``01`` §4.8 / ``02`` §2.9 / ``00`` §12）。

- ``BrandingSettings``：``extra="forbid"`` 的配置模型；主题色仅 ``#RRGGBB``（``00`` §12.7）。
- ``load``：进程内缓存 + 失效订阅（同 Profile 失效通道）。
- ``update``：需 ``brand.manage`` 权限；写库后发布失效事件；**不要重启容器**。
- ``seed_if_empty``：★ 幂等——仅当集合为空时写入（来自 ``BRANDING_SEED_FILE``）；
  **非空则跳过**——否则每次重启都会覆盖实施人员在后台的修改（``00`` §12.6）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core import mongo
from app.core.clock import utcnow
from app.branding.repository import BrandingRepository, SINGLETON_ID
from app.identity.context import RequestContext

log = logging.getLogger("zy_agents.branding.settings")

#: 主题色校验：仅 #RRGGBB，不接受任意 CSS 串（``00`` §12.7）
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

#: 品牌缓存（进程内）
_cache: "BrandingSettings | None" = None


class ThemeColors(BaseModel):
    """品牌主题色（仅 #RRGGBB）。"""

    model_config = ConfigDict(extra="forbid")
    primary: str = "#2563eb"
    accent: str = "#0ea5e9"

    @field_validator("primary", "accent")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        if not _HEX_COLOR.match(v):
            raise ValueError(f"主题色必须是 #RRGGBB 格式，收到 {v!r}")
        return v


class BrandingSettings(BaseModel):
    """品牌设置内容（extra="forbid"）。"""

    model_config = ConfigDict(extra="forbid")
    display_name: str  # 客户公司全称（UI 主标题、浏览器 title）
    short_name: str  # 简称（侧边栏、窄屏）
    theme: ThemeColors = Field(default_factory=ThemeColors)  # primary / accent，仅 #RRGGBB
    vendor_badge_enabled: bool = True  # 页脚"由 ZY-Agents 提供技术支持"，默认开
    # ---- 阶段 2 文案覆盖字段（均为可选；不填则用默认 UI 文案）----
    login_title: str | None = None  # 登录页大标题
    login_welcome: str | None = None  # 登录页欢迎语
    footer_copyright: str | None = None  # 页脚版权行（覆盖默认 "© ZY-Agents"）
    help_url: str | None = None  # 帮助/支持链接（仅 https，S-64）
    support_contact: str | None = None  # 支持联系方式（邮箱/电话文本）
    # 资产字段（由 assets 模块维护，管理员上传）
    logo_wide: dict[str, Any] | None = None
    logo_square: dict[str, Any] | None = None
    favicon: dict[str, Any] | None = None
    updated_at: Any | None = None
    updated_by: Any | None = None

    @field_validator("help_url")
    @classmethod
    def _validate_help_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # S-64：URL 只允许 https（相对路径/非 https 一律拒绝，防脚本注入）
        if not v.startswith("https://"):
            raise ValueError(f"help_url 必须是 https 链接，收到 {v!r}")
        return v


class BrandingPatch(BaseModel):
    """品牌部分更新（extra="forbid"；仅允许可配字段）。"""

    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    short_name: str | None = None
    theme: ThemeColors | None = None
    vendor_badge_enabled: bool | None = None
    # ---- 阶段 2 文案覆盖（可选字段）----
    login_title: str | None = None
    login_welcome: str | None = None
    footer_copyright: str | None = None
    help_url: str | None = None
    support_contact: str | None = None

    @field_validator("help_url")
    @classmethod
    def _validate_help_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.startswith("https://"):
            raise ValueError(f"help_url 必须是 https 链接，收到 {v!r}")
        return v


def _default_settings() -> BrandingSettings:
    """返回默认品牌（``02`` §2.9 集合初始态：display_name="ZY-Agents"、vendor_badge_enabled=true）。"""
    return BrandingSettings(display_name="ZY-Agents", short_name="ZY")


async def load() -> BrandingSettings:
    """读取品牌设置（进程内缓存；无缓存时读库，空则返回默认）。

    **品牌随每次调用重新读可用配置**——模型网关在构造请求时调用本函数，客户改品牌后
    下一个 turn 即生效（``00`` §12.4 / ``03`` §4.1），无需重启、无需失效 Profile 缓存。
    """
    global _cache
    if _cache is not None:
        return _cache
    repo = BrandingRepository()
    doc = await repo.get()
    if doc is None:
        _cache = _default_settings()
        return _cache
    _cache = _settings_from_doc(doc)
    return _cache


def _settings_from_doc(doc: dict[str, Any]) -> BrandingSettings:
    """把 Mongo 文档转为 BrandingSettings（兼容阶段 2 追加字段——extra=forbid 只拒绝未知）。

    注意：阶段 2 追加的可选字段（login_title 等）需随后显式加入本模型；
    当前只取阶段 1 字段，未知字段若先前写入会触发 forbid——故 seed/写入必须全新构造。
    """
    return BrandingSettings(
        display_name=str(doc.get("display_name", "ZY-Agents")),
        short_name=str(doc.get("short_name") or doc.get("display_name", "ZY")),
        theme=ThemeColors(**doc["theme"]) if doc.get("theme") else ThemeColors(),
        vendor_badge_enabled=bool(doc.get("vendor_badge_enabled", True)),
        # 阶段 2 文案覆盖字段（可选；缺省 None → UI 用默认文案）
        login_title=doc.get("login_title"),
        login_welcome=doc.get("login_welcome"),
        footer_copyright=doc.get("footer_copyright"),
        help_url=doc.get("help_url"),
        support_contact=doc.get("support_contact"),
        logo_wide=doc.get("logo_wide"),
        logo_square=doc.get("logo_square"),
        favicon=doc.get("favicon"),
        updated_at=doc.get("updated_at"),
        updated_by=doc.get("updated_by"),
    )


def invalidate_cache() -> None:
    """使进程内品牌缓存失效（写入/seed 后调用，next load 重读库）。"""
    global _cache
    _cache = None


async def update(ctx: RequestContext, patch: BrandingPatch) -> BrandingSettings:
    """更新品牌设置。

    需 ``brand.manage`` 权限；写库后发布失效事件；**不要重启容器**。
    """
    # 权限校验走**共享 guard**（identity/acl.require_permission）——
    # 早前这里是内联 `if "brand.manage" not in ctx.roles`，不承认 admin 角色码，
    # 导致管理员改品牌被 403（"能显示、不能改"）。三处内联判定已统一收敛。
    from app.identity.acl import require_permission

    require_permission(ctx, "brand.manage")

    repo = BrandingRepository()
    # 注意 await 的位置：repo.get() 是协程需 await，_settings_from_doc() 是**同步**
    # 函数不能 await。写成 `await _settings_from_doc(repo.get() or {})` 会把 await
    # 施加在 coroutine 上 → AttributeError: 'coroutine' object has no attribute 'get'。
    current = _settings_from_doc(await repo.get() or {})  # type: ignore[arg-type]
    updated = _apply_patch(current, patch)

    doc = updated.model_dump()
    doc["updated_at"] = utcnow()
    doc["updated_by"] = ctx.user_id
    await repo.upsert(doc)

    invalidate_cache()
    # 发布品牌失效事件（可选，供跨实例 / 前端热更；阶段 1 本地缓存已失效）
    await _publish_invalidate()
    return updated


async def seed_if_empty(source: Path | None) -> bool:
    """★ 幂等 seed：仅当集合为空时写入（来自 BRANDING_SEED_FILE）。

    参数
    ----
    source:
        可选：部署预置品牌文件（YAML/JSON，``BRANDING_SEED_FILE``）。

    返回是否执行了 seed（True=写入；False=集合非空或无需写入）。
    """
    repo = BrandingRepository()
    existing = await repo.get()
    if existing is not None:
        # 非空则跳过——避免每次重启覆盖后台修改（``00`` §12.6）
        return False

    if source is not None and source.exists():
        settings = _load_seed_file(source)
    else:
        settings = _default_settings()

    doc = settings.model_dump()
    doc["updated_at"] = utcnow()
    doc["updated_by"] = None
    await repo.upsert(doc)
    invalidate_cache()
    log.info("branding seeded (if_empty)", extra={"zy": {"source": str(source) if source else "default"}})
    return True


def _load_seed_file(source: Path) -> BrandingSettings:
    """从 seed 文件加载品牌设置（YAML/JSON）。"""
    import json

    text = source.read_text(encoding="utf-8")
    raw: dict[str, Any]
    if source.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml

            raw = yaml.safe_load(text) or {}
        except Exception:
            raw = json.loads(text)
    else:
        raw = json.loads(text)
    branding = raw.get("branding", raw)
    return BrandingSettings(**branding)


def _apply_patch(current: BrandingSettings, patch: BrandingPatch) -> BrandingSettings:
    data = current.model_dump()
    for field_ in (
        "display_name",
        "short_name",
        "vendor_badge_enabled",
        # 阶段 2 文案覆盖字段
        "login_title",
        "login_welcome",
        "footer_copyright",
        "help_url",
        "support_contact",
    ):
        val = getattr(patch, field_)
        if val is not None:
            data[field_] = val
    if patch.theme is not None:
        data["theme"] = patch.theme.model_dump()
    return BrandingSettings(**data)


async def _publish_invalidate() -> None:
    try:
        from app.core import redis as redis_core

        rc = redis_core.client()
        await rc.publish("branding:invalidated", SINGLETON_ID)
    except Exception:  # pragma: no cover
        log.warning("branding invalidate publish failed")
