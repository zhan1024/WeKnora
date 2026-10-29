# 03 · mini 核心代码逐段导读

> 建议用编辑器同时打开本文与源码。每个模块按“输入 → 处理 → 输出 → 边界”读，不必逐行死背。

## 1. 解析：`rag/parsers.py`

### 统一返回结构

```python
@dataclass
class ParsedDocument:
    markdown: str
    metadata: dict[str, Any]

    def is_valid(self) -> bool:
        return self.markdown.strip() != ""
```

这对应 WeKnora docreader 的统一 Markdown 产物。无论输入 HTML、DOCX 还是 PDF，后续分块只接收 `ParsedDocument`。

### 两个责任链

- `FirstParser`：第一个有效结果返回，异常继续；
- `PipelineParser`：上游文本作为下游输入，metadata 合并。

阅读 [parsers.py](../rag/parsers.py) 时思考：为什么 `FirstParser` 返回空文档还要尝试下一个？为什么 `PipelineParser` 不能在每一步丢弃 metadata？

## 2. 分块：`rag/chunker.py`

核心输出：

```python
ParsedChunk(content, start_at, end_at, context_header)
```

调用顺序：

```text
split
→ _find_protected_spans（fenced code）
→ _split_by_headings（标题段与面包屑）
→ 段未超长：直接成块
→ 段超长：_recursive_cut，切点避开代码保护区
```

重点检查 `_recursive_cut` 全部使用原文绝对偏移。把段内下标与全文下标混用，是这类代码最常见的 bug 之一。

源码：[chunker.py](../rag/chunker.py)。

## 3. 入库：`rag/pipeline.py`

```text
STAGE_DOCREADER
→ STAGE_CHUNKING
→ STAGE_EMBEDDING
→ completed
```

任一阶段异常：`failed`，并记录 `error_message = "[阶段] 原因"`。这与 WeKnora“必要阶段失败不能永久 processing”的语义一致。

注意 mini 的 `ingest_file` 是同步执行，且默认语料在 server 启动时重建，没有异步队列和持久化任务表。

源码：[pipeline.py](../rag/pipeline.py)。

## 4. 向量与关键词：`rag/hybrid.py`

### 两路召回

- `HybridStore.vector_search`：对全部 chunk 计算 cosine，按分数降序；
- `HybridStore.keyword_search`：`rank_bm25` 计算关键词分，过滤非正分；
- 返回值统一为 `ScoredChunk(chunk, score, vector_rank, keyword_rank)`。

### 三条融合路径

```text
只有 vector_lists → 按 chunk 去重，保留 cosine
只有 keyword_lists → 去重，BM25 除以列表最高分
两路都有 → 加权 RRF，再除以理论最大值
```

混合路径先通过 `_best_ranks` 计算各列表中的最好名次，再计算：

```python
score = 0.0
if v_rank is not None:
    score += vector_weight / (rrf_k + v_rank)
if k_rank is not None:
    score += keyword_weight / (rrf_k + k_rank)
final = score / max_rrf
```

**必须自己手算一次**：双路都第 1 时结果为 1；仅向量第 1 时结果为 0.7（默认权重下）。

源码：[hybrid.py](../rag/hybrid.py)。

## 5. Embedding：`rag/embeddings.py`

优先级：

1. `EMBEDDING_API_KEY`：独立 OpenAI 兼容 embedding 服务；
2. 非 DeepSeek 的 `OPENAI_API_KEY`：尝试同源 embedding；
3. 无可用服务：`HashEmbeddings` 离线兜底。

哈希向量把 token 哈希到固定桶，再计数和 L2 归一化，能演示向量运算但没有可靠语义能力。真实模型配置示例见 [.env.example](../.env.example)。

## 6. 重排：`rag/rerank.py`

- `NoopReranker`：保持融合顺序，便于离线运行；
- `LLMReranker`：让模型给每个候选打 0～10，再与基础分组合。

这用于理解重排位置，不代表生产级 rerank。生产应评估专门 rerank 模型、批量调用、超时、失败与成本。

## 7. 回答与引用：`rag/qa.py`

### 来源注册

```python
registry = {"c1": "[1]", "c2": "[2]"}
```

Prompt 要求模型输出 `<ref id="c1"/>`，而非让它任意编写 URL。一次性模式用 `_expand_all`；流式模式交给 `CitationStreamExpander`。

### 流式事件顺序

```text
chat.references（来源元数据）
→ chat.start
→ chat.stream * N
→ chat.complete
```

事件同时写入 `MemoryStreamManager` 并yield给当前连接，所以既能实时显示，也能之后按 offset 重放。

源码：[qa.py](../rag/qa.py)。

## 8. 流协议：`rag/stream.py`

重点阅读 `CitationStreamExpander.feed`：

1. `pending + chunk` 拼接；
2. 找到 `<` 且疑似标签但未闭合时存入 pending；
3. 完整 `<ref>` 按 registry 展开，未知别名输出空字符串；
4. 普通 `<` 原样输出；
5. `flush` 处理流结束后的残余。

**错误做法**：每个 chunk 都直接 `re.sub`，或者先 `strip()` 再拼。这样会破坏跨边界标签或改变模型原文。

源码：[stream.py](../rag/stream.py)。

## 9. 一分钟后自测

不看代码回答：

- 为什么 `_best_ranks` 不按列表拼接后的下标排名？
- 为什么分块要同时保存 `start_at/end_at`？
- 为什么 `chat.references` 可以先于正文发送？
- 为什么未知 `<ref>` 要 fail-closed？
- 为什么 mini 重启后要重新计算默认语料向量？
