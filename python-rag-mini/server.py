"""WeKnora RAG 精简对照版 HTTP/SSE 服务。

启动:
    .venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000

接口:
    GET  /health                         服务状态
    GET  /docs                           FastAPI 在线接口文档
    POST /api/ingest                     解析并入库项目内文件/目录
    POST /api/ask                        一次性检索问答
    GET  /api/ask/stream?question=...    SSE 流式检索问答
    GET  /api/events/{session}/{message} 按 offset 重放事件(断线续传)
"""
from __future__ import annotations

import os
import pathlib
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict

from main import _load_dotenv

_load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rag.embeddings import get_embeddings
from rag.hybrid import HybridStore, hybrid_search
from rag.pipeline import ingest_file
from rag.qa import answer, stream_answer
from rag.rerank import get_reranker
from rag.stream import MemoryStreamManager, StreamEvent, format_sse

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".pdf"}

store = HybridStore()
stream_manager = MemoryStreamManager()
store_lock = threading.Lock()
embeddings = None
loaded_files: list[str] = []


def _resolve_local_path(raw_path: str) -> pathlib.Path:
    """仅允许读取本项目目录,避免把本地文件服务变成任意文件读取接口。"""
    path = pathlib.Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path 必须位于项目目录内") from exc
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {raw_path}")
    return resolved


def _collect_files(path: pathlib.Path) -> list[pathlib.Path]:
    if path.is_file():
        files = [path]
    else:
        files = sorted(p for p in path.rglob("*") if p.is_file())
    return [p for p in files if p.suffix.lower() in SUPPORTED_EXTENSIONS]


def _ingest(raw_path: str) -> list:
    path = _resolve_local_path(raw_path)
    files = _collect_files(path)
    if not files:
        raise HTTPException(status_code=400, detail="目录中没有支持的文档")
    tasks = []
    with store_lock:
        for file_path in files:
            task = ingest_file(str(file_path), store, embeddings)
            tasks.append(task)
            if task.parse_status == "completed":
                loaded_files.append(task.file_name)
    return tasks


def _search(question: str, match: int, candidate: int):
    with store_lock:
        if not store.chunks:
            raise HTTPException(status_code=400, detail="知识库为空,请先调用 /api/ingest")
        query_embedding = embeddings.embed_query(question)
        results = hybrid_search(store, question, query_embedding,
                                candidate_k=candidate, match_count=match)
    return get_reranker().rerank(question, results)


def _result_dict(index: int, result) -> dict:
    return {
        "index": index,
        "chunk_id": result.chunk.chunk_id,
        "source": result.chunk.source,
        "score": round(result.score, 6),
        "vector_rank": result.vector_rank,
        "keyword_rank": result.keyword_rank,
        "context_header": result.chunk.metadata.get("context_header", ""),
        "start_at": result.chunk.metadata.get("start_at"),
        "end_at": result.chunk.metadata.get("end_at"),
        "content": result.chunk.content,
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    global embeddings
    embeddings = get_embeddings()
    tasks = _ingest("sample_data")
    failed = [t.file_name for t in tasks if t.parse_status == "failed"]
    if failed:
        raise RuntimeError(f"默认语料入库失败: {failed}")
    yield


app = FastAPI(
    title="WeKnora RAG Mini",
    version="0.1.0",
    description="WeKnora 文档解析、混合检索、RRF 融合、引用问答与 SSE 流协议的 Python 精简对照版。",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "web"), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "web" / "index.html")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    match: int = Field(default=5, ge=1, le=20)
    candidate: int = Field(default=20, ge=1, le=100)


class IngestRequest(BaseModel):
    path: str = Field(default="sample_data", description="本项目内的文件或目录路径")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "chunks": len(store.chunks),
        "files": loaded_files,
        "embedding": type(embeddings).__name__ if embeddings else "not_ready",
    }


@app.post("/api/ingest")
def ingest(request: IngestRequest) -> dict:
    tasks = _ingest(request.path)
    return {
        "tasks": [asdict(task) for task in tasks],
        "total_chunks": len(store.chunks),
    }


@app.post("/api/ask")
def ask(request: AskRequest) -> dict:
    results = _search(request.question, request.match, request.candidate)
    generated = answer(request.question, results)
    return {
        "question": request.question,
        "answer": generated.text,
        "retrieved": [_result_dict(i, r) for i, r in enumerate(results, 1)],
        "citations": [r.chunk.chunk_id for r in generated.sources],
    }


@app.get("/api/ask/stream")
def ask_stream(question: str = Query(min_length=1),
               match: int = Query(default=5, ge=1, le=20),
               candidate: int = Query(default=20, ge=1, le=100),
               session_id: str = Query(default="web-session")) -> StreamingResponse:
    results = _search(question, match, candidate)
    message_id = str(uuid.uuid4())[:8]
    events = stream_answer(question, results, stream_manager, session_id, message_id)

    def frames():
        yield format_sse(StreamEvent("meta", {
            "message_id": message_id, "session_id": session_id}))
        for event in events:
            yield format_sse(event)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/events/{session_id}/{message_id}")
def events(session_id: str, message_id: str, offset: int = Query(default=0, ge=0)) -> dict:
    items, next_offset = stream_manager.get_events(session_id, message_id, offset)
    return {"events": [asdict(e) for e in items], "next_offset": next_offset}
