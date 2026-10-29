"""中英文混合分词器。

对照 WeKnora: Go 侧用 gojieba 做中文分词(见检索引擎文档),这里用
「英文/数字按词 + 中文按 bigram」的轻量方案,避免引入 jieba 依赖,
对演示语料足够。
"""
import re

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def tokenize(text: str) -> list[str]:
    """把文本切成 token 列表。英文数字按词,中文逐字 + 相邻字 bigram。"""
    tokens: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        tokens.append(word)
    han = [c for c in text if "一" <= c <= "鿿"]
    tokens.extend(han)  # 单字,保证召回
    tokens.extend(a + b for a, b in zip(han, han[1:]))  # bigram,保证精度
    return tokens
