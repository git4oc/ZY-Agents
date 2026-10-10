"""Profile 缓存（``kernel/profile/store.py``，见 ``03`` §3.5）。

- 进程内按 ``content_hash`` 缓存编译结果（内存 Profile 是唯一需跨请求复用的对象）。
- 失效通过 Redis 频道 ``profile:invalidate:{org_unit_id}`` 广播（``core.redis``），
  各实例清理本地缓存，下次 ``compile_profile`` 重算、hash 自然变化。
- **不要用"手写失效清单"**（``03`` §3.5）——写入 model/tool/skill/grants 的任何入口
  都应调用 ``invalidate`` 广播，而非逐个维护。

说明：本模块作为阶段 1 的内存实现，不引入额外基础设施；
跨实例最终一致性由 Redis 订阅达成（阶段 4 完整落地订阅循环）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

log = get_logger("kernel.profile.store")

#: 进程内 profile 缓存（content_hash -> RuntimeProfileSnapshot dict）
_cache: dict[str, Any] = {}
#: 进程内集合级失效标记的简单时间戳（用于乐观失效，非严格）
_lock = asyncio.Lock()


def put(content_hash: str, snapshot: Any) -> None:
    """存入缓存（按 content_hash 键）。"""
    _cache[content_hash] = snapshot


def get(content_hash: str) -> Any | None:
    """按 content_hash 取缓存；未命中返回 None。"""
    return _cache.get(content_hash)


def clear() -> None:
    """清空进程内 Profile 缓存。"""
    _cache.clear()


async def invalidate(org_unit_id: str) -> None:
    """使某组织单元的 Profile 缓存失效。

    - 清空进程内缓存；
    - 向 Redis 频道 ``profile:invalidate:{org_unit_id}`` 广播，通知其它实例清理
      （``03`` §3.5；阶段 1 由发布者自身已清理，订阅方清理由阶段 4 订阅循环落地）。

    调用时机：写入 model_providers / model_instances / tools / skills / resource_grants 后。
    """
    clear()
    try:
        from app.core import redis as redis_core

        rc = redis_core.client()
        await rc.publish(redis_core.profile_invalidate_channel(org_unit_id), "")
    except Exception as exc:  # pragma: no cover - Redis 不可用时不阻塞写入
        log.warning("profile invalidate broadcast failed", extra={"zy": {"error": str(exc)}})
