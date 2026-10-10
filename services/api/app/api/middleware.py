"""HTTP 中间件（``api/middleware.py``，``01`` §5.2 / §3）。

顺序敏感（``01`` §5.2）：:

    TraceIdMiddleware          → 生成/透传 trace_id，注入日志上下文
    ErrorMappingMiddleware     → DomainError → HTTP status + 统一错误体
    AuthMiddleware(公开路由)    → resolve_context → request.state.ctx
    HmacAuthMiddleware(内部)    → credentials.verify → request.state.kernel_claims

**内部路由的鉴权不复用公开路由的中间件**——两条路径的凭据类型完全不同。
"""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core import logging as logging_core
from app.core.errors import DomainError, GatewayTokenError, Unauthenticated
from app.core.logging import get_logger
from app.identity.auth import resolve_context

log = get_logger("api.middleware")

#: 内部内核回调前缀（HMAC）
KERNEL_INTERNAL_PREFIX = "/internal/kernel/"
#: 公开免鉴权端点（``02`` §4.1：GET /api/v1/branding 是唯一免 JWT 公开端点）
#: 注：POST /auth/login 必须免 JWT（登录是获取 token 的入口，否则鸡生蛋）。
PUBLIC_WHITELIST = {("GET", "/api/v1/branding"), ("POST", "/api/v1/auth/login")}


class TraceIdMiddleware(BaseHTTPMiddleware):
    """生成/透传 trace_id 并注入日志上下文。"""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        trace_id = request.headers.get("X-Trace-Id") or _new_trace_id()
        logging_core.set_trace_id(trace_id)
        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            logging_core.reset_trace_id()


class ErrorMappingMiddleware(BaseHTTPMiddleware):
    """把 DomainError 映射为统一错误体 HTTP 响应。"""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        try:
            return await call_next(request)
        except DomainError as exc:
            exc.trace_id = logging_core.get_trace_id()
            return JSONResponse(
                status_code=exc.status,
                content={"error": exc.as_body()},
            )
        except Exception as exc:  # noqa: BLE001 - 未预期异常也返回可读错误
            log.exception("unhandled error")
            trace_id = logging_core.get_trace_id()
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "服务器内部错误",
                        "details": {},
                        "trace_id": trace_id,
                    }
                },
            )


class CORSDevelopmentMiddleware(BaseHTTPMiddleware):
    """仅开发环境的 CORS 兜底（生产由 nginx 处理）。"""

    def __init__(self, app: Any, *, allowed_origins: list[str] | None = None) -> None:
        super().__init__(app)
        self._allowed = allowed_origins or ["*"]

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = ",".join(self._allowed) if self._allowed != ["*"] else "*"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Trace-Id"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Credentials"] = "false"
        if request.method == "OPTIONS":
            return Response(status_code=204)
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """公开路由鉴权：Bearer JWT → RequestContext → request.state.ctx。

    内部路由 / 公开白名单端点不在此鉴权（前者走 HmacAuthMiddleware，后者免 JWT）。
    任何失败抛 Unauthenticated，绝不返回匿名上下文。
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path
        if path.startswith(KERNEL_INTERNAL_PREFIX):
            # 内部路由交由 HmacAuthMiddleware，不复用本中间件
            return await call_next(request)

        if (request.method, path) in PUBLIC_WHITELIST or _is_static(path):
            # 公开白名单端点 / 静态资源不鉴权
            return await call_next(request)

        from app.settings import get_settings as _gs

        authorization = request.headers.get("Authorization")
        try:
            ctx = await resolve_context(authorization, settings=_gs())
        except Unauthenticated as exc:
            exc.trace_id = logging_core.get_trace_id()
            return JSONResponse(status_code=401, content={"error": exc.as_body()})

        request.state.ctx = ctx
        return await call_next(request)


class HmacAuthMiddleware(BaseHTTPMiddleware):
    """内部内核回调鉴权：HMAC 短时 token → request.state.kernel_claims。

    独立于公开路由中间件。校验同时比对 aud 与密钥域（模型/工具分离）。
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path
        if not path.startswith(KERNEL_INTERNAL_PREFIX):
            return await call_next(request)

        from app.kernel.inbound import credentials
        from app.kernel.inbound.credentials import AUD_MODEL, AUD_TOOL
        from app.settings import get_settings as _gs

        settings = _gs()
        authorization = request.headers.get("Authorization")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "gateway_token_error", "message": "缺少网关 token", "details": {}, "trace_id": logging_core.get_trace_id()}},
            )

        # 期望 aud：模型端点 / 工具端点（approval 走工具域）
        expect_aud = AUD_MODEL if path.endswith("/model/generate") else AUD_TOOL
        secret = (
            settings.kernel_model_gateway_secret
            if expect_aud == AUD_MODEL
            else settings.kernel_tool_gateway_secret
        )
        try:
            claims = credentials.verify(token, expect_aud=expect_aud, secret=secret)
        except GatewayTokenError as exc:
            exc.trace_id = logging_core.get_trace_id()
            return JSONResponse(status_code=401, content={"error": exc.as_body()})

        request.state.kernel_claims = claims
        return await call_next(request)


def _is_static(path: str) -> bool:
    # 阶段 1 无前端静态资源由本服务托管；保留钩子避免误鉴权非 API 路径
    return not path.startswith("/api/") and not path.startswith("/internal/")


def _new_trace_id() -> str:
    import uuid

    return uuid.uuid4().hex
