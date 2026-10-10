"""应用装配（``app/main.py``，``01`` §5.1 启动顺序）。

启动顺序：
1. settings = Settings()（缺关键配置 fail fast）
2. 构建 FastAPI(app, lifespan)
3. 注册中间件（顺序敏感）：trace_id → 异常映射 → CORS（仅开发）
4. 注册路由：公开 api/v1/*（identity 鉴权）、内部 internal/kernel/*（HMAC，独立中间件）
5. lifespan.startup
   a. mongo.connect() + ensure_indexes()
   b. redis.connect()
   c. capability.registry.bootstrap()
   d. (阶段 1) 本地 Agent 注册表订阅留待阶段 3
   e. branding.seed_if_empty(settings.branding_seed_file)  # ★ 幂等 seed
   f. kernel.dsh.gateway.health_check()                    # 宿主不可用仅告警不阻塞
6. lifespan.shutdown：停止新 turn、等待进行中 turn 收尾、释放 Redis 在线标记、关闭 mongo/redis
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.core.logging import configure_logging, get_logger

log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：startup / shutdown。"""
    from app.settings import Settings, validate_critical

    settings = Settings()
    validate_critical(settings)

    # ---- startup ----
    # a. mongo
    from app.core import mongo

    mongo.connect(settings.mongo_uri, settings.mongo_db)
    mongo.ensure_indexes()
    log.info("mongo ready")

    # b. redis
    from app.core import redis as redis_core

    redis_core.connect(settings.redis_url)
    log.info("redis ready")

    # c. capability bootstrap（注册服务端工具）
    from app.capability import registry as capability_registry

    capability_registry.bootstrap()

    # d. (阶段 1) 本地 Agent 注册表订阅留待阶段 3 落地

    # e. branding seed（幂等：仅空库写入）
    try:
        from app.branding.settings import seed_if_empty

        seeded = await seed_if_empty(settings.branding_seed_file)
        if seeded:
            log.info("branding seeded on first boot")
    except Exception as exc:  # noqa: BLE001 - seed 失败不阻塞启动
        log.warning("branding seed skipped", extra={"zy": {"error": str(exc)}})

    # e2. 身份域 seed（幂等：仅空集合写入；提供可登录的管理员/成员账号 + 角色绑定）
    #     与品牌 seed 同样**不覆盖**已有人工改动。失败不阻塞启动——
    #     私有化环境里运维要能看到界面排查问题，而不是整个服务起不来（``01`` §5.1）。
    try:
        from app.identity.seed import seed_identity

        written = await seed_identity()
        if any(written.values()):
            log.info("identity seeded on first boot", extra={"zy": {"written": written}})
    except Exception as exc:  # noqa: BLE001 - 身份 seed 失败不阻塞启动
        log.warning("identity seed skipped", extra={"zy": {"error": str(exc)}})

    # e3. 内置工具目录同步（幂等：registry server 工具 → tools 目录 + 默认授权）。
    #     目录为空时 profile.tools 恒为空、内核智能体拿不到任何工具（执行层的
    #     registry 不会自动进编译层）；失败不阻塞启动。
    try:
        from app.capability.catalog_seed import sync_server_tool_catalog

        inserted = await sync_server_tool_catalog()
        if inserted:
            log.info("tool catalog synced on boot", extra={"zy": {"inserted": inserted}})
    except Exception as exc:  # noqa: BLE001 - 同步失败不阻塞启动
        log.warning("tool catalog sync skipped", extra={"zy": {"error": str(exc)}})

    # f. kernel host health check（失败仅告警不阻塞，``01`` §5.1）
    #    经 kernel/turn/driver 调用，避免 app.* 直接 import kernel/dsh（``01`` §3 分层）
    try:
        from app.kernel.turn.driver import health_check

        health = await health_check()
        log.info("kernel host healthy", extra={"zy": {"health": health}})
    except Exception as exc:  # noqa: BLE001
        log.warning("kernel host health check failed (non-blocking)", extra={"zy": {"error": str(exc)}})

    # g. kernel 事件持久化接线（#18：事件先落 kernel_events 再推送）
    #    kernel → collaboration 禁止直连，经回调注入（``01`` §3 分层）
    #
    #    ★ fail-fast：事件持久化是 SSE 正确性的地基，接线失败**必须**让启动失败。
    #    静默丢事件比启动失败危险得多——后者立刻暴露，前者让你以为一切正常，
    #    几周后才发现 SSE 少事件（这正是 P1-1 的教训：原先这里只 warning 后继续启动）。
    from app.collaboration.events import persist_envelope
    from app.kernel.events import set_sink
    from app.kernel.turn.driver import set_event_handler

    async def _persist_kernel_event(
        *, org_unit_id: str, conversation_id: str, envelope: object
    ) -> None:
        await persist_envelope(org_unit_id, conversation_id, envelope=envelope)  # type: ignore[arg-type]

    # 定义在 try 之外：否则后续步骤引用它时会因前一步失败而 NameError（同样是静默失败）
    set_event_handler(_persist_kernel_event)
    set_sink(_persist_kernel_event)
    log.info("kernel event persistence wired (driver + inbound sink)")

    app.state.settings = settings
    log.info("app started")

    yield

    # ---- shutdown ----
    # a. 停止接受新 turn：阶段 1 进程退出即停，进行中 turn 由 claim TTL 兜底
    # b. 等待进行中 turn 收尾（上限 30s）：阶段 1 简化，依赖 claim TTL
    # c. 释放 Redis 在线标记：阶段 1 本地 Agent 未启用，跳过
    # d. 关闭 mongo / redis
    try:
        await redis_core.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        await mongo.close()
    except Exception:  # noqa: BLE001
        pass
    log.info("app shut down")


def create_app() -> FastAPI:
    """构建并装配 FastAPI 应用。"""
    from app.settings import Settings

    settings = Settings()
    configure_logging(settings.log_level, logger_name="zy_agents")

    app = FastAPI(
        title="ZY-Agents API",
        version="0.3.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # 3. 中间件（顺序敏感：trace → error → CORS → auth → hmac）
    from app.api.middleware import (
        AuthMiddleware,
        CORSDevelopmentMiddleware,
        ErrorMappingMiddleware,
        HmacAuthMiddleware,
        TraceIdMiddleware,
    )

    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(ErrorMappingMiddleware)
    if settings.app_env == "development":
        app.add_middleware(CORSDevelopmentMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(HmacAuthMiddleware)

    # 4. 路由
    from app.api.router import api_router, internal_router

    app.include_router(api_router)
    app.include_router(internal_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """存活探针。"""
        return {"ok": "true"}

    return app


app = create_app()
