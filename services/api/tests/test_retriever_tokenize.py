"""检索分词测试（``knowledge/tokenizer.py``，纯函数无需 Mongo/网络）。

守护的是 S-32 冻结签名下的召回面：分词结果直接决定 BM25 命中，
中文短查询回退 bigram 扩大召回。
"""

from __future__ import annotations

import unittest

from app.knowledge.tokenizer import bigrams, tokenize


class TestTokenize(unittest.TestCase):
    def test_english_words(self) -> None:
        self.assertEqual(tokenize("hello world"), ["hello", "world"])

    def test_lowercased(self) -> None:
        self.assertEqual(tokenize("Hello World"), ["hello", "world"])

    def test_mixed_cjk_english(self) -> None:
        tokens = tokenize("知识库 retrieval")
        self.assertIn("retrieval", tokens)
        self.assertTrue(any(t for t in tokens), tokens)

    def test_empty_query(self) -> None:
        self.assertEqual(tokenize("   "), [])

    def test_bigrams_for_cjk(self) -> None:
        grams = bigrams("向量检索")
        self.assertTrue(len(grams) >= 2)
        self.assertIn("向量", grams)


if __name__ == "__main__":
    unittest.main()
