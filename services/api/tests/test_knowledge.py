"""知识库模块测试（``tests/test_knowledge.py``，``00`` §6.4 / ``S-31``~``S-34``）。

纯函数部分（不依赖 Mongo）离线可验证：
- tokenizer：jieba 分词 + 去停用词 + bigram 回退。
- bm25：idf / score_token / rank_chunks 打分逻辑。
- retriever：S-32 签名冻结 + S-33 ACL 过滤时序（不直接触 Mongo 的部分）。

Mongo 依赖的端到端（indexer/retriever 读写集合）留待真实 Mongo 就绪后验证。
"""

from __future__ import annotations

import sys
import unittest

import tests._zytest_stubs  # noqa: F401

from app.knowledge.bm25 import idf, rank_chunks, score_token
from app.knowledge.models import SearchFilters, Highlight, Hit, TimeRange
from app.knowledge.retriever import _apply_acl, _build_hits, _dedup, search
from app.knowledge.tokenizer import bigrams, tokenize


class TokenizerTest(unittest.TestCase):
    def test_tokenize_filters_stopwords(self):
        t = tokenize("智能体平台的核心是上下文工程")
        self.assertIn("上下文", t)
        self.assertIn("工程", t)
        self.assertNotIn("的", t)  # 停用词被过滤
        self.assertNotIn("是", t)

    def test_tokenize_lowercases_english(self):
        t = tokenize("Payment Gateway 设计")
        joined = " ".join(t)
        self.assertIn("payment", joined)

    def test_bigrams(self):
        """短查询回退：'知识库' → ['知识','识库']。"""
        b = bigrams("知识库")
        self.assertIn("知识", b)
        self.assertIn("识库", b)

    def test_bigrams_short_single_char(self):
        b = bigrams("库")
        self.assertEqual(b, ["库"])


class Bm25Test(unittest.TestCase):
    def test_idf_rare_term_higher(self):
        self.assertGreater(idf(1, 100), idf(50, 100))

    def test_score_token_zero_when_tf_zero(self):
        self.assertEqual(score_token(0, 10, 100, 5, 5), 0.0)

    def test_title_bonus_increases_score(self):
        base = score_token(2, 10, 100, 5, 5)
        titled = score_token(2, 10, 100, 5, 5, is_title=True)
        self.assertGreater(titled, base)

    def test_rank_chunks_relevant_first(self):
        df = {"企业": 10, "协同": 20}
        cands = [
            {"chunk_id": "c1", "document_id": "d1", "text": "企业协同平台",
             "terms": ["企业", "协同", "平台"], "title_terms": ["企业"]},
            {"chunk_id": "c2", "document_id": "d2", "text": "市场竞争分析",
             "terms": ["市场", "竞争"], "title_terms": []},
        ]
        ranked = rank_chunks(["企业"], cands, df_table=df, avgdl=5.0, n_total=100)
        self.assertEqual(ranked[0]["chunk_id"], "c1")
        self.assertGreater(ranked[0]["score"], 0)


class RetrieverAclTest(unittest.TestCase):
    def test_search_signature_is_fixed(self):
        """S-32：search(query, *, filters, top_k) -> list[Hit]（签名冻结）。"""
        import inspect

        sig = inspect.signature(search)
        params = list(sig.parameters)
        self.assertIn("query", params)
        self.assertIn("filters", params)
        self.assertIn("top_k", params)
        # filters 与 top_k 是 keyword-only（* 之后）
        self.assertTrue(sig.parameters["filters"].kind is inspect.Parameter.KEYWORD_ONLY)
        self.assertTrue(sig.parameters["top_k"].kind is inspect.Parameter.KEYWORD_ONLY)
        # 返回注解指向 Hit 列表
        annot = str(sig.return_annotation)
        self.assertIn("Hit", annot)

    def test_restricted_chunk_filtered_after_score(self):
        """S-33：ACL 在打分后过滤——越权者看不到 restricted chunk，但有权限者可见。
        分数保留不过滤时的原值（后过滤不改变打分）。"""
        scored = [
            {"chunk_id": "c1", "document_id": "d1", "score": 3.0,
             "acl": {"visibility": "org"}},
            {"chunk_id": "c2", "document_id": "d2", "score": 5.0,
             "acl": {"visibility": "restricted", "allowed_user_ids": ["u-a"]}},
        ]
        # 无权限用户：看不到 restricted c2
        class Ctx:  # 极简 RequestContext 兼容对象
            user_id = "u-b"
            roles = frozenset()

        visible = _apply_acl(scored, SearchFilters(org_unit_id="ou1", user_context=Ctx()))
        self.assertEqual([c["chunk_id"] for c in visible], ["c1"])
        self.assertEqual(visible[0]["score"], 3.0)  # 分数原值不变（后过滤）

        # 有权限用户：能看到 c2，且分数仍是 5.0（不过滤时的原值）
        class CtxA:
            user_id = "u-a"
            roles = frozenset()

        visible2 = _apply_acl(scored, SearchFilters(org_unit_id="ou1", user_context=CtxA()))
        # _apply_acl 保持打分后的原顺序（c1 在前），只过滤不掉越权；两可见
        self.assertEqual([c["chunk_id"] for c in visible2], ["c1", "c2"])
        # 且分数保持不过滤时的原值（后过滤不改变打分，S-33）
        self.assertEqual(visible2[0]["score"], 3.0)
        self.assertEqual(visible2[1]["score"], 5.0)

    def test_dedup_keeps_highest_chunk_per_doc(self):
        scored = [
            {"chunk_id": "c1", "document_id": "d1", "score": 2.0},
            {"chunk_id": "c2", "document_id": "d1", "score": 5.0},
            {"chunk_id": "c3", "document_id": "d2", "score": 4.0},
        ]
        deduped = _dedup(scored)
        self.assertEqual([c["chunk_id"] for c in deduped], ["c2", "c3"])

    def test_build_hits_shape(self):
        scored = [
            {"chunk_id": "c1", "document_id": "d1", "text": "企业协同平台",
             "heading_path": ["知识库"], "score": 3.0,
             "title_terms": ["企业"], "terms": ["协同", "平台"]},
        ]
        hits = _build_hits(scored)
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertIsInstance(h, Hit)
        self.assertEqual(h.chunk_id, "c1")
        self.assertEqual(h.document_id, "d1")
        self.assertEqual(h.score, 3.0)
        self.assertIsInstance(h.highlights, list)
        self.assertEqual(h.heading_path, ["知识库"])

    def test_filters_dataclass(self):
        f = SearchFilters(org_unit_id="ou1", source_types=["pdf"],
                          doc_ids=["d1"], time_range=TimeRange())
        self.assertEqual(f.org_unit_id, "ou1")
        self.assertEqual(f.source_types, ["pdf"])
        self.assertEqual(f.doc_ids, ["d1"])
        self.assertIsNotNone(f.time_range)


if __name__ == "__main__":
    unittest.main()
