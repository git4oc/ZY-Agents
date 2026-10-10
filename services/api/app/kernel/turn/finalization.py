"""turn 收尾（``kernel/turn/finalization.py``）。

在 turn 顺利结束后：落 ``turn_records`` 终态、释放单写者 claim。
（推送终态事件给前端由 ``collaboration/stream`` 承接，终态事件持久化走 ``kernel_events``。）
"""

from __future__ import annotations

from app.core import mongo
from app.core.clock import utcnow
from app.core.logging import get_logger
from app.kernel.turn import claims

log = get_logger("kernel.turn.finalization")

TURN_RECORDS_COLLECTION = "turn_records"


async def finalize(
    *,
    org_unit_id: str,
    conversation_id: str,
    request_id: str,
    owner_user_id: str,
    status: str = "completed",
    cancel_cause: str | None = None,
) -> None:
    """记录 turn 终态（只留痕）并释放单写者 claim。

    参数
    ----
    status:
        ``completed`` / ``cancelled`` / ``failed``。
    cancel_cause:
        status=cancelled 时的原因。
    """
    coll = mongo.get_collection(TURN_RECORDS_COLLECTION)
    # 留痕：upsert 保证存在记录（created 或补全）
    await coll.update_one(
        {"conversation_id": conversation_id, "request_id": request_id},
        {
            "$setOnInsert": {
                "org_unit_id": org_unit_id,
                "owner_user_id": owner_user_id,
                "started_at": utcnow(),
            },
            "$set": {
                "status": status,
                "cancel_cause": cancel_cause,
                "finished_at": utcnow(),
            },
        },
        upsert=True,
    )

    await claims.release_turn(conversation_id, request_id)
    log.info(
        "turn finalized",
        extra={"zy": {"conversation_id": conversation_id, "request_id": request_id, "status": status}},
    )
