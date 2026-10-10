"""中文分词（``knowledge/tokenizer.py``，``01`` §4.6 / ``00`` §6）。

- ``tokenize(text)``：jieba 主路径，过滤停用词，返回 term 列表。
- ``bigrams(text)``：短查询回退（2 字内的字词组合），用于 B25 短查询兜底。

**关键约束**：``00`` §6.2 明确——中文检索**不用 MongoDB $text**（S-31），
分词在代码层完成、倒排索引自建。故 tokenizer 是检索链路的第一环且**纯函数**，
不依赖 Mongo，可离线验证。
"""

from __future__ import annotations

import re

#: 默认停用词（中文检索高频无信息量词）
_STOPWORDS = frozenset(
    {
        "的", "了", "和", "与", "或", "在", "是", "有", "为", "对", "把", "被",
        "吗", "呢", "啊", "呀", "吧", "喔", "哦", "嗯", "哈", "啊",
        "一个", "一下", "这个", "那个", "这些", "那些", "我们", "你们", "他们",
        "to", "the", "and", "of", "for", "in", "on", "is", "are",
    }
)

#: 仅保留中英文/数字的词（去标点、去纯符号）
_WORD_RE = re.compile(r"^[\w\u4e00-\u9fff]+$")


def tokenize(text: str) -> list[str]:
    """jieba 主路径：分词 → 去停用词 → 去纯标点。返回有序 term 列表（可含重复）。

    :param text: 原始文本（标题 / 正文 / 查询）。
    :return: 过滤后的词元列表。
    """
    import jieba

    # 触发一次初始化缓存（幂等；词典已 cache 则秒回）
    jieba.initialize()

    tokens: list[str] = []
    for token in jieba.cut(_normalize(text)):
        token = token.strip().lower()
        if not token:
            continue
        if token in _STOPWORDS:
            continue
        if not _WORD_RE.match(token):
            continue
        tokens.append(token)
    return tokens


def bigrams(text: str) -> list[str]:
    """短查询回退：对长度不足做 2-gram 字组合召回（``00`` §6.2 / 短查询兜底）。

    用于查询词过少（<2 个有效 term）时扩大召回，避免漏召回。
    返回去重后的 2-gram 相邻字对（如 ``"知识库"`` → ``["知识", "识库"]``）。

    :param text: 原始查询。
    :return: 去重后的相邻 2-gram 列表。
    """
    chars = [c for c in _normalize(text) if _WORD_RE.match(c)]
    if len(chars) < 2:
        return list(dict.fromkeys(chars))
    grams: list[str] = []
    for i in range(len(chars) - 1):
        gram = chars[i] + chars[i + 1]
        if gram not in _STOPWORDS:
            grams.append(gram)
    return list(dict.fromkeys(grams))


def _normalize(text: str) -> str:
    """规范化：统一空格 / 换行。"""
    return " ".join(text.split())
