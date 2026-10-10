"""知识库数据结构（``knowledge/models.py``，``00`` §6.4）。

``SearchFilters`` / ``Hit`` 是检索接口的固定契约：``search(query, *, filters, top_k) -> list[Hit]``
（``S-32`` 签名冻结，阶段 5 换混合检索时调用方零改动）。

- ``Hit`` 结构：``chunk_id, document_id, text, heading_path, score, highlights[]``（``00`` §6.4）。
- ``SearchFilters``：``org_unit_id, user_context, source_types[], doc_ids[], time_range``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: 命中的高亮范围（字符区间 [start, end) + 原始词）
@dataclass(frozen=True, slots=True)
class Highlight:
    start: int
    end: int
    term: str


@dataclass(frozen=True, slots=True)
class Hit:
    """一条检索命中（``00`` §6.4）。"""

    chunk_id: str
    document_id: str
    text: str
    heading_path: list[str] = field(default_factory=list)
    score: float = 0.0
    highlights: list[Highlight] = field(default_factory=list)


#: 时间范围（可选；用于按文档创建时间过滤）
@dataclass(frozen=True, slots=True)
class TimeRange:
    start: Any | None = None  # datetime
    end: Any | None = None  # datetime


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """检索过滤器（``00`` §6.4）。"""

    org_unit_id: str
    user_context: Any | None = None  # RequestContext（用于 S-33 ACL 后过滤）
    source_types: list[str] | None = None  # 仅检索这些 source_type
    doc_ids: list[str] | None = None  # 仅检索这些文档
    time_range: TimeRange | None = None


@dataclass(slots=True)
class IndexResult:
    """一次文档索引的结果。"""

    doc_id: str
    status: Literal["indexed", "reindexed", "failed"] = "indexed"
    chunk_count: int = 0
    error: str | None = None
