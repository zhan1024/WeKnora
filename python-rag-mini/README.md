# WeKnora RAG 精简对照版(Python + LangChain)

用 Python + LangChain 核心组件,对照实现 WeKnora(Go)RAG 主链路的最小可运行版本,
用于学习对照:**同样的检索流程、同样的融合公式、同样的默认参数**,两边代码可以逐段对看。

## 链路对照表

| 环节 | WeKnora(Go) | 本精简版(Python) |
|---|---|---|
| 文档解析/分块 | `docreader/` + `internal/infrastructure/chunker` | `main.py` 中 `RecursiveCharacterTextSplitter`(chunk 300 / overlap 50) |
| 中文分词 | gojieba | `rag/tokenizer.py`(中文 bigram + 英文按词) |
| Embedding | `internal/models/embedding`(多 provider 远程 API) | `rag/embeddings.py`:有 `OPENAI_API_KEY` 用 OpenAI 兼容 embedding,否则离线哈希 embedding |
| 向量检索 | `internal/application/repository/retriever/*`(pgvector/Qdrant/...) | `rag/hybrid.py` `HybridStore.vector_search`(内存余弦) |
| 关键词检索 | 各引擎 BM25/全文 | `rag/hybrid.py` `keyword_search`(rank_bm25) |
| 混合融合 | `knowledgebase_search_fusion.go` `fuseWithRRF` | `rag/hybrid.py` `fuse_or_deduplicate`,**公式与默认值完全一致** |
| 重排 | `internal/reranking/`(远程 rerank 模型 + 复合打分) | `rag/rerank.py`:Noop(离线)/ LLMReranker(composite = 0.7×rerank + 0.3×base) |
| 文档解析 | `docreader/`(gRPC 服务,责任链组合器,统一 Markdown 产物) | `rag/parsers.py`:FirstParser/PipelineParser 责任链 + md/txt/html/docx/pdf 解析器,详见 [docs/01](docs/01-文档解析管线对照.md) |
| 结构化分块 | `internal/infrastructure/chunker/`(标题层级/面包屑/代码块保护/原文偏移) | `rag/chunker.py`:同名机制全量复刻,详见 [docs/01](docs/01-文档解析管线对照.md) |
| 入库状态机 | `knowledge_process.go`(分阶段 + processing/completed/failed) | `rag/pipeline.py`:`ingest_file`,失败不落 processing,详见 [docs/01](docs/01-文档解析管线对照.md) |
| 引用生成 | `internal/modelcontext/sources.go` + `citations.go`(私有 ref 标签注册/展开) | `rag/qa.py` + `rag/stream.py`:协议一致,模型输出 `<ref id="cN"/>`,展开为 `[N]`,未知别名 fail-closed |
| 流式输出 | `internal/stream/`(append-only + offset 重放)+ SSE | `rag/stream.py`:MemoryStreamManager + CitationStreamExpander 流解码,详见 [docs/02](docs/02-流协议对照.md) |
| 生成回答 | Agent ReAct 引擎 `internal/agent/engine.go` | `rag/qa.py` 单轮 QA(无 Agent 循环,见「精简掉了什么」) |

## RRF 融合公式(两边一致)

```
score = vectorWeight/(k + vectorRank) + keywordWeight/(k + keywordRank)
归一化:score / ((vectorWeight + keywordWeight) / (k + 1))   → [0, 1]
默认:k=60,vectorWeight=0.7,keywordWeight=0.3
```

单路退化逻辑也一致:仅向量保留 cosine 分;仅关键词 BM25 除以列表最高分归一。

## 运行

```bash
cd python-rag-mini
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 文档解析管线(解析->分块->向量化->入库,带状态机)
python main.py ingest sample_data

# 离线问答(一次性)
python main.py ask "WeKnora 支持哪些向量数据库?"

# 流式问答(SSE 事件流 + 引用跨边界重组演示 + 断线重放)
python main.py ask "RRF 融合是怎么工作的?" --stream

# 完整模式(真实 LLM 回答 + LLM 重排 + 真实流式)
pip install langchain-openai

# 方式一:环境变量
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.deepseek.com/v1   # 任意 OpenAI 兼容端点
export CHAT_MODEL=deepseek-chat

# 方式二:项目根目录 .env 文件(main.py 启动时自动加载),参考 .env 注释
# 注意:DeepSeek 没有 embeddings API —— 检测到 DeepSeek 端点时向量路
# 自动回退离线哈希;要真实语义向量,另配独立 embedding 服务。

python main.py ask "Agent 的沙箱有哪些后端?"
```

### 当前 .env 配置状态

| 用途 | 服务 | 模型 | 状态 |
|---|---|---|---|
| 聊天 + 重排 | DeepSeek | deepseek-chat | ✅ 已启用 |
| 向量 embedding | 硅基流动 | BAAI/bge-m3(1024 维) | ✅ 已启用 |
| 向量 embedding | 阿里百炼 | text-embedding-v4 | ⏸ 已写入但注释,账户欠费恢复后启用 |

真实 embedding 效果实测(bge-m3 vs 离线哈希):
- 同义改写「把团队分散的文档汇集起来」↔「怎么把团队里散落的资料集中起来管理」:
  余弦相似度 **0.80**(bge-m3) vs 0.34(哈希)
- 无关文本相似度 0.35 vs 0.18 —— 真实向量的语义区分度约为哈希的 3 倍

## 学习文档

- [docs/00-学习路径](docs/00-学习路径.md):推荐阅读顺序与实验方法
- [docs/01-文档解析管线对照](docs/01-文档解析管线对照.md)
- [docs/02-流协议对照](docs/02-流协议对照.md)
- [docs/03-核心代码逐段导读](docs/03-core-code-walkthrough.md)
- [docs/04-HTTP/SSE 与前端联调](docs/04-http-sse-frontend.md)
- [docs/05-mini 面试问答与扩展练习](docs/05-interview-and-labs.md)
- [主项目从零到一学习与面试手册](../docs/learning/README.md):多租户、记忆、MCP、核心 API 与项目复盘

## 精简掉了什么(WeKnora 有而这里没有)

- **Agent ReAct 循环、工具系统、HITL 审批、沙箱** —— 属于 Agent 层,可用 LangGraph StateGraph 向上扩展
- **多租户/权限、多 KB 并发 fan-out、多 store 引擎** —— 产品化能力
- **上下文压缩、token 估算校准、checkpoint 中断恢复** —— Agent 引擎能力
- **FAQ 优先、知识图谱检索、MMR 多样性、父子分块、Wiki 加权** —— 检索增强策略
- **PDF 扫描件 OCR、图片多模态、gRPC 服务边界、Redis 流存储** —— 产品化能力
- **持久化(pgvector 等)** —— 这里是内存索引,重启重建

这些正是 LangChain/LangGraph 生态里可以再往上加的部分:比如用 LangGraph 的
StateGraph 把这个单轮 QA 扩展成带工具调用的 ReAct Agent。

## 启动 HTTP/SSE 服务

```bash
cd /Users/cherry/Documents/学习代码/WeKnora/python-rag-mini
source .venv/bin/activate
uvicorn server:app --host 127.0.0.1 --port 8000
```

启动时会自动加载 `.env`，并将 `sample_data/` 中的默认语料解析、分块、向量化后入库。

- 前端问答界面：<http://127.0.0.1:8000/>
- 在线接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- 一次性问答：`POST /api/ask`
- SSE 流式问答：`GET /api/ask/stream?question=你的问题`
- 文档入库：`POST /api/ingest`，请求体 `{"path":"sample_data"}`（仅限项目目录内）
- 事件重放：`GET /api/events/{session_id}/{message_id}?offset=0`

一次性问答示例：

```bash
curl http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"WeKnora 支持哪些向量数据库?","match":3}'
```

SSE 示例：

```bash
curl -N 'http://127.0.0.1:8000/api/ask/stream?question=混合检索怎么工作&session_id=demo'
```

## 前端界面

`web/` 是零构建依赖的原生 HTML/CSS/JavaScript 单页应用，由 FastAPI 直接托管：

- 流式显示 DeepSeek 回答，引用编号实时展开
- 展示命中文档、相关度和原文片段
- 查看知识库文档与分块数量、重新解析示例语料
- 支持快捷问题、新建对话、Enter 发送和移动端布局

修改前端文件后刷新浏览器即可，不需要 npm 安装或前端构建。
