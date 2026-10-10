"""turn 取消（``kernel/turn/cancellation.py``）。

持有者或 owner 可取消进行中的 turn；取消后落 ``turn_records`` 终态并释放 claim。
"""

from __future__ import annotations

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.kernel.turn import claims
from app.kernel.turn.finalization import finalize

log = get_logger("kernel.turn.cancellation")


async def cancel_turn(
    *,
    org_unit_id: str,
    conversation_id: str,
    request_id: str,
    owner_user_id: str,
    cause: str = "user_cancelled",
) -> None:
    """取消一次进行中的 turn。

    参数
    ----
    org_unit_id / conversation_id / request_id / owner_user_id:
        turn 定位与归属。
    cause:
        取消原因。
    """
    # 落终态并释放 claim（复用 finalization 的收尾）
    await finalize(
        org_unit_id=org_unit_id,
        conversation_id=conversation_id,
        request_id=request_id,
        owner_user_id=owner_user_id,
        status="cancelled",
        cancel_cause=cause,
    )
    log.info(
        "turn cancelled",
        extra={"zy": {"conversation_id": conversation_id, "request_id": request_id, "cause": cause}},
    )
