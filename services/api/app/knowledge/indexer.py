"""文档索引（``knowledge/indexer.py``，``00`` §6.2 / ``01`` §4.6）。

把文档切块 → 分词 → 写 ``knowledge_chunks``（terms / title_terms / heading_path /
page 回指 / acl 冗余）→ 更新 ``knowledge_terms`` 的 df 统计。

**S-31**：中文检索**禁止**用 MongoDB ``$text``（本模块不创建任何 ``text`` 索引；
集合索引在 ``core/mongo.py`` 的 ``ensure_indexes`` 中定义，均非 text）。索引在代码层
自建倒排（terms 数组 + terms 索引），BM25 打分在 ``bm25.py``。

- ``index_document(doc_id)``：索引单个文档。
- ``rebuild_df(org_unit_id)``：重建该 org 的 term -> df 统计。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core import mongo
from app.core.clock import utcnow
from app.core.errors import StorageError
from app.knowledge.models import IndexResult
from app.knowledge.tokenizer import tokenize

_CHUNKS_COLLECTION = "knowledge_chunks"
_TERMS_COLLECTION = "knowledge_terms"
_DOC_COLLECTION = "knowledge_documents"

#: 默认切块字符数（中文字符约）
_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 80


async def index_document(doc_id: str) -> IndexResult:
    """索引单个文档：读文档 → 切块 → 分词 → 写 chunks → 更新 df。

    :param doc_id: knowledge_documents._id。
    :return: IndexResult（indexed / reindexed / failed）。
    """
    doc_col = mongo.get_collection(_DOC_COLLECTION)
    chunks_col = mongo.get_collection(_CHUNKS_COLLECTION)
    terms_col = mongo.get_collection(_TERMS_COLLECTION)

    doc = await doc_col.find_one({"_id": doc_id})
    if doc is None:
        return IndexResult(doc_id=str(doc_id), status="failed", error="document not found")

    org = str(doc.get("org_unit_id") or "")
    text = str(doc.get("raw_text") or "")
    title = str(doc.get("title") or "")
    acl = doc.get("acl") or {"visibility": "org"}

    # 1) 切块（带 overlap）
    chunks = _split_chunks(text)
    if len(chunks) == 0:
        chunks = [text]

    title_terms = tokenize(title)

    # 2) 幂等：先清该文档旧 chunks（重入库）
    await chunks_col.delete_many({"document_id": str(doc_id)})

    # 3) 写新 chunks
    seq = 0
    for i, ctext in enumerate(chunks):
        body_terms = tokenize(ctext)
        seq = i
        await chunks_col.insert_one(
            {
                "org_unit_id": org,
                "document_id": str(doc_id),
                "seq": i,
                "text": ctext,
                "terms": body_terms,
                "title_terms": title_terms,
                "heading_path": list(doc.get("heading_path") or []),
                "page_from": doc.get("page_from"),
                "page_to": doc.get("page_to"),
                "acl": acl,
                "created_at": utcnow(),
            }
        )

    # 4) 更新文档状态
    await doc_col.update_one(
        {"_id": str(doc_id)},
        {
            "$set": {
                "status": "ready",
                "chunk_count": len(chunks),
                "char_len": len(text),
                "indexed_at": utcnow(),
            }
        },
    )

    # 5) 更新该 org 的 df 统计（仅当文档级 terms 变化；简化：全量重算该 org 的 df）
    await rebuild_df(org)

    # 6) 向量嵌入（阶段 2 C 方案；尽力而为：网关不可用/未配置时跳过，回退纯 BM25）
    await _embed_chunks(org, str(doc_id), chunks)

    return IndexResult(doc_id=str(doc_id), status="indexed", chunk_count=len(chunks))


async def _embed_chunks(org: str, doc_id: str, chunks: list[str]) -> None:
    """为该文档的 chunks 批量嵌入并回写 ``embedding`` 字段（尽力而为，S-34 不新增基础设施）。

    嵌入失败**不阻断索引**——BM25 路径不受影响，缺 embedding 的 chunk 自然不参与
    向量召回。网关调用放线程池（同步 httpx），避免堵事件循环。
    """
    from app.knowledge.embedding import EMBED_BATCH_SIZE, embed_texts_sync
    from app.settings import get_settings

    if not chunks:
        return
    model = get_settings().model_embedding_model
    if not model:
        return

    chunks_col = mongo.get_collection(_CHUNKS_COLLECTION)
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        vectors = await asyncio.to_thread(embed_texts_sync, batch)
        if not vectors:
            return
        for offset, vec in enumerate(vectors):
            if not vec:
                continue
            await chunks_col.update_one(
                {"org_unit_id": org, "document_id": doc_id, "seq": start + offset},
                {"$set": {"embedding": vec, "embedding_model": model}},
            )


async def rebuild_df(org_unit_id: str) -> None:
    """重建某 org_unit 的 term -> 文档频率（knowledge_terms.df）。

    df = 含该 term 的 chunk 文档数（按 document 去重）。从 knowledge_chunks 聚合，
    写回 knowledge_terms 集合（S-31 不依赖 $text，纯倒排统计）。
    """
    chunks_col = mongo.get_collection(_CHUNKS_COLLECTION)
    terms_col = mongo.get_collection(_TERMS_COLLECTION)

    # 统计每个 term 出现在多少个文档（按 doc 去重）
    pipeline: list[dict[str, Any]] = [
        {"$match": {"org_unit_id": org_unit_id}},
        {"$unwind": "$terms"},
        {
            "$group": {
                "_id": {"term": "$terms", "document_id": "$document_id"},
            }
        },
        {
            "$group": {
                "_id": "$_id.term",
                "df": {"$sum": 1},
            }
        },
    ]
    rows = await chunks_col.aggregate(pipeline).to_list(length=None)

    # 先清空该 org 旧统计，再写入
    await terms_col.delete_many({"org_unit_id": org_unit_id})
    if rows:
        await terms_col.insert_many(
            [
                {"org_unit_id": org_unit_id, "term": str(r["_id"]), "df": int(r["df"])}
                for r in rows
                if str(r["_id"])
            ]
        )


def _split_chunks(text: str) -> list[str]:
    """按固定长度切块，带相邻 overlap（保证跨块语义不丢）。"""
    if not text:
        return []
    if len(text) <= _CHUNK_SIZE:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + _CHUNK_SIZE, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - _CHUNK_OVERLAP
    return chunks
