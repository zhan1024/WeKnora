"""混合检索:向量 + BM25 + 加权 RRF 融合。

逐行对照 WeKnora Go 实现:
  internal/application/service/knowledgebase_search.go         HybridSearch 主流程
  internal/application/service/knowledgebase_search_fusion.go  fuseOrDeduplicate / fuseWithRRF
  internal/types/retrieval_config.go                           GetEffectiveRRFK / GetEffectiveRRFWeights

融合规则与 Go 版完全一致:
  - 仅向量: 保留 cosine 相似度,按 chunk 去重取最高分
  - 仅关键词: BM25 除以本列表最高分,归一到 [0,1]
  - 混合: 加权 RRF score = vW/(k+vRank) + kW/(k+kRank),再除以理论最大值
    (vW+kW)/(k+1) 归一到 [0,1]。rank 在每个列表内按分数单独计算(1 起),
    chunk 取它在各列表中的最好名次。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

from .embeddings import cosine
from .tokenizer import tokenize

# 与 internal/types/retrieval_config.go 的缺省值一致
DEFAULT_RRF_K = 60
DEFAULT_VECTOR_WEIGHT = 0.7
DEFAULT_KEYWORD_WEIGHT = 0.3


@dataclass
class Chunk:
    """对照 Go 的 types.Chunk + SearchResult 的最小字段集。"""

    chunk_id: str
    content: str
    source: str  # 对应 KnowledgeID / 文档名
    embedding: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoredChunk:
    """对照 Go 的 types.IndexWithScore。"""

    chunk: Chunk
    score: float
    vector_rank: int | None = None
    keyword_rank: int | None = None


class HybridStore:
    """内存版向量 + 关键词双索引。对照 WeKnora 的 postgres(pgvector + 全文)单 store。"""

    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self._bm25: BM25Okapi | None = None

    def add(self, chunks: list[Chunk]) -> None:
        self.chunks.extend(chunks)
        self._bm25 = BM25Okapi([tokenize(c.content) for c in self.chunks])

    # ---- 向量路:对照 retriever 的 VectorSearch,返回 cosine 相似度 ----
    def vector_search(self, query_embedding: list[float], top_k: int) -> list[ScoredChunk]:
        scored = [
            ScoredChunk(chunk=c, score=max(0.0, cosine(query_embedding, c.embedding)))
            for c in self.chunks
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]

    # ---- 关键词路:对照 retriever 的 KeywordSearch(BM25) ----
    def keyword_search(self, query: str, top_k: int) -> list[ScoredChunk]:
        assert self._bm25 is not None
        scores = np.asarray(self._bm25.get_scores(tokenize(query)), dtype=np.float64)
        order = np.argsort(-scores)
        out: list[ScoredChunk] = []
        for i in order[:top_k]:
            if scores[i] <= 0:
                continue
            out.append(ScoredChunk(chunk=self.chunks[int(i)], score=float(scores[i])))
        return out


def _best_ranks(lists: list[list[ScoredChunk]]) -> dict[str, int]:
    """对照 Go 的 bestRanks:每个列表内按分数排序得名次(1 起),chunk 取最好名次。"""
    ranks: dict[str, int] = {}
    for lst in lists:
        ordered = sorted(lst, key=lambda s: s.score, reverse=True)
        for i, s in enumerate(ordered, start=1):
            cid = s.chunk.chunk_id
            if cid not in ranks or i < ranks[cid]:
                ranks[cid] = i
    return ranks


def _deduplicate_by_score(lists: list[list[ScoredChunk]]) -> list[ScoredChunk]:
    """对照 Go 的 deduplicateByScore。"""
    best: dict[str, ScoredChunk] = {}
    for s in (item for lst in lists for item in lst):
        cur = best.get(s.chunk.chunk_id)
        if cur is None or s.score > cur.score:
            best[s.chunk.chunk_id] = s
    return sorted(best.values(), key=lambda s: s.score, reverse=True)


def fuse_or_deduplicate(
    vector_lists: list[list[ScoredChunk]],
    keyword_lists: list[list[ScoredChunk]],
    rrf_k: int = DEFAULT_RRF_K,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
) -> list[ScoredChunk]:
    """对照 Go 的 fuseOrDeduplicate / fuseWithRRF,三条路径输出都在 [0,1]。"""
    if not keyword_lists:
        # 仅向量:cosine 分数原样保留(FAQ 阈值依赖它)
        return _deduplicate_by_score(vector_lists)
    if not vector_lists:
        # 仅关键词:BM25 无上界,除以本列表最高分归一,保持相对顺序
        # 对照 Go 的 rescaleUnboundedScores:仅当最高分 >1 才缩放
        flat = _deduplicate_by_score(keyword_lists)
        max_score = max((s.score for s in flat if s.score == s.score), default=0.0)
        if max_score > 1:
            for s in flat:
                s.score = s.score / max_score if s.score > 0 else 0.0
        return flat

    # 混合:加权 RRF,除以理论最大值归一到 [0,1]
    max_rrf = (vector_weight + keyword_weight) / (rrf_k + 1)
    vector_ranks = _best_ranks(vector_lists)
    keyword_ranks = _best_ranks(keyword_lists)

    # 收集全部 chunk,优先保留向量路的元数据(同 Go)
    merged: dict[str, ScoredChunk] = {}
    for s in _deduplicate_by_score(vector_lists):
        merged[s.chunk.chunk_id] = s
    for s in (item for lst in keyword_lists for item in lst):
        merged.setdefault(s.chunk.chunk_id, s)

    result: list[ScoredChunk] = []
    for cid, s in merged.items():
        score = 0.0
        v_rank = vector_ranks.get(cid)
        k_rank = keyword_ranks.get(cid)
        if v_rank is not None:
            score += vector_weight / (rrf_k + v_rank)
        if k_rank is not None:
            score += keyword_weight / (rrf_k + k_rank)
        result.append(
            ScoredChunk(chunk=s.chunk, score=score / max_rrf, vector_rank=v_rank, keyword_rank=k_rank)
        )
    result.sort(key=lambda s: s.score, reverse=True)
    return result


def hybrid_search(
    store: HybridStore,
    query: str,
    query_embedding: list[float],
    candidate_k: int = 20,
    match_count: int = 5,
) -> list[ScoredChunk]:
    """对照 knowledgebase_search.go 的 HybridSearch:两路召回 -> 融合 -> 截断。"""
    vec = store.vector_search(query_embedding, candidate_k)
    kw = store.keyword_search(query, candidate_k)
    fused = fuse_or_deduplicate([vec] if vec else [], [kw] if kw else [])
    return fused[:match_count]
