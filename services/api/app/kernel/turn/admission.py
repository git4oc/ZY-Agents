"""turn 准入（``kernel/turn/admission.py``，``01`` §4.3）。

``admit(ctx, conversation_id, *, request_id) -> TurnAdmission``

校验：
- 会话存在且当前用户是参与者（协同方法负责既往下放校验）。
- Profile 是否存在（编译主体＝会话发起者）。
- 并发 claim：抢不到 → ``ConversationBusyError``（``00`` §7.5：多人同时提问是排队，不是并发）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.clock import utcnow
from app.core.errors import ConversationBusyError, NotFound
from app.core.logging import get_logger
from app.identity.context import RequestContext
from app.kernel.profile.compiler import compile_profile
from app.kernel.turn import claims

log = get_logger("kernel.turn.admission")

#: claim 默认 TTL（秒）
_DEFAULT_CLAIM_TTL = 300


@dataclass(frozen=True, slots=True)
class TurnAdmission:
    """一次通过准入的 turn 上下文。"""

    conversation_id: str
    request_id: str
    subject_user_id: str
    profile_version: str


async def admit(
    ctx: RequestContext,
    conversation_id: str,
    *,
    request_id: str,
) -> TurnAdmission:
    """对一次 turn 进行准入，成功后抢占单写者 claim。

    参数
    ----
    ctx:
        发起者上下文。
    conversation_id / request_id:
        会话与 turn 请求标识。

    异常
    ----
    ConversationBusyError:
        claim 抢注失败（会话正在被他人驱动，``00`` §7.5）。
    NotFound:
        会话不存在或未授权（由调用方校验参与者关系后调用）。
    """
    # 1) Profile 是否存在（编译主体＝发起者）——必须存在且可编译
    profile = await compile_profile(ctx.org_unit_id, ctx.user_id)
    if profile is None:
        raise NotFound("无法为该用户编译 Runtime Profile")

    # 2) 并发 claim：抢不到 → ConversationBusyError
    ok = await claims.claim_turn(
        conversation_id,
        request_id,
        holder_user_id=ctx.user_id,
        ttl_seconds=_DEFAULT_CLAIM_TTL,
    )
    if not ok:
        # 无法从 Redis 直接取 holder，交错误体携带 origin（会话繁忙提示）
        raise ConversationBusyError(ctx.user_id, message="会话正在运行中")

    return TurnAdmission(
        conversation_id=conversation_id,
        request_id=request_id,
        subject_user_id=ctx.user_id,
        profile_version=profile.profile_version,
    )
