"""组织单元仓储（``identity/org_units.py``，见 ``02`` §2.1 集合 ``org_units``）。

阶段 1 主服务身份模块。
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from app.core.clock import utcnow
from app.core.errors import NotFound
from app.identity.context import RequestContext
from app.identity.repository import ScopedRepository

COLLECTION = "org_units"


class OrgUnitsRepository(ScopedRepository[dict[str, Any]]):
    """组织单元数据访问。"""

    def __init__(self, collection: AsyncIOMotorCollection | None = None) -> None:
        if collection is None:
            from app.core import mongo

            collection = mongo.get_collection(COLLECTION)
        super().__init__(collection)

    async def get(self, org_unit_id: str) -> dict[str, Any]:
        """按 id 查询组织单元（org_unit 本身无 org 隔离——它是隔离维度的源头）。"""
        from bson import ObjectId

        doc = await self._collection.find_one({"_id": _to_objid(org_unit_id)})
        if doc is None:
            raise NotFound("组织单元不存在")
        return doc

    async def create(
        self,
        ctx: RequestContext,
        *,
        name: str,
        code: str,
        parent_id: str | None = None,
    ) -> str:
        """创建组织单元，返回 id。
        说明：根组织单元由 bootstrap 预置；此处支持子单元创建。
        """
        from bson import ObjectId

        doc = {
            "name": name,
            "code": code,
            "parent_id": _to_objid(parent_id) if parent_id else None,
            "status": "active",
            "created_at": utcnow(),
        }
        # org_units 的 _id 就是 org_unit_id 本身，无独立 org_unit_id 字段
        result = await self._collection.insert_one(doc)
        return str(result.inserted_id)


def _to_objid(value: str) -> Any:
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return value
