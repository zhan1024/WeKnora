# 08 · mini 项目从零到一

## 学习目标

- 独立启动并解释 mini 的每个模块。
- 看懂“主项目复杂能力”如何被压缩为可实验的小代码。
- 能基于 mini 做一次有验证的扩展，而不是只运行现成 Demo。

## 1. 当前完整结构

```text
python-rag-mini/
├── main.py                 # CLI：ingest / ask / ask --stream
├── server.py               # FastAPI：HTTP、SSE、静态前端、事件重放
├── requirements.txt
├── .env                    # 本地密钥，不提交
├── rag/
│   ├── parsers.py          # 多格式解析与责任链
│   ├── chunker.py          # 标题/面包屑/代码保护/偏移/递归分块
│   ├── pipeline.py         # 入库阶段与状态机
│   ├── tokenizer.py        # 中英文 token 化
│   ├── embeddings.py       # OpenAI 兼容 embedding / 离线哈希
│   ├── hybrid.py           # 向量 + BM25 + 加权 RRF
│   ├── rerank.py           # Noop / LLM 重排
│   ├── qa.py               # 来源注册、引用协议、一次性/流式回答
│   └── stream.py           # 事件流、SSE、ref 跨边界解码
├── web/
│   ├── index.html          # 单页结构
│   ├── styles.css          # 响应式界面
│   └── app.js              # fetch 流式读取、消息与来源渲染
├── sample_data/            # 默认学习语料
└── docs/                   # mini 专项学习文档
```

## 2. 从零启动

```bash
cd /Users/cherry/Documents/学习代码/WeKnora/python-rag-mini
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 若仓库未提供 example，则按 README 手动新建
uvicorn server:app --host 127.0.0.1 --port 8000
```

当前已有 `.env` 时不要覆盖。启动后：

- <http://127.0.0.1:8000/>：前端问答；
- <http://127.0.0.1:8000/docs>：Swagger；
- <http://127.0.0.1:8000/health>：分块与 embedding 状态。

`.gitignore` 必须包含 `.env`、`.venv/`、`__pycache__/`。文档中不要写真实 API Key。

## 3. 三条使用路径

### CLI 入库

```bash
python main.py ingest sample_data
```

展示每个文件的 `completed/failed`、阶段、分块数和 metadata。

### CLI 问答

```bash
python main.py ask "WeKnora 支持哪些向量数据库?"
python main.py ask "混合检索怎么工作?" --stream
```

### HTTP/前端

```bash
curl http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"混合检索怎么工作?","match":3}'

curl -N 'http://127.0.0.1:8000/api/ask/stream?question=混合检索怎么工作&session_id=demo'
```

## 4. 代码调用链

### 服务启动

```text
server.lifespan
→ get_embeddings
→ _ingest("sample_data")
→ ingest_file
→ parse_file / split / embed_documents / store.add
```

### 一次问答

```text
POST /api/ask 或 GET /api/ask/stream
→ _search
→ embeddings.embed_query
→ hybrid_search
→ get_reranker().rerank
→ answer 或 stream_answer
→ StreamManager + CitationStreamExpander（流式）
```

### 前端流式显示

[web/app.js](../../python-rag-mini/web/app.js) 用 `fetch` 获取 `response.body.getReader()`，按 SSE 空行拆帧，处理：

- `chat.references`：先展示参考来源；
- `chat.start`：更新生成状态；
- `chat.stream`：追加回答；
- `chat.complete`：最终内容收尾。

渲染前进行 HTML 转义，引用 `[1]` 只是转为 `<sup>` 样式，不直接注入模型 HTML。

## 5. mini 与主项目的真实边界

| 能力 | mini | 主项目 |
|---|---|---|
| 文档解析 | 5 种格式、同步、简化 PDF 信号 | 多格式、gRPC、多引擎、扫描路由、图片富化 |
| 分块 | 标题/面包屑/代码保护/偏移 | 另有策略链、校验、父子分块等 |
| 检索 | 内存全量向量 + BM25 + RRF | 多引擎、跨 KB、阈值、图谱/FAQ/MMR 等 |
| 生成 | 单轮 QA、DeepSeek/OpenAI 兼容 | RAG 插件管线与完整 Agent |
| 输出 | SSE、内存重放、ref 解码 | 内存/Redis、多副本、停止/续传/合并优化 |
| 权限 | 无完整认证/RBAC，仅绑定本地与路径限制 | 多租户、RBAC、API Key、KB scope |
| 记忆 | 未实现长期记忆 | 多种记忆、确认、冲突、召回与导出 |
| MCP | 未实现 | Client + 内置 Server |

## 6. 最适合做的扩展

按难度递增：

1. 给 `sample_data` 增加一篇文档，观察解析与检索变化。
2. 给 RRF 写参数化单元测试，覆盖单路缺失和双路第一。
3. 把内存向量替换为 pgvector/Qdrant，保留 `ScoredChunk` 返回契约。
4. 增加检索评估脚本，输出 Recall@K 与引用命中率。
5. 用 LangGraph 实现一个最小 ReAct，把 `hybrid_search` 作为工具。
6. 增加用户/tenant 字段和最小权限测试，再讨论完整 RBAC。
7. 用 MCP SDK 暴露只读 `search_knowledge`，并加入 endpoint token 和 KB scope。

每步都应先写验收，不要一次把所有企业级功能堆进 mini。

## 7. 继续阅读

- [mini 核心代码逐行导读](../../python-rag-mini/docs/03-core-code-walkthrough.md)
- [mini HTTP/SSE 与前端](../../python-rag-mini/docs/04-http-sse-frontend.md)
- [mini 面试问答与扩展练习](../../python-rag-mini/docs/05-interview-and-labs.md)
- [主项目核心 API](09-api-cheatsheet.md)
- [面试问答](10-interview-qa.md)

## 动手任务与验收

选择“RRF 单元测试”或“检索评估脚本”完成一个 PR 级别的小改动：

- 代码不超过一个主要模块；
- 有正常、边界和失败用例；
- 附运行命令与结果；
- 能解释改动与主项目哪个机制对应。

**验收**：能独立讲清服务启动、问答和前端流式渲染三条调用链。
