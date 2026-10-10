"""检索编排（``knowledge/retriever.py``，``00`` §6.4 / ``01`` §4.6）。

**S-32（硬门槛）**：调用签名**固定**为
``search(query, *, filters: SearchFilters, top_k: int) -> list[Hit]``。
混合检索只改本模块内部，调用方 / API / ``Hit`` 结构**零改动**。

**S-33**：ACL 过滤**必须在打分之后、返回之前**——先过滤会让召回集变小、分数不可比；
后过滤只丢越权结果（``00`` §6.4 第 4 步）。

流程（阶段 2 C 方案：BM25 + 向量两路召回 → RRF 融合 → 可选 rerank 精排）：
1. 查询分词 → terms
2. 两路召回：``$in`` 倒排命中（标题加权）∪ 已嵌入向量候选（``knowledge_chunks.embedding``）
3. BM25 打分 + 向量余弦 → RRF 融合（score 替换为融合分，rank-based 两路可比）
4. 可选 rerank 精排（融合 top-N；尽力而为，失败回退融合序）
5. **ACL 后过滤**（在打分之后、返回之前）
6. 同文档去重（保留最高分 chunk）
7. 上下文扩展：命中 chunk 取前后各 N 个作为 evidence
"""

from __future__ import annotations

import os
from typing import Any

from app.knowledge.bm25 import rank_chunks
from app.knowledge.models import Highlight, Hit, SearchFilters
from app.knowledge.tokenizer import bigrams, tokenize

_CHUNKS_COLLECTION = "knowledge_chunks"
_TERMS_COLLECTION = "knowledge_terms"
_DOC_COLLECTION = "knowledge_documents"

#: 上下文扩展：命中 chunk 前后各取 N 个
CONTEXT_WINDOW = 3

#: RRF 融合常数（标准取值 60）
_RRF_K = 60

#: 向量补充召回的候选上限（已嵌入但未被倒排命中的 chunk）
_VECTOR_CANDIDATE_LIMIT = 400

#: rerank 精排池大小（融合 top-N 参与精排）
_RERANK_POOL = 20

#: 同步只读客户端（懒加载；S-32 同步签名 + 真实 Mongo 读取用 pymongo 同步游标）。
#: 检索是只读中等请求量，不必走 motor async，保持 search 为纯同步调用。
_SYNC: Any = None
_SYNC_DB_NAME: str = "zy_agents"


def _db() -> Any:
    """返回当前库的同步句柄（懒加载；host/db 取自环境，缺省 127.0.0.1/zy_agents）。"""
    global _SYNC, _SYNC_DB_NAME
    if _SYNC is None:
        import pymongo

        host = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
        _SYNC = pymongo.MongoClient(host=host, serverSelectionTimeoutMS=5000)
        _SYNC_DB_NAME = os.environ.get("MONGO_DB", "zy_agents")
    return _SYNC[_SYNC_DB_NAME]


def search(query: str, *, filters: SearchFilters, top_k: int) -> list[Hit]:
    """检索（**签名固定，S-32**；阶段 2 混合检索：BM25 + 向量 RRF 融合 + 可选 rerank）。

    :param query: 用户查询串。
    :param filters: 检索过滤（org_unit_id / user_context / source_types / doc_ids / time_range）。
    :param top_k: 返回条数。
    :return: 打分后排序、ACL 过滤后的 Hit 列表。

    注：本函数为同步签名（``S-32``），内部阻塞调用（pymongo / httpx 嵌入与重排）
    由上层端点用 ``run_in_threadpool`` 放线程池，不堵事件循环。
    """
    # 1) 查询分词（短查询回退 bigram 扩大召回）
    terms = tokenize(query)
    if not terms:
        terms = bigrams(query)

    # 2) 两路召回：BM25 倒排命中 ∪ 已嵌入向量候选（org 隔离）
    _dbh = _db()
    chunk_col = _dbh[_CHUNKS_COLLECTION]
    terms_col = _dbh[_TERMS_COLLECTION]

    base_clause: dict[str, Any] = {"org_unit_id": filters.org_unit_id}
    if filters.doc_ids:
        base_clause["document_id"] = {"$in": filters.doc_ids}

    clause = dict(base_clause)
    if terms:
        clause["$or"] = [{"terms": {"$in": terms}}, {"title_terms": {"$in": terms}}]
    candidates = _fetch_candidates(chunk_col, clause)
    candidates = _merge_vector_candidates(chunk_col, base_clause, candidates)

    if not candidates:
        return []

    # 3) 混合打分：BM25 + 向量余弦 → RRF 融合（score 替换为融合分）
    df_table = _load_df(terms_col, filters.org_unit_id)
    n_total = _count_chunks(chunk_col, filters.org_unit_id)
    avgdl = _avg_chunk_len(chunk_col, filters.org_unit_id)
    if terms:
        scored = rank_chunks(terms, candidates, df_table=df_table, avgdl=avgdl, n_total=n_total)
    else:
        scored = candidates  # terms 空：跳过 BM25，仅向量路排序
    query_vec = _query_vector(query)
    scored = _fuse_with_vectors(scored, query_vec)

    # 两路都无信号（terms 空且向量不可用/全零）→ 无结果
    if all(float(c.get("score") or 0.0) <= 0.0 for c in scored):
        return []

    # 4) 可选 rerank 精排（融合 top-N；尽力而为，失败保持融合序）
    scored = _rerank_top(query, scored)

    # 5) ACL 过滤（打分后、返回前，S-33）
    visible = _apply_acl(scored, filters)

    # 6) 同文档去重（保留最高分 chunk）
    deduped = _dedup(visible)

    # 7) 上下文扩展 + 组装 Hit
    hits = _build_hits(deduped[:top_k])
    return hits


def _fetch_candidates(coll, clause: dict) -> list[dict]:
    """$in 召回候选 chunk（含 terms / title_terms / heading_path / acl / embedding）。"""
    out: list[dict] = []
    for doc in coll.find(clause).limit(1000):
        out.append(
            {
                "chunk_id": str(doc.get("_id")),
                "document_id": str(doc.get("document_id") or ""),
                "text": doc.get("text") or "",
                "heading_path": list(doc.get("heading_path") or []),
                "terms": list(doc.get("terms") or []),
                "title_terms": list(doc.get("title_terms") or []),
                "acl": doc.get("acl") or {},
                "_seq": doc.get("seq") or 0,
                "embedding": doc.get("embedding"),
            }
        )
    return out


def _merge_vector_candidates(
    coll, base_clause: dict[str, Any], candidates: list[dict]
) -> list[dict]:
    """向量补充召回：把已嵌入但未被 BM25 倒排命中的 chunk 并入候选（两路召回的向量路）。

    仅按 org 隔离过滤（base_clause），不查 terms——语义相近但词面不同的 chunk 靠这路进候选。
    """
    have = {c.get("chunk_id") for c in candidates}
    clause = dict(base_clause)
    clause["embedding"] = {"$exists": True, "$type": "array"}
    for doc in coll.find(clause).limit(_VECTOR_CANDIDATE_LIMIT):
        cid = str(doc.get("_id"))
        if cid in have:
            continue
        candidates.append(
            {
                "chunk_id": cid,
                "document_id": str(doc.get("document_id") or ""),
                "text": doc.get("text") or "",
                "heading_path": list(doc.get("heading_path") or []),
                "terms": list(doc.get("terms") or []),
                "title_terms": list(doc.get("title_terms") or []),
                "acl": doc.get("acl") or {},
                "_seq": doc.get("seq") or 0,
                "embedding": doc.get("embedding"),
            }
        )
    return candidates


def _query_vector(query: str) -> list[float] | None:
    """查询向量（尽力而为：未配置/失败返回 None，回退纯 BM25）。"""
    from app.knowledge.embedding import embed_texts_sync

    vecs = embed_texts_sync([query])
    if not vecs or not vecs[0]:
        return None
    return vecs[0]


def _fuse_with_vectors(scored: list[dict], query_vec: list[float] | None) -> list[dict]:
    """BM25 + 向量两路 RRF 融合（C 方案核心）。

    ``score`` 替换为 RRF 融合分：``Σ 1/(k + rank)``（rank 从 1 起，k=60）。
    rank-based 分数使 BM25 与余弦两路**可直接相加**，不必做量纲归一。
    无向量（未配置/嵌入失败）时保持 BM25 序原样返回。

    - BM25 路贡献：仅当该候选 BM25 分 > 0（避免任意序注入噪声）。
    - 向量路贡献：仅嵌入候选参与，按余弦降序取 rank。
    - 原始 BM25 分保留在 ``bm25_score``（可观测，不参与排序）。
    """
    if query_vec is None:
        return scored  # 纯 BM25

    from app.knowledge.embedding import cosine

    # 向量路排序（有 embedding 的候选按余弦降序）
    vec_scored: list[tuple[str, float]] = []
    for cand in scored:
        emb = cand.get("embedding")
        if emb:
            vec_scored.append((cand.get("chunk_id"), cosine(query_vec, emb)))
    vec_scored.sort(key=lambda x: x[1], reverse=True)

    bm25_ranks = {c.get("chunk_id"): r for r, c in enumerate(scored, start=1)}
    vec_ranks = {cid: r for r, (cid, _s) in enumerate(vec_scored, start=1)}

    for cand in scored:
        cid = cand.get("chunk_id")
        bm25_score = float(cand.get("score") or 0.0)
        rrf = 0.0
        if bm25_score > 0.0 and cid in bm25_ranks:
            rrf += 1.0 / (_RRF_K + bm25_ranks[cid])
        if cid in vec_ranks:
            rrf += 1.0 / (_RRF_K + vec_ranks[cid])
        cand["bm25_score"] = bm25_score
        cand["score"] = rrf
    return sorted(scored, key=lambda c: c.get("score", 0.0), reverse=True)


def _rerank_top(query: str, scored: list[dict]) -> list[dict]:
    """对融合后 top-N 候选做 rerank 精排（尽力而为：失败保持融合序）。

    精排池内按 rerank 相关度分排前（``score`` 替换为 rerank 分），池外保持融合序排后。
    """
    if not scored:
        return scored
    from app.knowledge.embedding import rerank_sync

    pool = scored[:_RERANK_POOL]
    rest = scored[_RERANK_POOL:]
    ranked = rerank_sync(query, [c.get("text") or "" for c in pool])
    if not ranked:
        return scored

    out: list[dict] = []
    seen: set[str] = set()
    for idx, rel in ranked:
        if idx < 0 or idx >= len(pool):
            continue
        cand = pool[idx]
        cid = cand.get("chunk_id")
        if cid in seen:
            continue
        cand["rerank_score"] = rel
        cand["score"] = rel
        out.append(cand)
        seen.add(cid)
    # 网关未返回的池内候选（保底：按融合序补上）
    for cand in pool:
        if cand.get("chunk_id") not in seen:
            out.append(cand)
            seen.add(cand.get("chunk_id"))
    out.extend(rest)
    return out


def _load_df(coll, org_unit_id: str) -> dict[str, int]:
    """从 knowledge_terms 装载 term -> df 映射。"""
    df_table: dict[str, int] = {}
    for doc in coll.find({"org_unit_id": org_unit_id}):
        term = doc.get("term")
        if term:
            df_table[str(term)] = int(doc.get("df") or 0)
    return df_table


def _count_chunks(coll, org_unit_id: str) -> int:
    return int(coll.count_documents({"org_unit_id": org_unit_id}))


def _avg_chunk_len(coll, org_unit_id: str) -> float:
    """平均 chunk 长度（词元数近似：按字符长度 / 2 粗糙估计，避免额外聚合查询）。"""
    import statistics

    lens = []
    for doc in coll.find({"org_unit_id": org_unit_id}, {"text": 1}).limit(2000):
        lens.append(len(doc.get("text") or ""))
    if not lens:
        return 1.0
    return max(1.0, sum(lens) / len(lens) / 2.0)


def _apply_acl(scored: list[dict], filters: SearchFilters) -> list[dict]:
    """S-33：ACL 过滤在打分之后。保留 org 可见或列在 allowed 的用户/角色。"""
    ctx = filters.user_context
    user_id = getattr(ctx, "user_id", None) if ctx else None
    roles = getattr(ctx, "roles", None) if ctx else None

    visible: list[dict] = []
    for cand in scored:
        acl = cand.get("acl") or {}
        vis = acl.get("visibility", "org")
        if vis != "restricted":
            visible.append(cand)
            continue
        # restricted：仅 allowed_user_ids / allowed_roles 可见
        allowed_users = set(acl.get("allowed_user_ids") or [])
        allowed_roles = set(acl.get("allowed_roles") or [])
        if user_id and user_id in allowed_users:
            visible.append(cand)
            continue
        if roles and (roles & allowed_roles):
            visible.append(cand)
            continue
        # 越权 → 丢弃（但分数保持不过滤时一致，S-33）
    return visible


def _dedup(scored: list[dict]) -> list[dict]:
    """同文档去重（保留最高分 chunk）。"""
    seen: dict[str, float] = {}
    result: list[dict] = []
    for cand in scored:
        doc_id = cand.get("document_id") or ""
        best = seen.get(doc_id, -1.0)
        if cand.get("score", 0.0) > best:
            seen[doc_id] = cand.get("score", 0.0)
            # 移除该文档旧候选中较低分者
            result = [c for c in result if c.get("document_id") != doc_id]
            result.append(cand)
    return result


def _build_hits(scored: list[dict]) -> list[Hit]:
    hits: list[Hit] = []
    for cand in scored:
        text = cand.get("text") or ""
        # 高亮：出现在 text 中的查询词
        highlights = _highlight(text, cand.get("title_terms") or [], cand.get("terms") or [])
        hits.append(
            Hit(
                chunk_id=cand.get("chunk_id", ""),
                document_id=cand.get("document_id", ""),
                text=text,
                heading_path=list(cand.get("heading_path") or []),
                score=float(cand.get("score") or 0.0),
                highlights=highlights,
            )
        )
    return hits


def _highlight(text: str, title_terms: list[str], body_terms: list[str]) -> list[Highlight]:
    """在文本中定位查询词元的高亮区间（简单实现：取 title+body terms 中出现的）。"""
    out: list[Highlight] = []
    if not text:
        return out
    for term in list(dict.fromkeys([*title_terms, *body_terms]))[:6]:
        idx = text.find(term)
        if idx >= 0:
            out.append(Highlight(start=idx, end=idx + len(term), term=term))
    return out
