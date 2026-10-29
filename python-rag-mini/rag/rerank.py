"""重排(Rerank)。

对照 WeKnora: internal/reranking/ 是远程 rerank 模型客户端(如 gte-rerank),
上层再做复合打分(base 分 + rerank 分)。精简版提供两级:

- NoopReranker: 默认,直接用融合分,不重排(离线可跑)
- LLMReranker: 有 OPENAI_API_KEY 时,让 LLM 对每个候选打 0-10 相关度分,
  composite = 0.7*rerank + 0.3*base,对照 WeKnora 复合打分思路
"""
from __future__ import annotations

import os
import re

from .hybrid import ScoredChunk


class NoopReranker:
    def rerank(self, query: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]:
        return candidates


class LLMReranker:
    def __init__(self) -> None:
        from langchain_openai import ChatOpenAI

        self.llm = ChatOpenAI(
            model=os.environ.get("CHAT_MODEL", "gpt-4o-mini"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            temperature=0,
        )

    def rerank(self, query: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]:
        for c in candidates:
            prompt = (
                "评估下面这段内容与问题的相关程度,只输出 0-10 的一个数字。\n\n"
                f"问题:{query}\n\n内容:{c.chunk.content[:600]}"
            )
            try:
                resp = self.llm.invoke(prompt).content
                m = re.search(r"\d+(?:\.\d+)?", str(resp))
                rel = min(max(float(m.group()) / 10.0, 0.0), 1.0) if m else 0.0
            except Exception:
                rel = 0.0
            c.score = 0.7 * rel + 0.3 * c.score
        candidates.sort(key=lambda s: s.score, reverse=True)
        return candidates


def get_reranker():
    if os.environ.get("OPENAI_API_KEY"):
        return LLMReranker()
    return NoopReranker()
