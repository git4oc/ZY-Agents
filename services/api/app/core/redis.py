"""Redis 客户端与 pub/sub 封装。

阶段 1 主服务的基础设施模块之一（``core/redis.py``，见 ``01-engineering-design.md`` §2 目录树）。

设计要点
--------
- Redis 承担：缓存、Celery broker、本地 Agent 在线注册表、跨实例事件路由（``00`` §1.2）。
- turn 抢占用 Redis ``SET NX EX``（原子 + TTL，见 ``02`` §2.7）。
- Profile 失效通过 Redis 频道 ``profile:invalidate:{org_unit_id}`` 广播（``03`` §3.5）。
- 提供 ```acquire_lock``（SET NX EX）工具，供 turn claim / 跨实例互斥使用。
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis  # type: ignore[import]

from app.core.errors import StorageError
from app.core.logging import get_logger

log = get_logger("core.redis")

#: 频道前缀常量（避免各处手写字符串导致不一致）
PROFILE_INVALIDATE_CHANNEL = "profile:invalidate:"
LOCALAGENT_ROUTE_CHANNEL = "localagent:route:"
LOCALAGENT_ONLINE_KEY = "localagent:online:"


_client: aioredis.Redis | None = None


def connect(url: str) -> aioredis.Redis:
    """建立（或复用）Redis 连接。

    参数
    ----
    url:
        ``REDIS_URL``（如 ``redis://redis:6379/0``）。

    返回客户端句柄；幂等。
    """
    global _client
    if _client is not None:
        return _client
    # protocol=2（RESP2）：本地/旧版 Redis（<6.0）不支持 HELLO 握手（RESP3 需要），
    # 显式降级保证服务端兼容性（redis-py 8.x 默认 RESP3）。
    _client = aioredis.from_url(url, decode_responses=True, protocol=2)
    log.info("redis connected")
    return _client


def client() -> aioredis.Redis:
    """返回当前 Redis 客户端；未连接时抛错。"""
    if _client is None:
        raise StorageError("Redis 尚未连接，请先调用 redis.connect()")
    return _client


async def close() -> None:
    """关闭 Redis 连接。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        log.info("redis closed")


async def acquire_lock(
    key: str,
    *,
    token: str | None = None,
    ttl_seconds: int = 30,
) -> bool:
    """尝试获取一把 Redis 锁（``SET key value NX EX``）。

    参数
    ----
    key:
        锁键（如 ``turn:lock:{conversation_id}``）。
    token:
        锁持有者标记（默认用空串）；释放时应回传同一 token 以防误删他人锁。
    ttl_seconds:
        锁自动过期秒数（turn claim 需 TTL 语义）。

    返回是否成功获得锁。
    """
    rc = client()
    val = token or ""
    result = await rc.set(key, val, nx=True, ex=ttl_seconds)
    return result is not None and result is True


async def release_lock(key: str, *, token: str | None = None) -> None:
    """释放一把 Redis 锁（比较并删除，防止误删）。

    参数
    ----
    key:
        锁键。
    token:
        与 acquire 时一致的值；不匹配则不删除（安全兜底）。
    """
    rc = client()
    val = token or ""
    # 用 Lua 脚本保证"比较-删除"原子性
    script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """
    try:
        await rc.eval(script, 1, key, val)
    except Exception as exc:  # pragma: no cover
        log.warning("release_lock failed", extra={"zy": {"key": key, "error": str(exc)}})


def profile_invalidate_channel(org_unit_id: str) -> str:
    """返回某组织单元的 Profile 失效频道名。"""
    return f"{PROFILE_INVALIDATE_CHANNEL}{org_unit_id}"


def localagent_route_channel(user_id: str) -> str:
    """返回某用户的本地 Agent 路由频道名。"""
    return f"{LOCALAGENT_ROUTE_CHANNEL}{user_id}"


def localagent_online_hash(org_unit_id: str) -> str:
    """返回某组织单元的在线 Agent Redis Hash 键。"""
    return f"{LOCALAGENT_ONLINE_KEY}{org_unit_id}"
