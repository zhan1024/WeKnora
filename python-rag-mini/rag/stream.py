"""流式协议:append-only 事件流 + SSE + 引用流解码器。

对照 WeKnora:
  internal/stream/factory.go / memory_manager.go
      StreamManager 接口只有 AppendEvent / GetEvents(fromOffset) 两个方法,
      生产者只追加,消费者按 offset 拉取 —— 任意时刻可从头重放,
      这是断线重连续传(continue-stream)的基础。
  internal/event/event.go
      事件类型常量(chat.stream / retrieval.complete / ...)。
  internal/modelcontext/citations.go  citationStreamExpander
      流式输出时,私有 <ref id="cN"/> 标签可能被拆到两个 chunk 边界两侧,
      解码器把不完整标签缓存在 pending,凑齐后展开为公开引用,
      未知别名 fail-closed 直接删除 —— 部分标签永不上"线"。
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

# ---- 事件类型,对照 internal/event/event.go 的命名风格 ----
EVENT_RETRIEVAL_START = "retrieval.start"
EVENT_RETRIEVAL_COMPLETE = "retrieval.complete"
EVENT_CHAT_START = "chat.start"
EVENT_CHAT_STREAM = "chat.stream"        # 增量内容
EVENT_CHAT_REFERENCES = "chat.references"  # 引用列表(生成前下发)
EVENT_CHAT_COMPLETE = "chat.complete"
EVENT_ERROR = "error"


@dataclass
class StreamEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


class MemoryStreamManager:
    """对照 internal/stream/memory_manager.go:
    进程内 append-only 事件存储。key = (session_id, message_id)。"""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], list[StreamEvent]] = {}
        self._lock = threading.RLock()

    def append_event(self, session_id: str, message_id: str, event: StreamEvent) -> None:
        with self._lock:
            self._events.setdefault((session_id, message_id), []).append(event)

    def get_events(self, session_id: str, message_id: str,
                   from_offset: int = 0) -> tuple[list[StreamEvent], int]:
        """返回 (从 from_offset 起的事件副本, 下一 offset)。
        对照 GetEvents 返回副本避免竞态;重放时传 0。"""
        with self._lock:
            events = list(self._events.get((session_id, message_id), []))
        return events[from_offset:], len(events)


_REF_TAG_RE = re.compile(r'^<ref\s+id="([^"]+)"\s*/?>$', re.I)
_TAG_PREFIXES = ("<ref", "<kb", "<web")  # 对照 isSourceTagPending 关心的标签族


def _is_tag_start(data: str) -> bool:
    """对照 isRefTagStart/isNamedTagStart:判断 data 以某标签开头。"""
    lower = data.lower()
    for name in ("ref", "kb", "web"):
        prefix = "<" + name
        if lower.startswith(prefix):
            if len(lower) == len(prefix) or lower[len(prefix)] in " \t\r\n>":
                return True
    return False


def _could_be_tag_prefix(data: str) -> bool:
    """data(以 < 开头)可能是某标签的前半段,如 '<re'、'<ref id='。"""
    lower = data.lower()
    return any(p.startswith(lower) or _is_tag_start(lower) for p in _TAG_PREFIXES)


class CitationStreamExpander:
    """逐行对照 citations.go 的 citationStreamExpander。

    Feed(chunk) -> 可安全输出的文本。未凑齐的标签留在 pending,
    完整的 <ref id="cN"/> 用 registry 映射展开(c1 -> "[1]"),
    未知别名 fail-closed 删除,<kb>/<web> 标签剥掉不输出。
    """

    def __init__(self, registry: dict[str, str]):
        self.registry = registry
        self.pending = ""

    def feed(self, chunk: str) -> str:
        data = self.pending + chunk
        self.pending = ""
        out: list[str] = []
        while data:
            idx = data.find("<")
            if idx < 0:
                out.append(data)
                break
            out.append(data[:idx])
            data = data[idx:]
            if _could_be_tag_prefix(data) and ">" not in data:
                # 标签被流边界切开:缓存等待下一个 chunk
                self.pending = data
                break
            if _is_tag_start(data):
                end = data.find(">")
                if end < 0:
                    self.pending = data
                    break
                tag = data[: end + 1]
                m = _REF_TAG_RE.match(tag)
                if m:
                    # 已知别名展开,未知别名 fail-closed 丢弃
                    out.append(self.registry.get(m.group(1), ""))
                # <kb>/<web> 等内部标签直接剥掉
                data = data[end + 1 :]
                continue
            out.append("<")
            data = data[1:]
        return "".join(out)

    def flush(self) -> str:
        """流结束时冲刷 pending(残缺的标签按普通文本输出之外的策略:直接丢弃)。"""
        tail, self.pending = self.pending, ""
        return "" if tail.startswith("<") else tail


def format_sse(event: StreamEvent) -> str:
    """SSE 帧格式化:`event: <type>` + `data: <json>` + 空行。"""
    return f"event: {event.type}\ndata: {json.dumps(asdict(event), ensure_ascii=False)}\n\n"


def replay_sse(manager: MemoryStreamManager, session_id: str, message_id: str,
               from_offset: int = 0) -> Iterator[str]:
    """对照 continue-stream:从指定 offset 重放历史事件为 SSE 帧。"""
    events, _ = manager.get_events(session_id, message_id, from_offset)
    for e in events:
        yield format_sse(e)
