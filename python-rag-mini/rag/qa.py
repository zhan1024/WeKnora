"""带引用的问答链(支持一次性与流式两种模式)。

对照 WeKnora:
  internal/agent/grounding_prompt.go       约束模型「基于给定知识回答」
  internal/modelcontext/sources.go         请求级来源注册,分配低熵别名 cN
  internal/modelcontext/citations.go       ref 标签展开 / 流式解码(见 rag/stream.py)
  internal/handler/session/...             chat.stream 增量事件 -> SSE

协议设计(与 WeKnora 一致):
  prompt 要求模型行内引用时输出私有标签 <ref id="cN"/>(N 为资料编号);
  输出给客户端前由 expander 展开为公开形式 "[N]";未知别名 fail-closed 丢弃。
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Iterator

from .hybrid import ScoredChunk
from .stream import (CitationStreamExpander, MemoryStreamManager, StreamEvent,
                     EVENT_CHAT_COMPLETE, EVENT_CHAT_REFERENCES, EVENT_CHAT_START,
                     EVENT_CHAT_STREAM)

PROMPT_TEMPLATE = """你是知识库问答助手。严格基于下面的资料回答问题,不要编造;
回答中引用资料时,在句末插入 <ref id="c编号"/> 标注来源(例如 <ref id="c1"/>);
资料不足时直接说明不知道。

{context}

问题:{question}
回答:"""


@dataclass
class Answer:
    text: str
    sources: list[ScoredChunk]


def build_registry(results: list[ScoredChunk]) -> dict[str, str]:
    """对照 sources.go:为每条资料分配别名 cN -> 公开引用文本 [N]。"""
    return {f"c{i}": f"[{i}]" for i, _ in enumerate(results, 1)}


def build_context(results: list[ScoredChunk]) -> str:
    return "\n\n".join(
        f"<source id=\"c{i}\" name=\"{r.chunk.source}\">\n{r.chunk.content}\n</source>"
        for i, r in enumerate(results, 1)
    )


def _llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.environ.get("CHAT_MODEL", "gpt-4o-mini"),
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        temperature=0,
    )


def _offline_text(question: str, results: list[ScoredChunk]) -> str:
    """离线演示回答:引用私有标签,与线上协议同构。"""
    lines = ["[离线模式] 未配置 OPENAI_API_KEY,以下为检索到的资料:"]
    for i, r in enumerate(results, 1):
        lines.append(f'{r.chunk.content[:80].strip()}… <ref id="c{i}"/>')
    return "\n".join(lines)


def _expand_all(text: str, registry: dict[str, str]) -> str:
    """一次性展开(对照 citations.go 的 ExpandText):未知别名删除。"""
    return re.sub(
        r'<ref\s+id="([^"]+)"\s*/?>', lambda m: registry.get(m.group(1), ""), text
    )


def _cited(text: str, results: list[ScoredChunk]) -> list[ScoredChunk]:
    cited: list[ScoredChunk] = []
    for n in dict.fromkeys(int(m) for m in re.findall(r"\[(\d+)\]", text)):
        if 1 <= n <= len(results):
            cited.append(results[n - 1])
    return cited


# ---------------------------------------------------------------- 一次性模式

def answer(question: str, results: list[ScoredChunk]) -> Answer:
    if not results:
        return Answer(text="知识库中没有找到相关内容。", sources=[])
    registry = build_registry(results)
    if os.environ.get("OPENAI_API_KEY"):
        raw = str(_llm().invoke(
            PROMPT_TEMPLATE.format(context=build_context(results), question=question)
        ).content)
    else:
        raw = _offline_text(question, results)
    text = _expand_all(raw, registry)
    return Answer(text=text, sources=_cited(text, results))


# ---------------------------------------------------------------- 流式模式

def stream_answer(question: str, results: list[ScoredChunk],
                  manager: MemoryStreamManager, session_id: str,
                  message_id: str | None = None) -> Iterator[StreamEvent]:
    """对照 chat_pipeline:引用列表先下发,内容经 citationStreamExpander 增量下发。
    事件写入 StreamManager(可重放),同时作为生成器产出给当前连接。"""
    message_id = message_id or str(uuid.uuid4())[:8]

    def emit(event: StreamEvent) -> StreamEvent:
        manager.append_event(session_id, message_id, event)
        return event

    registry = build_registry(results)
    # 1. 引用元数据先行(前端据此渲染角标悬浮卡)
    yield emit(StreamEvent(EVENT_CHAT_REFERENCES, {
        "message_id": message_id,
        "references": [
            {"alias": f"c{i}", "label": f"[{i}]", "source": r.chunk.source,
             "chunk_id": r.chunk.chunk_id, "score": round(r.score, 4),
             "context_header": r.chunk.metadata.get("context_header", ""),
             "content": r.chunk.content}
            for i, r in enumerate(results, 1)
        ],
    }))
    yield emit(StreamEvent(EVENT_CHAT_START, {"message_id": message_id}))

    # 2. 增量内容:每个片段先过 expander,部分标签不会泄漏到线上
    expander = CitationStreamExpander(registry)
    full: list[str] = []
    if os.environ.get("OPENAI_API_KEY"):
        stream = _llm().stream(
            PROMPT_TEMPLATE.format(context=build_context(results), question=question))
        pieces = (str(c.content) for c in stream)
    else:
        # 离线模拟流:故意按 6 字符切片,演示 <ref> 标签跨 chunk 重组
        raw = _offline_text(question, results)
        pieces = (raw[i:i + 6] for i in range(0, len(raw), 6))
    for piece in pieces:
        full.append(piece)
        delta = expander.feed(piece)
        if delta:
            yield emit(StreamEvent(EVENT_CHAT_STREAM, {"content": delta}))
        time.sleep(0.01)  # 演示用节流,便于观察流式效果
    tail = expander.flush()
    if tail:
        yield emit(StreamEvent(EVENT_CHAT_STREAM, {"content": tail}))

    text = "".join(full)
    yield emit(StreamEvent(EVENT_CHAT_COMPLETE, {
        "message_id": message_id,
        "content": _expand_all(text, registry),
        "cited": [_cited(_expand_all(text, registry), results).__len__()],
    }))
