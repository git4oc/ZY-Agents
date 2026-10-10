"""FastAPI 依赖注入（``app/deps.py``，``01`` §2 目录树）。

- ``get_settings``：进程级设置单例。
- ``get_ctx``：从请求解析 RequestContext（由 AuthMiddleware 已填充；供端点安全地读取）。
- ``get_current_user``：显式依赖当前请求上下文（AuthMiddleware 兜底后的权威来源）。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.errors import Unauthenticated
from app.identity.context import RequestContext
from app.settings import Settings, get_settings


def get_settings() -> Settings:
    """返回配置单例（FastAPI dependency）。"""
    return _settings()


def _settings() -> Settings:
    from app.settings import get_settings as _gs

    return _gs()


def get_ctx(request: Request) -> RequestContext:
    """读取由 AuthMiddleware 填充的 RequestContext。

    公开路由的鉴权由 ``AuthMiddleware`` 统一完成并写入 ``request.state.ctx``；
    本 dependency 供端点读取。若缺失（未走鉴权中间件的路径/内部路由）则抛 Unauthenticated。
    """
    ctx: RequestContext | None = getattr(request.state, "ctx", None)
    if ctx is None:
        raise Unauthenticated("缺少已认证上下文")
    return ctx
