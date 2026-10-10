"""单写者 claim（``kernel/turn/claims.py``，``00`` §7.5 / ``02`` §2.7）。

turn 抢占用 **Redis**（``SET NX EX``），不占 Mongo——它需要 TTL 语义和原子性。
``turn_records`` 只负责留痕。

接口对齐 ``01`` §4.3：:

    claim_turn(conversation_id, request_id, *, ttl_seconds) -> Claim | None
    release_turn(conversation_id, request_id) -> None
    transfer_turn(conversation_id, *, to_request_id) -> None   # 显式接管
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core import redis as redis_core
from app.core.clock import utcnow


@dataclass(frozen=True, slots=True)
class Claim:
    """一个 turn 的单写者 claim。"""

    conversation_id: str
    request_id: str
    holder_user_id: str
    claimed_at: datetime
    expires_at: datetime


def _lock_key(conversation_id: str) -> str:
    return f"turn:claim:{conversation_id}"


async def claim_turn(
    conversation_id: str,
    request_id: str,
    *,
    holder_user_id: str,
    ttl_seconds: int = 300,
) -> bool:
    """尝试抢注一个会话的 turn 单写者 claim。

    参数
    ----
    conversation_id:
        会话 id。
    request_id:
        本次 turn 的请求 id。
    holder_user_id:
        发起者（owner）。
    ttl_seconds:
        claim 自动过期秒数。

    返回是否抢注成功（True = 本请求成为单写者）。
    """
    key = _lock_key(conversation_id)
    # value = request_id，放入锁值便于归属校验
    return await redis_core.acquire_lock(key, token=request_id, ttl_seconds=ttl_seconds)


async def release_turn(conversation_id: str, request_id: str) -> None:
    """释放 turn claim（校验归属，避免误删他人锁）。"""
    key = _lock_key(conversation_id)
    await redis_core.release_lock(key, token=request_id)


async def transfer_turn(conversation_id: str, *, to_request_id: str) -> None:
    """显式接管：把单写者 claim 转移给另一请求。

    接管走显式流程（``00`` §7.5）：请求接管 → 原持有者收到通知 → 确认后转移。
    本函数在**确认转移后**调用，此时新的持有者（``to_request_id``）即将接管，
    因此直接以 ``to_request_id`` 抢占（覆盖原锁）。原来的持有者已被告知放弃，
    不会再来续跑，故"覆盖是否成功"以最终抢占为准。
    """
    key = _lock_key(conversation_id)
    # 用 SET NX EX 抢占：若原锁仍存在则先由承接流程释放；这里直接置新值保证接管生效
    rc = redis_core.client()
    await rc.set(key, to_request_id, nx=False, ex=300)
