"""结构化分块器。

对照 WeKnora internal/infrastructure/chunker:
  strategy.go          Split() 的策略链:heading 优先,失败回退 recursive/legacy
  heading_splitter.go  按 Markdown 标题层级切段
  header_tracker.go    维护标题面包屑(ContextHeader)
  fenced_code.go       保护 ``` 代码块,绝不在中间切断
  splitter.go          超长段递归字符切分兜底
  processChunks        每块记录 StartAt/EndAt 原文偏移(用于引用定位/高亮)

精简版输出块字段与 Go 的 ParsedChunk 对齐:
  content / start_at / end_at / context_header
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^```")


@dataclass
class ParsedChunk:
    """对照 Go 的 types.ParsedChunk。"""

    content: str
    start_at: int          # 原文偏移(字符),对照 StartAt
    end_at: int            # 对照 EndAt
    context_header: str = ""  # 标题面包屑,对照 ContextHeader(如 "检索引擎 > 混合检索")


def _find_protected_spans(text: str) -> list[tuple[int, int]]:
    """对照 fenced_code.go:找出 ```...``` 区间的 (start, end),分块不得切断。"""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for m in re.finditer(r"^.*$", text, re.M):
        if _FENCE_RE.match(m.group()):
            if start is None:
                start = m.start()
            else:
                spans.append((start, m.end()))
                start = None
    if start is not None:  # 未闭合围栏:保护到文末
        spans.append((start, len(text)))
    return spans


def _split_by_headings(text: str) -> list[tuple[int, int, str]]:
    """对照 heading_splitter + header_tracker:
    遇标题行切新段,面包屑 = 当前生效的标题路径。返回 (start, end, header)。"""
    sections: list[tuple[int, int, str]] = []
    # heading_stack[level] = title,组合成面包屑
    heading_stack: dict[int, str] = {}
    sec_start = 0
    cur_header = ""

    for m in re.finditer(r"^.*$", text, re.M):
        line = m.group()
        hm = _HEADING_RE.match(line)
        if hm:
            if m.start() > sec_start and text[sec_start:m.start()].strip():
                sections.append((sec_start, m.start(), cur_header))
            level = len(hm.group(1))
            heading_stack[level] = hm.group(2).strip()
            # 更深的旧标题出栈(对照 header_tracker 的层级维护)
            for k in [k for k in heading_stack if k > level]:
                del heading_stack[k]
            cur_header = " > ".join(heading_stack[k] for k in sorted(heading_stack))
            sec_start = m.start()
    if text[sec_start:].strip():
        sections.append((sec_start, len(text), cur_header))
    return sections


def _recursive_cut(text: str, sec_start: int, sec_end: int, chunk_size: int,
                   overlap: int, protected: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """对照 splitter.go 的兜底递归切分:按 段落→句子→字符 优先级找自然切点,
    切点避开保护区间。全程使用全文绝对坐标,返回 (start, end)。"""
    out: list[tuple[int, int]] = []
    pos = sec_start
    seps = ["\n\n", "\n", "。", "，", " "]  # 由粗到细
    while pos < sec_end:
        end = min(pos + chunk_size, sec_end)
        if end < sec_end:
            # 在窗口内找最后一个分隔符,让切点更自然
            for sep in seps:
                idx = text.rfind(sep, pos + overlap, end)
                if idx > pos:
                    end = idx + len(sep)
                    break
            # 避开 fenced code 保护区间:落在区间中间则延长到区间结束
            for s_start, s_end in protected:
                if s_start < end < s_end:
                    end = min(s_end, sec_end)
        out.append((pos, end))
        if end >= sec_end:
            break
        pos = max(end - overlap, pos + 1)
    return out


def split(text: str, chunk_size: int = 1000, overlap: int = 100) -> list[ParsedChunk]:
    """对照 strategy.go 的 Split():标题策略优先,超长段回退递归切分。"""
    if not text.strip():
        return []
    protected = _find_protected_spans(text)
    chunks: list[ParsedChunk] = []
    for start, end, header in _split_by_headings(text):
        section = text[start:end]
        if len(section) <= chunk_size:
            chunks.append(ParsedChunk(section.strip(), start, end, header))
            continue
        # 超长段:递归切分,切点避开代码块保护区(宁可超长也不在 ``` 中间切断)
        for s, e in _recursive_cut(text, start, end, chunk_size, overlap, protected):
            piece = text[s:e].strip()
            if piece:
                chunks.append(ParsedChunk(piece, s, e, header))
    return chunks
