"""Embedding 实现。

对照 WeKnora: internal/models/embedding 下是多 provider 的远程 embedding 适配
(Ollama / OpenAI 兼容 API 等)。精简版提供两种:

- OpenAIEmbeddings: 设置 OPENAI_API_KEY 时走真实 embedding API(推荐)
- HashEmbeddings: 无 key 时的离线兜底,把 token 哈希到固定维度桶里计数,
  再做 L2 归一化。确定性强、零依赖,能跑通整条链路,语义能力弱于真实模型。
"""
from __future__ import annotations

import hashlib
import math
import os

import numpy as np
from langchain_core.embeddings import Embeddings

from .tokenizer import tokenize


class HashEmbeddings(Embeddings):
    """确定性哈希 embedding,离线演示用。"""

    def __init__(self, dim: int = 2048):
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float64)
        for tok in tokenize(text):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def get_embeddings() -> Embeddings:
    """Embedding 选择,优先级:
    1. EMBEDDING_API_KEY(+ 可选 EMBEDDING_BASE_URL / EMBEDDING_MODEL)
       —— 独立的 embedding 服务,如硅基流动/阿里百炼
    2. OPENAI_API_KEY 且端点非 DeepSeek —— DeepSeek 无 embeddings API
    3. 离线哈希兜底(仅演示)
    """
    emb_key = os.environ.get("EMBEDDING_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    openai_base = os.environ.get("OPENAI_BASE_URL", "")

    if emb_key:
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
            api_key=emb_key,
            base_url=os.environ.get("EMBEDDING_BASE_URL") or None,
        )
    if openai_key and "deepseek" not in openai_base.lower():
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
            base_url=openai_base or None,
        )
    if openai_key:
        print("[提示] DeepSeek 无 embeddings API,向量路用离线哈希 embedding;")
        print("       如需真实语义向量,请配置 EMBEDDING_API_KEY(如硅基流动免费 key)")
    else:
        print("[提示] 未设置 OPENAI_API_KEY,使用离线哈希 embedding(仅演示)")
    return HashEmbeddings()


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a), np.asarray(b)
    denom = math.sqrt(float(va @ va)) * math.sqrt(float(vb @ vb))
    return float(va @ vb / denom) if denom else 0.0
