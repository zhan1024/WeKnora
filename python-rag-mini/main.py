"""WeKnora RAG 精简对照版 — CLI 入口。

子命令:
    python main.py ingest <文件或目录>...   # 文档解析管线:解析->分块->向量化->入库
    python main.py ask "问题"               # 混合检索 + 一次性回答
    python main.py ask "问题" --stream      # 流式回答(事件流 + 引用解码)
    python main.py "问题"                   # 兼容旧用法,等价 ask

链路对照 WeKnora: 文档解析(docreader) -> 结构化分块(chunker) ->
向量+BM25 双路召回 -> 加权 RRF 融合 -> 截断 -> (可选)重排 ->
带引用生成(一次性 / SSE 事件流)。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

def _load_dotenv() -> None:
    """极简 .env 加载:KEY=VALUE 逐行注入,不覆盖已有环境变量。"""
    env_path = pathlib.Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

from rag.chunker import split
from rag.embeddings import get_embeddings
from rag.hybrid import Chunk, HybridStore, hybrid_search
from rag.parsers import parse_file
from rag.pipeline import ingest_file
from rag.qa import answer, stream_answer
from rag.rerank import get_reranker
from rag.stream import MemoryStreamManager, format_sse, replay_sse

import uuid


def cmd_ingest(args, embed) -> HybridStore:
    store = HybridStore()
    paths: list[str] = []
    for p in args.paths:
        pp = pathlib.Path(p)
        if pp.is_dir():
            paths.extend(str(x) for x in sorted(pp.rglob("*")) if x.suffix.lower()
                         in {".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".pdf"})
        else:
            paths.append(str(pp))
    for path in paths:
        task = ingest_file(path, store, embed,
                           chunk_size=args.chunk_size, overlap=args.overlap)
        icon = {"completed": "✓", "failed": "✗", "processing": "…"}[task.parse_status]
        print(f"{icon} {task.file_name}: {task.parse_status} "
              f"(chunks={task.chunk_count}, {task.elapsed_ms}ms) meta={task.metadata}")
        if task.parse_status == "failed":
            print(f"   错误: {task.error_message}")
    print(f"索引总量: {len(store.chunks)} 个分块")
    return store


def cmd_ask(args, embed) -> None:
    # 兼容目录直读(旧行为):直接用 chunker.split 而不走 ingest 状态机
    store = HybridStore()
    paths = sorted(pathlib.Path(args.dir).glob("*.md")) + sorted(
        pathlib.Path(args.dir).glob("*.txt"))
    chunks: list[Chunk] = []
    for path in paths:
        doc = parse_file(str(path))
        for p in split(doc.markdown, chunk_size=args.chunk_size, overlap=args.overlap):
            chunks.append(Chunk(chunk_id=str(uuid.uuid4())[:8], content=p.content,
                                source=path.name,
                                metadata={"context_header": p.context_header}))
    vecs = embed.embed_documents([c.content for c in chunks])
    for c, v in zip(chunks, vecs):
        c.embedding = v
    store.add(chunks)
    print(f"已加载 {len(chunks)} 个分块(来源: {args.dir})")

    print("== 混合检索(向量 + BM25,加权 RRF 融合)==")
    results = hybrid_search(store, args.question, embed.embed_query(args.question),
                            candidate_k=args.candidate, match_count=args.match)
    for i, r in enumerate(results, 1):
        header = r.chunk.metadata.get("context_header", "")
        print(f"   [{i}] score={r.score:.4f} 向量名次={r.vector_rank} "
              f"关键词名次={r.keyword_rank} 来源={r.chunk.source}"
              + (f" 面包屑={header}" if header else ""))

    reranker = get_reranker()
    results = reranker.rerank(args.question, results)
    print(f"重排器: {type(reranker).__name__}")

    if not args.stream:
        print("== 回答(一次性)==")
        ans = answer(args.question, results)
        print(ans.text)
        if ans.sources:
            print("\n引用来源:")
            for s in ans.sources:
                print(f"   - {s.chunk.source}#{s.chunk.chunk_id}")
        return

    # 流式:事件写入 StreamManager,同时打印 SSE 帧;最后演示断线重放
    print("== 回答(流式 SSE)==")
    manager = MemoryStreamManager()
    session_id = "demo-session"
    answer_text: list[str] = []
    message_id = ""
    for event in stream_answer(args.question, results, manager, session_id):
        raw = format_sse(event)
        if event.type == "chat.stream":
            answer_text.append(event.data["content"])
        else:
            print(raw.rstrip())
        if event.type == "chat.references":
            message_id = event.data["message_id"]
    print("--- 客户端实际看到的增量文本 ---")
    print("".join(answer_text))

    print("\n== 断线重放演示(从 offset 0 重放全部事件帧数 =="
          f" {len(manager.get_events(session_id, message_id, 0)[0])}) ==")
    frames = list(replay_sse(manager, session_id, message_id, from_offset=0))
    print(f"重放 {len(frames)} 帧,首帧类型: {frames[0].splitlines()[0] if frames else '无'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="WeKnora RAG 精简对照版")
    sub = parser.add_subparsers(dest="cmd")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--chunk-size", type=int, default=1000)
    common.add_argument("--overlap", type=int, default=100)

    p_ingest = sub.add_parser("ingest", parents=[common], help="文档解析入库")
    p_ingest.add_argument("paths", nargs="+", help="文件或目录")

    p_ask = sub.add_parser("ask", parents=[common], help="检索问答")
    p_ask.add_argument("question")
    p_ask.add_argument("--dir", default="sample_data")
    p_ask.add_argument("--match", type=int, default=5)
    p_ask.add_argument("--candidate", type=int, default=20)
    p_ask.add_argument("--stream", action="store_true", help="流式输出")

    # 兼容旧用法: python main.py "问题" —— 首个参数不是子命令时按 ask 处理
    argv = sys.argv[1:]
    if argv and argv[0] not in ("ingest", "ask", "-h", "--help"):
        argv = ["ask", *argv]
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        sys.exit(1)

    embed = get_embeddings()
    if args.cmd == "ingest":
        cmd_ingest(args, embed)
    else:
        cmd_ask(args, embed)


if __name__ == "__main__":
    main()
