"""知识库 HTTP 端点（``api/endpoints/knowledge.py``，``02`` §4.7）。

三个"知识源"读取端点（前端 ``apps/web/src/api/knowledgeSources.ts`` 已在调）+ 一个检索端点：

- ``GET  /api/v1/knowledge/sources/documents/{document_id}``：单文档元信息。
- ``GET  /api/v1/knowledge/sources/documents/{document_id}/preview``：预览**二进制流**
  （前端 ``resp.blob()``，因此必须 FileResponse/StreamingResponse，不能包 JSON）。
- ``GET  /api/v1/knowledge/sources/documents/{document_id}/chunks/{chunk_id}``：单 chunk 详情。
- ``POST /api/v1/knowledge/search``：``{query, top_k?, source_types?, doc_ids?}`` → 命中列表。

**S-32（签名冻结）**：检索必须走 ``knowledge/retriever.py`` 的
``search(query, *, filters, top_k)`` 同步签名，不得改签名、不得在端点里重排 ACL 顺序。
它是**阻塞 pymongo** 调用，故用 ``run_in_threadpool`` 包起来，**不写成 async 直接调**
（否则会堵死事件循环）。

**S-33（fail-closed ACL）**：``ctx`` 必须传进 ``SearchFilters.user_context``——
检索器的 ACL 过滤在打分之后据此后过滤；传 None 等于关掉 ACL。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.errors import Forbidden, NotFound
from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

#: 集合名（与 knowledge/indexer.py、retriever.py 一致）
_DOCS = "knowledge_documents"
_CHUNKS = "knowledge_chunks"


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    """检索请求体（``02`` §4.7：``{query, filters, top_k}``）。"""

    query: str
    top_k: int = 10
    source_types: list[str] | None = None
    doc_ids: list[str] | None = None


# ---------------------------------------------------------------------------
# 知识源：文档元信息
# ---------------------------------------------------------------------------


@router.get("/sources/documents/{document_id}")
async def get_document(
    document_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """返回单个知识源文档的元信息（对齐前端 ``KnowledgeSourceDocument``）。"""
    doc = await _load_document(ctx, document_id)
    return {
        "id": str(doc.get("_id", document_id)),
        "scope": str(doc.get("scope") or doc.get("source_type") or "org"),
        "name": str(doc.get("title") or doc.get("name") or ""),
        "originalFilename": str(doc.get("original_filename") or doc.get("filename") or ""),
        "fileExt": _file_ext(doc),
        "mimeType": str(doc.get("mime_type") or "application/octet-stream"),
        "previewMimeType": str(doc.get("preview_mime_type") or doc.get("mime_type") or "text/plain"),
        "previewStatus": str(doc.get("preview_status") or "ready"),
        "chunkCount": int(doc.get("chunk_count") or 0),
        "canDownload": True,
    }


# ---------------------------------------------------------------------------
# 知识源：预览（二进制流）
# ---------------------------------------------------------------------------


@router.get("/sources/documents/{document_id}/preview")
async def get_document_preview(
    document_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> Any:
    """返回文档预览的**二进制流**（前端用 ``resp.blob()`` 消费）。

    数据来源优先级：
    1. ``storage_path`` 指向的本地卷文件（真实上传的原件）；
    2. 退化为 ``raw_text`` 文本流（索引入库时的正文）。

    两者都缺失时返回 404（无可预览内容），不返回空 200 骗前端。
    """
    from fastapi.responses import StreamingResponse

    doc = await _load_document(ctx, document_id)

    storage_path = doc.get("storage_path")
    if storage_path:
        from app.settings import get_settings

        root = get_settings().storage_root
        path = root / str(storage_path)
        # 目录穿越防护：解析后必须仍在 storage_root 内
        try:
            resolved = path.resolve()
            root_resolved = root.resolve()
            resolved.relative_to(root_resolved)
        except (ValueError, OSError):
            raise Forbidden("非法的存储路径")
        if resolved.is_file():
            return StreamingResponse(
                _file_iterator(resolved),
                media_type=str(doc.get("mime_type") or "application/octet-stream"),
            )

    raw_text = doc.get("raw_text")
    if isinstance(raw_text, str) and raw_text:
        # 无原件时以正文文本流兜底（前端 blob 仍可正常消费）
        return StreamingResponse(
            iter([raw_text.encode("utf-8")]),
            media_type=str(doc.get("preview_mime_type") or "text/plain"),
        )

    raise NotFound("该文档暂无可预览内容")


# ---------------------------------------------------------------------------
# 知识源：chunk 详情
# ---------------------------------------------------------------------------


@router.get("/sources/documents/{document_id}/chunks/{chunk_id}")
async def get_chunk(
    document_id: str,
    chunk_id: str,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """返回单个 chunk 详情（对齐前端 ``KnowledgeSourceChunk``）。"""
    from app.core import mongo

    await _load_document(ctx, document_id)  # 父文档不存在/无权 → 404/403

    doc = await mongo.get_collection(_CHUNKS).find_one(
        {
            "org_unit_id": ctx.org_unit_id,
            "document_id": str(document_id),
            "_id": _oid(chunk_id),
        }
    )
    if doc is None:
        raise NotFound("chunk 不存在")

    return {
        "id": str(doc.get("_id", chunk_id)),
        "documentId": str(doc.get("document_id", document_id)),
        "chunkId": str(doc.get("_id", chunk_id)),
        "chunkStage": str(doc.get("chunk_stage") or "indexed"),
        "ordinal": int(doc.get("seq") or 0),
        "text": str(doc.get("text") or ""),
        "contextualText": str(doc.get("contextual_text") or doc.get("text") or ""),
        "titlePath": list(doc.get("heading_path") or []),
        "pageNo": doc.get("page_from"),
        "contentType": str(doc.get("content_type") or "text"),
        "sourceChunkIds": list(doc.get("source_chunk_ids") or []),
        "metadata": dict(doc.get("metadata") or {}),
    }


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------


@router.post("/search")
async def search_knowledge(
    body: SearchRequest,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """知识检索（走冻结签名 ``retriever.search``；S-33 ACL 后过滤依赖 user_context）。

    因 ``search`` 是**同步阻塞** pymongo 调用，用 ``run_in_threadpool`` 放线程池，
    避免堵住事件循环。
    """
    from app.knowledge.models import SearchFilters
    from app.knowledge.retriever import search as retriever_search

    if not body.query.strip():
        return {"items": [], "total": 0}

    filters = SearchFilters(
        org_unit_id=ctx.org_unit_id,
        user_context=ctx,  # ★ S-33：必须传，ACL 后过滤依赖它（fail-closed）
        source_types=body.source_types,
        doc_ids=body.doc_ids,
    )

    hits = await run_in_threadpool(
        retriever_search, body.query, filters=filters, top_k=body.top_k
    )

    items = [
        {
            "chunk_id": h.chunk_id,
            "document_id": h.document_id,
            "text": h.text,
            "heading_path": list(h.heading_path),
            "score": float(h.score),
            "highlights": [
                {"start": hl.start, "end": hl.end, "term": hl.term} for hl in h.highlights
            ],
        }
        for h in hits
    ]
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


async def _load_document(ctx: RequestContext, document_id: str) -> dict[str, Any]:
    """加载知识源文档（org 隔离；不存在 → 404）。

    字段名兼容：索引侧（``knowledge/indexer.py``）写入的是 ``_id``(str) / ``title`` /
    ``raw_text`` / ``chunk_count``；上传侧可能写 ``original_filename`` / ``mime_type`` /
    ``storage_path``。此处对 ``_id`` 同时尝试 str 与 ObjectId 两种形态。
    """
    from app.core import mongo

    coll = mongo.get_collection(_DOCS)
    doc = await coll.find_one(
        {"org_unit_id": ctx.org_unit_id, "_id": _oid(document_id)}
    )
    if doc is None:
        doc = await coll.find_one(
            {"org_unit_id": ctx.org_unit_id, "_id": str(document_id)}
        )
    if doc is None:
        raise NotFound("知识源文档不存在")
    return doc


def _file_ext(doc: dict[str, Any]) -> str:
    """从文件名/扩展名推导文件后缀（不含点）。"""
    import os

    name = str(doc.get("original_filename") or doc.get("filename") or "")
    if name:
        _, ext = os.path.splitext(name)
        if ext:
            return ext.lstrip(".").lower()
    mime = str(doc.get("mime_type") or "")
    return mime.split("/")[-1] if "/" in mime else ""


def _file_iterator(path: Any):
    """分块读取本地文件（避免大文件一次性进内存）。"""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(64 * 1024)
            if not chunk:
                break
            yield chunk


def _oid(value: str) -> Any:
    """字符串 → ObjectId（失败则原样返回，支持 str 主键）。"""
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return value
