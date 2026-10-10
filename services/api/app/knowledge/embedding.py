"""嵌入与重排网关客户端（``knowledge/embedding.py``，阶段 2 向量检索 C 方案）。

经模型网关调用（复用 ``model_provider_base_url`` + ``model_provider_api_key``，
key 只进 .env，不进库/日志）：

- 嵌入：``POST {base}/embeddings``（模型 ``emb``，1024 维）
- 重排：``POST {base}/rerank``（**路径为 /rerank**；/reranker 网关不支持，error_code 336100）

**S-34 边界**：不新增任何基础设施服务——嵌入向量随 chunk 落 Mongo
（``knowledge_chunks.embedding``），余弦相似度在检索进程内计算，不引入向量库。

所有调用均为**尽力而为**（best-effort）：网关不可用 / 超时 / 未配置时返回 None，
调用方回退纯 BM25 路径，不得阻断索引或检索。
"""

from __future__ import annotations

import logging
import math

from app.settings import get_settings

logger = logging.getLogger(__name__)

_EMBED_TIMEOUT_SECONDS = 15.0
_RERANK_TIMEOUT_SECONDS = 10.0

#: 单次嵌入批大小（chunk 数上限，超出由调用方分批）
EMBED_BATCH_SIZE = 8


def _gateway() -> tuple[str, str] | None:
    """返回 (base_url, api_key)；未配置返回 None。"""
    s = get_settings()
    base = (s.model_provider_base_url or "").rstrip("/")
    key = s.model_provider_api_key or ""
    if not base or not key:
        return None
    return base, key


def embed_texts_sync(texts: list[str]) -> list[list[float]] | None:
    """批量嵌入（同步阻塞）。失败返回 None（调用方回退 BM25）。

    :param texts: 文本列表（单批 ≤ ``EMBED_BATCH_SIZE`` 条，超出请分批调用）。
    :return: 与输入等长的向量列表（1024 维）；未配置 / 失败返回 None。
    """
    gw = _gateway()
    model = get_settings().model_embedding_model
    if gw is None or not model or not texts:
        return None
    base, key = gw
    try:
        import httpx

        resp = httpx.post(
            f"{base}/embeddings",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "input": texts},
            timeout=_EMBED_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        ordered = [item.get("embedding") for item in sorted(data, key=lambda x: x.get("index", 0))]
        if len(ordered) != len(texts) or any(not e for e in ordered):
            logger.warning("embedding 返回数量不匹配: %s/%s", len(ordered), len(texts))
            return None
        return ordered
    except Exception as exc:  # noqa: BLE001 —— 尽力而为，失败回退 BM25
        logger.warning("嵌入调用失败（回退 BM25）: %s", exc)
        return None


def rerank_sync(query: str, documents: list[str]) -> list[tuple[int, float]] | None:
    """重排（同步阻塞）。返回 ``[(候选下标, 相关度分)]`` 按分数降序；失败返回 None。

    :param query: 用户查询。
    :param documents: 候选文本列表（下标即返回值里的 index）。
    """
    gw = _gateway()
    model = get_settings().model_reranker_model
    if gw is None or not model or not documents:
        return None
    base, key = gw
    try:
        import httpx

        resp = httpx.post(
            f"{base}/rerank",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "query": query, "documents": documents},
            timeout=_RERANK_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        out = [
            (int(r.get("index")), float(r.get("relevance_score") or r.get("score") or 0.0))
            for r in results
        ]
        out.sort(key=lambda x: x[1], reverse=True)
        return out
    except Exception as exc:  # noqa: BLE001 —— 尽力而为，失败回退融合序
        logger.warning("重排调用失败（回退融合序）: %s", exc)
        return None


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯 Python；1024 维 × 千级 chunk 规模足够，避免引入 numpy）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)
