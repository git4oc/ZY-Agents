"""标识符生成。

阶段 1 主服务的基础设施模块之一（``core/ids.py``，见 ``01-engineering-design.md`` §2 目录树）。

设计要点
--------
- 文档约定主键 ``_id`` 用 ObjectId（``02`` §1），对外统一序列化为字符串 ``id``。
- 业务对外稳定标识（``request_id`` / ``tracking_id`` / ``approval_id`` 等）用 **ULID**：
  时间有序 + 随机，天然可排序且无需数据库自增。ULID 为 26 字符 Crockford Base32。
- 这里只提供"生成新 id"的纯净函数，不做任何存储。
"""

from __future__ import annotations

import uuid
from typing import Any

try:  # pragma: no cover - 依赖可选，缺失时回退到 uuid 派生实现
    from ulid import ULID

    def new_id() -> str:
        """生成一个 ULID 字符串（26 字符，时间有序）。"""
        return str(ULID())

    _HAS_ULID = True
except Exception:  # pragma: no cover
    _HAS_ULID = False

    def new_id() -> str:
        """回退实现：UUID4 的 hex（无时间有序性，仅保证唯一）。"""
        return uuid.uuid4().hex


def new_uuid() -> str:
    """生成一个 UUID4 hex 字符串，用于无需排序、仅需唯一的标识（如事件 id）。"""
    return uuid.uuid4().hex


def new_asset_key() -> str:
    """生成服务端持有的资产存储键（品牌 logo / favicon 文件）。

    文件名由**服务端生成**（UUID），**不接受请求参数参与路径拼接**（见 ``00`` §12.7 / ``02`` §2.9）。
    """
    return f"asset-{uuid.uuid4().hex}"


def new_payload_id() -> str:
    """生成幂等去重用的载荷摘要键（approval arguments_digest）。"""
    return uuid.uuid4().hex


def object_id_str(obj: Any) -> str | None:
    """将任意 ``_id``（ObjectId / str / None）归一化为字符串 id。

    ``02`` §1 约定：对外统一序列化为字符串；此处避免各端点重复判断类型。
    """
    if obj is None:
        return None
    return str(obj)
