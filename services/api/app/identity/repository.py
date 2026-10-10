"""ScopedRepository —— 强制上下文的仓储基类（``01`` §4.1）。

阶段 1 主服务身份模块（``identity/repository.py``）。

设计要点
--------
- **所有查询方法第一个参数是 ``ctx: RequestContext``**；不提供不带 ctx 的重载——刻意的
  （``00`` §7.1：让越权成为类型期问题）。
- ``_scope(ctx)`` 返回 ``{"org_unit_id": ctx.org_unit_id}``，所有查询自动并入该条件，
  调用方无法遗漏隔离维度。
- 泛型 ``TDoc`` 为文档类型（dict 或 Pydantic 模型）。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from motor.motor_asyncio import AsyncIOMotorCollection

from app.identity.context import RequestContext

TDoc = TypeVar("TDoc")


class ScopedRepository(Generic[TDoc]):
    """按 ``org_unit_id`` 强制限域的仓储基类。

    任何继承类都应把自身的方法第一个参数声明为 ``ctx: RequestContext``，
    并在内部通过 ``find_one`` / ``find_many`` 完成隔离。
    """

    def __init__(self, collection: AsyncIOMotorCollection) -> None:
        self._collection = collection

    @property
    def collection(self) -> AsyncIOMotorCollection:
        """暴露底层集合（供写操作使用）。"""
        return self._collection

    def _scope(self, ctx: RequestContext) -> dict[str, Any]:
        """构造强制隔离条件：所有查询自动并入 ``org_unit_id``。"""
        return {"org_unit_id": ctx.org_unit_id}

    async def find_one(self, ctx: RequestContext, **filters: Any) -> TDoc | None:
        """按条件查询单条文档（自动并入 org_unit_id 隔离）。"""
        merged = self._scope(ctx)
        merged.update(filters)
        return await self._collection.find_one(merged)  # type: ignore[return-value]

    async def find_many(self, ctx: RequestContext, **filters: Any) -> list[TDoc]:
        """按条件查询多条文档（自动并入 org_unit_id 隔离）。返回列表快照。"""
        merged = self._scope(ctx)
        merged.update(filters)
        cursor = self._collection.find(merged)
        return await cursor.to_list(length=None)  # type: ignore[return-value]

    async def insert_one(self, ctx: RequestContext, document: dict[str, Any]) -> Any:
        """插入单条文档。调用方需自行保证 document 含 required 字段。"""
        scoped = {**document, "org_unit_id": ctx.org_unit_id}
        result = await self._collection.insert_one(scoped)
        return result.inserted_id

    async def replace_one(
        self,
        ctx: RequestContext,
        filter_: dict[str, Any],
        replacement: dict[str, Any],
    ) -> bool:
        """按条件替换文档（自动并入 org_unit_id 隔离）。返回是否命中。"""
        merged = self._scope(ctx)
        merged.update(filter_)
        result = await self._collection.replace_one(merged, replacement)
        return result.modified_count > 0

    async def update_one(
        self,
        ctx: RequestContext,
        filter_: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> bool:
        """按条件更新文档（自动并入 org_unit_id 隔离）。返回是否命中。"""
        merged = self._scope(ctx)
        merged.update(filter_)
        result = await self._collection.update_one(merged, update, upsert=upsert)
        return result.modified_count > 0 or (upsert and result.upserted_id is not None)

    async def delete_one(self, ctx: RequestContext, **filters: Any) -> bool:
        """按条件删除文档（自动并入 org_unit_id 隔离）。返回是否命中。"""
        merged = self._scope(ctx)
        merged.update(filters)
        result = await self._collection.delete_one(merged)
        return result.deleted_count > 0
