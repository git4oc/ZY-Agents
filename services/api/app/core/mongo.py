"""MongoDB 客户端与索引初始化。

阶段 1 主服务的基础设施模块之一（``core/mongo.py``，见 ``01-engineering-design.md`` §2 目录树、``02`` §1、§5）。

设计要点
--------
- 持有全局 ``AsyncIOMotorClient`` 与当前库句柄；``connect()`` / ``close()`` 由
  ``main.py`` lifespan 调用。
- ``ensure_indexes()`` 幂等声明全部首版索引（``02`` §5），只 ``create_index``，
  **禁止** ``drop`` 重建；索引变更走独立迁移脚本（``02`` §1）。
- 提供 ``database()`` / ``get_collection(name)`` 取库 / 集合便捷入口。
"""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, TEXT
from pymongo.errors import PyMongoError
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection

from app.core.errors import StorageError
from app.core.logging import get_logger

log = get_logger("core.mongo")

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def connect(uri: str, db_name: str) -> AsyncIOMotorDatabase:
    """建立（或复用）Mongo 客户端连接并缓存库句柄。

    参数
    ----
    uri:
        ``MONGO_URI``（如 ``mongodb://mongo:27017``）。
    db_name:
        ``MONGO_DB``（默认 ``zy_agents``）。

    返回库句柄；幂等：重复调用安全。
    """
    global _client, _db
    if _db is not None and _db.name == db_name:
        return _db
    if _client is None:
        _client = AsyncIOMotorClient(uri)
    _db = _client[db_name]
    log.info("mongo connected", extra={"zy": {"db": db_name}})
    return _db


def database() -> AsyncIOMotorDatabase:
    """返回当前库句柄；未连接时抛错（防误用）。"""
    if _db is None:
        raise StorageError("Mongo 尚未连接，请先调用 mongo.connect()")
    return _db


def get_collection(name: str) -> AsyncIOMotorCollection:
    """按名取集合句柄。"""
    return database()[name]


async def close() -> None:
    """关闭 Mongo 客户端连接。"""
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        log.info("mongo closed")


def _def(name: str, keys: list[tuple[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """构造一条 ``create_index`` 声明（供 ensure_indexes 使用）。"""
    spec: dict[str, Any] = {"name": name, "keys": keys}
    spec.update(kwargs)
    return spec


#: 首版全部索引声明（``02`` §5 索引清单）：``[(集合名, [索引声明, ...]), ...]``。
#:
#: ★ 提到模块级是为了**可被检查**：``tests/test_unique_index_alignment.py`` 遍历它，
#: 断言"每条 unique 索引的字段都在对应写端点模型里必填"。若加了索引字段而写端点
#: 没同步必填，该字段会按 null 参与唯一性比较，第二条就撞 E11000（原缺陷：500）。
INDEX_SPECS: list[tuple[str, list[dict[str, Any]]]] = [
        (
            "org_units",
            [
                {"name": "org_units_code_u", "keys": [("code", ASCENDING)], "unique": True},
            ],
        ),
        (
            "users",
            [
                {
                    "name": "users_org_user_u",
                    "keys": [("org_unit_id", ASCENDING), ("username", ASCENDING)],
                    "unique": True,
                },
            ],
        ),
        (
            "roles",
            [
                {
                    "name": "roles_org_code_u",
                    "keys": [("org_unit_id", ASCENDING), ("code", ASCENDING)],
                    "unique": True,
                },
            ],
        ),
        (
            "role_bindings",
            [
                {
                    "name": "role_bindings_u",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("subject_id", ASCENDING),
                        ("role_code", ASCENDING),
                        ("resource_id", ASCENDING),
                    ],
                    "unique": True,
                },
            ],
        ),
        (
            "resource_grants",
            [
                {
                    "name": "resource_grants_u",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("resource_type", ASCENDING),
                        ("resource_id", ASCENDING),
                        ("subject_type", ASCENDING),
                        ("subject_id", ASCENDING),
                    ],
                    "unique": True,
                },
            ],
        ),
        (
            "model_providers",
            [
                {
                    "name": "model_providers_org_code_u",
                    "keys": [("org_unit_id", ASCENDING), ("code", ASCENDING)],
                    "unique": True,
                },
            ],
        ),
        (
            "model_instances",
            [
                {
                    "name": "model_instances_u",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("provider_id", ASCENDING),
                        ("model_name", ASCENDING),
                    ],
                    "unique": True,
                },
            ],
        ),
        (
            "tools",
            [
                {
                    "name": "tools_org_name_u",
                    "keys": [("org_unit_id", ASCENDING), ("name", ASCENDING)],
                    "unique": True,
                },
            ],
        ),
        (
            "skills",
            [
                {
                    "name": "skills_org_key_u",
                    "keys": [("org_unit_id", ASCENDING), ("key", ASCENDING)],
                    "unique": True,
                },
            ],
        ),
        (
            "skill_versions",
            [
                {
                    "name": "skill_versions_u",
                    "keys": [("skill_id", ASCENDING), ("version", ASCENDING)],
                    "unique": True,
                },
            ],
        ),
        (
            "conversations",
            [
                {
                    "name": "conversations_owner",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("owner_user_id", ASCENDING),
                        ("last_active_at", -1),
                    ],
                },
                {
                    "name": "conversations_session",
                    "keys": [("session_id", ASCENDING)],
                    "unique": True,
                    "sparse": True,
                },
                {
                    "name": "conversations_visibility",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("visibility", ASCENDING),
                        ("last_active_at", -1),
                    ],
                },
            ],
        ),
        (
            "session_participants",
            [
                {
                    "name": "parts_conv_user_u",
                    "keys": [("conversation_id", ASCENDING), ("user_id", ASCENDING)],
                    "unique": True,
                },
                {
                    "name": "parts_org_user_removed",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("user_id", ASCENDING),
                        ("removed_at", ASCENDING),
                    ],
                },
            ],
        ),
        (
            "messages",
            [
                {
                    "name": "messages_conv_seq_u",
                    "keys": [("conversation_id", ASCENDING), ("seq", ASCENDING)],
                    "unique": True,
                },
            ],
        ),
        (
            "kernel_events",
            [
                {
                    "name": "kernel_events_conv_cursor_u",
                    "keys": [("conversation_id", ASCENDING), ("cursor", ASCENDING)],
                    "unique": True,
                },
                {
                    "name": "kernel_events_org_occurred",
                    "keys": [("org_unit_id", ASCENDING), ("occurred_at", -1)],
                },
                {
                    "name": "kernel_events_session",
                    "keys": [("session_id", ASCENDING), ("occurred_at", -1)],
                },
            ],
        ),
        (
            "artifacts",
            [
                {
                    "name": "artifacts_conv",
                    "keys": [("conversation_id", ASCENDING), ("created_at", -1)],
                },
                {
                    "name": "artifacts_sha256",
                    "keys": [("sha256", ASCENDING)],
                },
            ],
        ),
        (
            "approvals",
            [
                {
                    "name": "approvals_conv_status",
                    "keys": [
                        ("conversation_id", ASCENDING),
                        ("status", ASCENDING),
                        ("created_at", -1),
                    ],
                },
                {
                    "name": "approvals_call_digest_u",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("call_id", ASCENDING),
                        ("arguments_digest", ASCENDING),
                    ],
                    "unique": True,
                },
            ],
        ),
        (
            "audit_logs",
            [
                {
                    "name": "audit_org_occurred",
                    "keys": [("org_unit_id", ASCENDING), ("occurred_at", -1)],
                },
                {
                    "name": "audit_org_action",
                    "keys": [("org_unit_id", ASCENDING), ("action", ASCENDING), ("occurred_at", -1)],
                },
                {
                    "name": "audit_trace",
                    "keys": [("trace_id", ASCENDING)],
                },
            ],
        ),
        (
            "quota_usage",
            [
                {
                    "name": "quota_u",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("user_id", ASCENDING),
                        ("period", ASCENDING),
                    ],
                    "unique": True,
                },
            ],
        ),
        (
            "devices",
            [
                {
                    "name": "devices_org_device_u",
                    "keys": [("org_unit_id", ASCENDING), ("device_id", ASCENDING)],
                    "unique": True,
                },
                {
                    "name": "devices_org_user_status",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("user_id", ASCENDING),
                        ("status", ASCENDING),
                    ],
                },
            ],
        ),
        (
            "device_credentials",
            [
                {
                    "name": "device_credentials_org",
                    "keys": [
                        ("org_unit_id", ASCENDING),
                        ("device_id", ASCENDING),
                        ("revoked_at", ASCENDING),
                    ],
                },
            ],
        ),
        (
            "pairing_sessions",
            [
                {
                    "name": "pairing_id_u",
                    "keys": [("pairing_id", ASCENDING)],
                    "unique": True,
                },
                {
                    "name": "pairing_expires_TTL",
                    "keys": [("expires_at", ASCENDING)],
                    "expireAfterSeconds": 0,
                },
            ],
        ),
        (
            "kernel_runtimes",
            [
                {
                    "name": "runtime_id_u",
                    "keys": [("runtime_id", ASCENDING)],
                    "unique": True,
                },
                {
                    "name": "runtimes_org_profile",
                    "keys": [("org_unit_id", ASCENDING), ("profile_version", ASCENDING)],
                },
            ],
        ),
        (
            "turn_records",
            [
                {
                    "name": "turn_records_conv",
                    "keys": [("conversation_id", ASCENDING), ("started_at", -1)],
                },
            ],
        ),
        (
            "knowledge_documents",
            [
                {
                    "name": "kd_org_sourceref_u",
                    "keys": [("org_unit_id", ASCENDING), ("source_ref", ASCENDING)],
                    "unique": True,
                },
                {
                    "name": "kd_org_status",
                    "keys": [("org_unit_id", ASCENDING), ("status", ASCENDING)],
                },
            ],
        ),
        (
            "knowledge_chunks",
            [
                {"name": "kc_org_terms", "keys": [("org_unit_id", ASCENDING), ("terms", ASCENDING)]},
                {
                    "name": "kc_org_title_terms",
                    "keys": [("org_unit_id", ASCENDING), ("title_terms", ASCENDING)],
                },
                {"name": "kc_doc_seq", "keys": [("document_id", ASCENDING), ("seq", ASCENDING)]},
                {"name": "kc_org_text", "keys": [("org_unit_id", ASCENDING), ("text", TEXT)]},
            ],
        ),
        (
            "knowledge_terms",
            [
                {
                    "name": "kt_org_term_u",
                    "keys": [("org_unit_id", ASCENDING), ("term", ASCENDING)],
                    "unique": True,
                },
            ],
        ),
        # branding_settings：单文档集合，固定 _id="singleton"，由唯一主键约束即可。
]


def ensure_indexes() -> None:
    """幂等创建首版全部索引（``02`` §5 索引清单）。

    同步执行于 lifespan startup。索引已存在时 Motor 的 ``create_index`` 为幂等操作。
    """
    db = database()
    for collection, indexes in INDEX_SPECS:
        coll = db[collection]
        for spec in indexes:
            name: str = spec.pop("name")
            keys: list[tuple[str, Any]] = spec.pop("keys")
            try:
                coll.create_index(keys, name=name, **spec)
            except PyMongoError as exc:  # pragma: no cover - 依赖真实 Mongo
                log.error(
                    "create_index failed",
                    extra={"zy": {"collection": collection, "index": name, "error": str(exc)}},
                )
                raise
    log.info(
        "mongo indexes ensured",
        extra={"zy": {"count": sum(len(i) for _, i in INDEX_SPECS)}},
    )
