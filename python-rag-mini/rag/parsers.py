"""文档解析器:多格式 -> 统一 Markdown 产物。

对照 WeKnora:
  docreader/parser/chain_parser.py    FirstParser / PipelineParser 责任链组合器
  docreader/proto/docreader.proto     ReadResult = MarkdownContent + Metadata(统一产物)
  internal/infrastructure/docparser/engine_registry.go  按文件类型路由引擎

精简版保留三个核心设计:
  1. 统一产物:无论 PDF/DOCX/HTML,解析结果都是 Markdown 文本 + metadata
  2. 责任链组合:FirstParser 按序尝试(第一个产出非空内容者胜),
     PipelineParser 把上游输出作为下游输入(元数据累积合并)
  3. 引擎路由:按扩展名查注册表,可配可换

简化:PDF 用 pypdf 纯文本抽取,页字符数过少标记疑似扫描页(对照 docreader
逐页路由的次信号;图片面积主信号需要 pypdfium2,精简版不做)。
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from xml.etree import ElementTree

# 对照 proto: ReadResponse { markdown_content, metadata }
@dataclass
class ParsedDocument:
    markdown: str
    metadata: dict[str, Any] = field(default_factory=dict)

    # 对照 docreader 的 document.is_valid(): content != ""
    def is_valid(self) -> bool:
        return self.markdown.strip() != ""


class Parser:
    """解析器基类。输入原始字节 + 文件名,输出 ParsedDocument。"""

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        raise NotImplementedError


# ---------------------------------------------------------------- 组合器

class FirstParser(Parser):
    """对照 chain_parser.py 的 FirstParser:按序尝试,首个有效结果胜出,
    异常吞掉继续下一个。"""

    def __init__(self, *parsers: Parser):
        self.parsers = parsers

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        for p in self.parsers:
            try:
                doc = p.parse(data, filename)
                if doc.is_valid():
                    return doc
            except Exception:
                continue
        return ParsedDocument(markdown="", metadata={"error": "no parser succeeded"})


class PipelineParser(Parser):
    """对照 chain_parser.py 的 PipelineParser:流水线,上游文本输出作为
    下游输入,metadata 累积合并。"""

    def __init__(self, *parsers: Parser):
        self.parsers = parsers

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        merged: dict[str, Any] = {}
        current = data
        doc = ParsedDocument(markdown="")
        for p in self.parsers:
            doc = p.parse(current, filename)
            merged.update(doc.metadata)
            current = doc.markdown.encode("utf-8")
        doc.metadata = merged
        return doc


# ---------------------------------------------------------------- 具体解析器

class MarkdownParser(Parser):
    """md/txt 直通(对照 docreader 的 MarkdownParser 流水线,精简掉图片抽取)。"""

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        return ParsedDocument(
            markdown=data.decode("utf-8", errors="replace"),
            metadata={"parser": "markdown", "file_type": filename.rsplit(".", 1)[-1]},
        )


class _HTMLToMarkdown(HTMLParser):
    """极简 HTML -> Markdown:标题转 #、段落/块级元素换行、跳过 script/style。
    对照 docreader 的 StdWebParser(精简:不保留链接与表格结构)。"""

    _BLOCK = {"p", "div", "li", "ul", "ol", "table", "tr", "section", "article", "br"}
    _HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._HEADING:
            self.parts.append("\n" + "#" * self._HEADING[tag] + " ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK or tag in self._HEADING:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        out = "".join(self.parts)
        return re.sub(r"\n{3,}", "\n\n", out).strip()


class HtmlParser(Parser):
    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        conv = _HTMLToMarkdown()
        conv.feed(data.decode("utf-8", errors="replace"))
        return ParsedDocument(markdown=conv.text(), metadata={"parser": "html"})


class DocxParser(Parser):
    """DOCX = zip + word/document.xml。段落直通,Heading 样式转 # 标题。
    对照 docreader 的 Docx2Parser = FirstParser(Markitdown, Docx) 的兜底实现。"""

    _W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        lines: list[str] = []
        for p in root.iter(self._W + "p"):
            texts = [t.text or "" for t in p.iter(self._W + "t")]
            line = "".join(texts).strip()
            if not line:
                continue
            style_el = p.find(f"{self._W}pPr/{self._W}pStyle")
            style = style_el.get(self._W + "val", "") if style_el is not None else ""
            m = re.match(r"Heading(\d)", style, re.I)
            lines.append("#" * min(int(m.group(1)), 6) + " " + line if m else line)
        return ParsedDocument(markdown="\n\n".join(lines), metadata={"parser": "docx"})


class PdfParser(Parser):
    """pypdf 逐页抽文本。对照 docreader builtin PDF 的逐页路由:
    字符数 < 10 的页记入 metadata['scanned_pages'](疑似扫描页,
    完整版会进一步走 VLM OCR;精简版只标记不 OCR)。"""

    MIN_CHARS = 10  # 对照 DOCREADER_PDF_SCAN_MIN_CHARS

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError("PDF 支持需要: pip install pypdf") from e
        reader = PdfReader(io.BytesIO(data))
        pages, scanned = [], []
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if len(text) < self.MIN_CHARS:
                scanned.append(i + 1)  # 1-based 页码
                continue
            pages.append(text)
        return ParsedDocument(
            markdown="\n\n".join(pages),
            metadata={"parser": "pdf", "page_count": len(reader.pages), "scanned_pages": scanned},
        )


# ---------------------------------------------------------------- 引擎路由
# 对照 engine_registry.go / ResolveParserEngine(fileType):扩展名 -> 解析器

PARSER_REGISTRY: dict[str, Parser] = {
    "md": MarkdownParser(),
    "markdown": MarkdownParser(),
    "txt": MarkdownParser(),
    "html": HtmlParser(),
    "htm": HtmlParser(),
    "docx": DocxParser(),
    "pdf": PdfParser(),
}


def parse_file(path: str) -> ParsedDocument:
    """按扩展名路由解析器,返回统一 Markdown 产物。对照 docparser.NewReader。"""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    parser = PARSER_REGISTRY.get(ext)
    if parser is None:
        raise ValueError(f"不支持的文件类型: .{ext}(已注册: {sorted(PARSER_REGISTRY)})")
    with open(path, "rb") as f:
        doc = parser.parse(f.read(), path)
    doc.metadata.setdefault("file_name", path.rsplit("/", 1)[-1])
    doc.metadata.setdefault("file_type", ext)
    return doc
