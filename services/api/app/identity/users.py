"""用户仓储（``identity/users.py``，见 ``01`` §4.1、``02`` §2.1）。

阶段 1 主服务身份模块。所有方法第一个参数为 ``ctx: RequestContext``。
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from app.core.clock import utcnow
from app.core.errors import NotFound
from app.identity.context import RequestContext
from app.identity.repository import ScopedRepository

#: 用户集合名
COLLECTION = "users"


class UsersRepository(ScopedRepository[dict[str, Any]]):
    """基于 ``ScopedRepository`` 的用户数据访问。"""

    def __init__(self, collection: AsyncIOMotorCollection | None = None) -> None:
        # 允许外部传入集合（测试用）；缺省从 core.mongo 取
        if collection is None:
            from app.core import mongo

            collection = mongo.get_collection(COLLECTION)
        super().__init__(collection)

    async def get_by_username(self, ctx: RequestContext, username: str) -> dict[str, Any] | None:
        """按登录名查询单个用户（org 隔离）。"""
        return await self.find_one(ctx, username=username)

    async def get_active(self, ctx: RequestContext, user_id: str) -> dict[str, Any]:
        """按 id 查询**未禁用**用户；不存在或已禁用抛 NotFound。"""
        doc = await self._collection.find_one(
            {"_id": _to_objid(user_id), "status": "active", "org_unit_id": ctx.org_unit_id}
        )
        if doc is None:
            raise NotFound("用户不存在")
        return doc

    async def list(self, ctx: RequestContext) -> list[dict[str, Any]]:
        """列出组织单元内的所有用户。"""
        return await self.find_many(ctx)


def _to_objid(value: str) -> Any:
    """字符串 → ObjectId（兼容字符串 id）。"""
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return value
