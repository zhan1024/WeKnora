# 03 · 文档解析与分块管线

## 学习目标

- 解释为什么 docreader 独立、为什么统一产物是 Markdown。
- 读懂解析责任链、PDF 路由、结构化分块、父子分块与原文定位。
- 说清异步任务、状态机、失败重试与图片富化为何不能混成一个大函数。

## 1. 架构切分

WeKnora 的 docreader 是独立 Python gRPC 服务，Go App 负责业务状态、存储、分块、向量化和后处理。接口见 [docreader.proto](../../docreader/proto/docreader.proto)：

- `Read`：一次性返回 Markdown、图片、metadata、SourceBlock；
- `ReadStream`：先发 meta，再每图一帧，避免大扫描件超过 unary 消息上限并控制内存；
- `ListEngines`：保留兼容，引擎目录主要由 Go 侧管理。

**统一产物**：不同格式最终归一为 `markdown_content + image_refs + metadata + source_blocks`，下游不必理解 PDF/DOCX/HTML 的全部差异。

## 2. 解析器责任链

[chain_parser.py](../../docreader/parser/chain_parser.py) 提供两种组合：

- `FirstParser`：按序尝试，第一个有效结果返回，异常继续；
- `PipelineParser`：上游输出作为下游输入，metadata 累积。

mini 在 [parsers.py](../../python-rag-mini/rag/parsers.py) 复刻这两个组合器，并实现 md/txt/html/docx/pdf 五种解析器。

**工程价值**：新增格式或备用引擎时，不重写主流程；可以组合“主解析器 + 兜底解析器”“网页抽取 + Markdown 规范化”。

## 3. PDF 不是简单调用 `extract_text`

主项目 [pdf_parser.py](../../docreader/parser/pdf_parser.py) 对每页判断文本型或扫描型：重要信号包括图片面积覆盖率和文本层字符数。扫描页可能需要走后续视觉/OCR 管线，不能盲信劣质文本层。

mini 只复刻“字符过少页标记为疑似扫描页”这一简化信号，见 `PdfParser.MIN_CHARS`。mini **没有实现**图片面积路由、OCR 和 VLM 图像描述。

## 4. 分块决定了检索上限

主项目分块器：[internal/infrastructure/chunker](../../internal/infrastructure/chunker/strategy.go)。

| 机制 | 解决问题 |
|---|---|
| 标题层级切分 | 尽量让同一块属于同一语义章节 |
| `context_header` 面包屑 | 小块保留“属于哪一章”的上下文 |
| fenced code 保护 | 避免代码在中间被截断，影响检索与展示 |
| 原文 `StartAt/EndAt` | 支持引用定位、高亮与原文还原 |
| 递归兜底 | 无标题或超长内容仍能控制块大小 |
| 父子分块 | 子块负责精准召回，父块负责补充上下文 |

mini 对应 [chunker.py](../../python-rag-mini/rag/chunker.py)，复刻标题、面包屑、代码保护、偏移和递归切分；**未复刻父子分块**。

### 父子分块为何有效

小块更精准，大块上下文更完整。父块只入数据库、不进向量索引；子块被命中后再回捞父块或相邻上下文。主项目装配见 [processChunks](../../internal/application/service/knowledge_process.go)。

## 5. 入库状态与异步边界

典型状态包括 `pending → processing → finalizing → completed`，失败为 `failed`，另有取消/删除状态。主流程会按 `docreader/chunking/embedding/multimodal` 等阶段记录进度。

关键原则：

- 任一必要阶段失败，写入 `failed + error_message`，不能永久停在 `processing`；
- 可重试错误与业务失败要区分；
- 后处理子任务需要有计划数、完成计数和短缺补偿，否则可能永远卡在 finalizing；
- 图片 OCR/caption 失败不应轻易作废已解析的全文；
- 重复解析/重试要考虑重复写块和索引一致性。

mini [pipeline.py](../../python-rag-mini/rag/pipeline.py) 用同步函数复刻 `processing/completed/failed` 与阶段错误信息，便于理解状态语义，但没有 asynq 和后处理编排。

## 6. 常见坑与排查顺序

1. **解析成功但检索不到**：检查是否只有图片链接、是否完成 OCR/向量索引、embedding 是否失败。
2. **答案总缺上下文**：检查块是否太小、标题是否丢失、是否需要父子/相邻扩展。
3. **扫描 PDF 内容乱**：检查逐页路由和 OCR，不要盲目调大 TopK。
4. **换 embedding 后效果差**：检查旧向量是否仍在、维度与模型是否一致。
5. **文档一直处理中**：看阶段记录、worker 队列、后处理计数和错误字段。

## 动手任务与验收

1. 用 mini 对一个含多级标题和 fenced code 的 Markdown 分块，验证代码块不被切断、偏移可映射原文。
2. 上传一个空文件，确认返回 `failed`，错误包含具体阶段，而非永久 `processing`。
3. 画一张父子分块示意图，并说明父块为什么不直接参与向量检索。

**验收**：能解释“解析、分块、向量化、图片富化”各自的输入输出与失败边界。
