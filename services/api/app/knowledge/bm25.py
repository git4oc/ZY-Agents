"""BM25 打分核心（``knowledge/bm25.py``，``00`` §6.4）。

纯函数、无 Mongo 依赖——``score_token`` / ``rank_chunks`` 可直接离线验证（S-32/S-33 的
CI 测试依赖它）。**不引入 ES / 向量库**（S-34）。

打分公式（经典 BM25）：

    score(q, d) = Σ_t idf(t) * f(t,d) * (k1+1) / (f(t,d) + k1*(1 - b + b*|d|/avgdl))

    idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

参数：
    k1 词频饱和（默认 1.5）
    b  长度归一（默认 0.75）
    avgdl 该 org_unit 的平均 chunk 长度（由 ``knowledge_terms``/统计给出）
"""

from __future__ import annotations

import math

_K1 = 1.5
_B = 0.75

#: 标题命中加权（``00`` §6.4：title_terms 命中的加权提高）
TITLE_TF_BONUS = 1.5


def idf(df: int, n_total: int) -> float:
    """BM25 逆文档频率（``df`` 来自 knowledge_terms，``n_total`` 为该 org_unit chunk 总数）。

    :param df: 词元出现的文档数。
    :param n_total: 语料库 chunk 总数。
    :return: idf 分数（≥ 0）。
    """
    if n_total <= 0:
        return 0.0
    return math.log(1 + (n_total - df + 0.5) / (df + 0.5))


def score_token(
    tf: float,
    df: int,
    n_total: int,
    field_len: int,
    avgdl: float,
    *,
    k1: float = _K1,
    b: float = _B,
    is_title: bool = False,
) -> float:
    """单词元对单 chunk 的 BM25 分量。

    :param tf: 词元在该 chunk 的出现频次（标题命中可乘 ``TITLE_TF_BONUS`` 后传入）。
    :param df: 词元的文档频率（knowledge_terms.df）。
    :param n_total: chunk 总数。
    :param field_len: 该 chunk 的长度（词元数，用于长度归一）。
    :param avgdl: 语料平均 chunk 长度。
    :param is_title: 该命中是否来自标题（加权提高）。
    :return: 该词的贡献分量。
    """
    if tf <= 0 or df <= 0 or field_len <= 0:
        return 0.0
    tf_eff = tf * (TITLE_TF_BONUS if is_title else 1.0)
    idf_v = idf(df, n_total)
    denom = tf_eff + k1 * (1 - b + b * (field_len / max(1e-9, avgdl)))
    return idf_v * (tf_eff * (k1 + 1)) / denom


def rank_chunks(
    query_terms: list[str],
    candidates: list[dict],
    *,
    df_table: dict[str, int],
    avgdl: float,
    n_total: int,
    k1: float = _K1,
    b: float = _B,
) -> list[dict]:
    """对召回候选 chunk 计算 BM25 分数并降序排序。

    :param query_terms: 查询分词后的词元列表。
    :param candidates: 每个候选的 ``{chunk_id, document_id, text, heading_path, terms[], title_terms[]}``。
    :param df_table: term -> df 的映射（来自 knowledge_terms）。
    :param avgdl: 语料平均 chunk 长度。
    :param n_total: chunk 总数。
    :return: 带 ``score`` 注解的候选（原位添加，按分数降序）。**不含 ACL 过滤**（S-33 留到 retriever）。
    """
    from collections import Counter

    q_counts = Counter(query_terms)
    for cand in candidates:
        len_c = max(1, len(cand.get("terms") or []))
        total = 0.0
        for term, qtf in q_counts.items():
            df = df_table.get(term) or 0
            # 正文 tf
            tf_body = (cand.get("terms") or []).count(term)
            is_title = term in (cand.get("title_terms") or [])
            if tf_body > 0 or is_title:
                tf = tf_body
                if tf == 0 and is_title:
                    tf = 1.0
                total += score_token(
                    tf * qtf, df, n_total, len_c, avgdl, k1=k1, b=b, is_title=is_title
                )
        cand["score"] = total
    return sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)
