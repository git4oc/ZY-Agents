"""品牌单文档数据访问（``branding/repository.py``，``02`` §2.9）。

``branding_settings`` 是**单文档集合**：``_id="singleton"``（固定主键强制"集合永远最多一条"）。
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from app.core import mongo

COLLECTION = "branding_settings"

#: 固定主键——单文档约束
SINGLETON_ID = "singleton"


class BrandingRepository:
    """branding_settings 单文档集合的访问。"""

    def __init__(self, collection: AsyncIOMotorCollection | None = None) -> None:
        self._collection = collection or mongo.get_collection(COLLECTION)

    @property
    def collection(self) -> AsyncIOMotorCollection:
        return self._collection

    async def get(self) -> dict[str, Any] | None:
        """读取单文档（不存在返回 None）。"""
        return await self.collection.find_one({"_id": SINGLETON_ID})

    async def upsert(self, document: dict[str, Any]) -> None:
        """写入单文档（覆写整条文档）。"""
        doc = dict(document)
        doc["_id"] = SINGLETON_ID
        await self.collection.replace_one({"_id": SINGLETON_ID}, doc, upsert=True)

    async def replace(self, document: dict[str, Any]) -> None:
        """别名：覆写单文档（幂等语义由调用方保证 seed 只在空库时写）。"""
        await self.upsert(document)
