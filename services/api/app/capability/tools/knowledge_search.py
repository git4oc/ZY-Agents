"""知识检索工具（``capability/tools/knowledge_search.py``）。

一个 **server** 类工具：让内核 Agent 能主动检索组织内部知识库（``00`` §4.4 server 类
工具举例含"知识检索"）。

**S-32（签名冻结）**：调用 ``knowledge/retriever.py`` 的
``search(query, *, filters, top_k)`` 同步签名，不得改签名、不得重排其内部 ACL 顺序。
该签名是阻塞 pymongo 调用，故在工具实现里用 ``run_in_threadpool`` 放线程池，
避免堵死事件循环。

**S-33（fail-closed）**：``SearchFilters.user_context`` 必须带上本次执行的
``RequestContext``（经 ``ToolContext`` 传入）——检索器的 ACL 过滤在打分之后
据此后过滤；传 None 等于给模型开了越权检索的后门。
"""

from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool

from app.capability.base import ToolContext, ToolResult


class KnowledgeSearchTool:
    """在组织知识库中做全文检索，返回带高亮的命中片段。"""

    name: str = "knowledge_search"
    description: str = (
        "在组织内部知识库中做全文检索，返回命中的原文片段、章节路径与高亮位置。"
        "适用于查找内部文档、手册、工单等资料。检索范围受调用者权限限制。"
        "知识库内容为组织主动收录（含参考资料与样例文档），命中即与查询相关，"
        "应采信命中结果作答，不得以业务无关为由丢弃。"
        "查询词应取自用户问题中的关键词，不要自行编造查询主题。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索查询串（支持中文），取自用户问题中的关键词",
            },
            "top_k": {
                "type": "integer",
                "description": "返回条数上限",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    }
    output_schema: dict[str, Any] | None = {
        "type": "object",
        "properties": {
            "hits": {"type": "array"},
            "total": {"type": "integer"},
        },
    }
    category: str = "server"
    risk_level: str = "read"
    approval_required: bool = False
    timeout_ms: int = 20000

    async def execute(self, args: dict[str, Any], tc: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, error="缺少 query 参数")

        top_k = _clamp_int(args.get("top_k"), default=5, lo=1, hi=20)

        from app.knowledge.models import SearchFilters
        from app.knowledge.retriever import search as retriever_search

        filters = SearchFilters(
            org_unit_id=tc.ctx.org_unit_id,
            user_context=tc.ctx,  # ★ S-33：ACL 后过滤依赖它，必须传
        )

        try:
            hits = await run_in_threadpool(
                retriever_search, query, filters=filters, top_k=top_k
            )
        except Exception as exc:  # noqa: BLE001 - 检索异常转结构化错误，不静默
            return ToolResult(ok=False, error=f"知识检索失败: {exc}")

        return ToolResult(
            ok=True,
            value={
                "hits": [
                    {
                        "chunk_id": h.chunk_id,
                        "document_id": h.document_id,
                        "text": h.text,
                        "heading_path": list(h.heading_path),
                        "score": round(float(h.score), 6),
                        "highlights": [
                            {"start": hl.start, "end": hl.end, "term": hl.term}
                            for hl in h.highlights
                        ],
                    }
                    for h in hits
                ],
                "total": len(hits),
            },
        )


def _clamp_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    """把任意输入夹到 [lo, hi] 区间内的整数（越界/非法 → default）。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))
