"""文档入库管线:解析 -> 分块 -> 向量化 -> 入索引,带状态机。

对照 WeKnora internal/application/service/knowledge_process.go:
  - beginStage(StageDocReader) / StageChunking / StageEmbedding 分阶段记录
  - 任一阶段失败:ParseStatus=failed + error_message,不停留在 processing
  - 全部成功:completed

精简为同步执行(WeKnora 用 asynq 异步队列,教学版聚焦状态与产物)。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.embeddings import Embeddings

from .chunker import split
from .hybrid import Chunk, HybridStore
from .parsers import ParsedDocument, parse_file

# 对照 knowledge.go 的 Stage 常量
STAGE_DOCREADER = "docreader"
STAGE_CHUNKING = "chunking"
STAGE_EMBEDDING = "embedding"

# 对照 types/knowledge.go 的 parse_status 状态机
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass
class IngestTask:
    """对照 types.Knowledge 的处理字段子集。"""

    knowledge_id: str
    file_name: str
    parse_status: str = STATUS_PROCESSING
    stage: str = ""
    error_message: str = ""
    chunk_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0


def ingest_file(path: str, store: HybridStore, embed: Embeddings,
                chunk_size: int = 1000, overlap: int = 100) -> IngestTask:
    """对照 knowledge_process.go 主流程。任何阶段异常都落 failed + error_message。"""
    task = IngestTask(knowledge_id=str(uuid.uuid4())[:8], file_name=path.rsplit("/", 1)[-1])
    t0 = time.time()
    try:
        # Stage 1: 解析(对照 convert / StageDocReader),产物统一为 Markdown
        task.stage = STAGE_DOCREADER
        doc: ParsedDocument = parse_file(path)
        if not doc.is_valid():
            raise ValueError("解析结果为空(document.is_valid() = false)")
        task.metadata.update(doc.metadata)

        # Stage 2: 分块(对照 processChunks / StageChunking)
        task.stage = STAGE_CHUNKING
        pieces = split(doc.markdown, chunk_size=chunk_size, overlap=overlap)
        if not pieces:
            raise ValueError("分块结果为空")
        chunks = [
            Chunk(
                chunk_id=str(uuid.uuid4())[:8],
                content=p.content,
                source=task.file_name,
                metadata={
                    "knowledge_id": task.knowledge_id,
                    "start_at": p.start_at,   # 对照 chunks.start_at,引用定位用
                    "end_at": p.end_at,
                    "context_header": p.context_header,  # 对照 chunks.context_header
                },
            )
            for p in pieces
        ]

        # Stage 3: 向量化 + 入索引(对照 StageEmbedding;失败原因如模型被删会记入 error_message)
        task.stage = STAGE_EMBEDDING
        vectors = embed.embed_documents([c.content for c in chunks])
        for c, v in zip(chunks, vectors):
            c.embedding = v
        store.add(chunks)

        task.chunk_count = len(chunks)
        task.parse_status = STATUS_COMPLETED
    except Exception as e:  # 对照 failStage:任何阶段失败都不停留在 processing
        task.parse_status = STATUS_FAILED
        task.error_message = f"[{task.stage}] {e}"
    task.elapsed_ms = int((time.time() - t0) * 1000)
    return task
