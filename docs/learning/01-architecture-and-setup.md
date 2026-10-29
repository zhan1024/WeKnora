# 01 · 架构与从零启动

## 学习目标

- 说出 WeKnora 各进程的职责和通信协议。
- 分清“完整产品启动”“前端开发调试”“mini 学习项目启动”三种方式。
- 能沿两条主链路定位源码：文档入库、知识问答。

## 1. 先看组件，而不是先背目录

| 组件 | 技术 | 核心职责 |
|---|---|---|
| `frontend/` | Vue 3 + Vite + Nginx | 工作空间、知识库、问答、设置；开发时 `/api` 走 Vite 代理 |
| `cmd/` + `internal/` | Go + Gin | 认证、租户、知识库、检索、Agent、会话、MCP Server |
| `docreader/` | Python + gRPC | 多格式文档统一解析为 Markdown、图片与位置元数据 |
| PostgreSQL + pgvector | SQL + 向量扩展 | 业务数据、全文检索、向量索引；也可接其他向量引擎 |
| Redis | RESP | 异步任务、事件流、限流、跨实例状态 |
| 可选 Neo4j | Bolt | 知识图谱与实体关系检索 |
| LLM/Embedding/Rerank 服务 | OpenAI 兼容或厂商 API | 生成、向量化、精排 |

源码入口：[路由](../../internal/router/router.go)、[前端](../../frontend/src/App.vue)、[docreader gRPC 协议](../../docreader/proto/docreader.proto)。

## 2. 进程间通信

```text
浏览器 ──HTTP/SSE──> frontend/Nginx ──HTTP──> Go App
Go App ──gRPC──────> docreader
Go App ──SQL───────> PostgreSQL/pgvector
Go App <─RESP──────> Redis
Go App ──HTTP──────> LLM / Embedding / Rerank / Web Search / MCP 服务
外部 MCP Client ──Streamable HTTP──> Go App /mcp/:endpoint_id
```

**面试关键点**：SSE 是“浏览器到后端”的回答流；gRPC `ReadStream` 是“Go 到 docreader”的解析流，两者解决的问题不同，不要混为一谈。

## 3. 两条核心数据流

### 文档入库

```text
上传文件/URL
→ 创建 Knowledge（pending）
→ 异步任务领取
→ 引擎路由 + docreader 解析
→ Markdown + 图片 + SourceBlock
→ 分块与标题上下文
→ 文本/图片富化
→ Embedding
→ 向量库/关键词索引
→ completed 或 failed + error_message
→ 可选摘要、问题生成、图谱、Wiki 后处理
```

主实现：[knowledge_process.go](../../internal/application/service/knowledge_process.go)。

### 知识问答

```text
用户问题
→ 会话/权限检查
→ 历史加载、意图理解、问题改写
→ 向量 + 关键词（可选实体）召回
→ RRF 融合 → Rerank → 合并/过滤
→ 上下文与来源别名组装
→ LLM 流式生成
→ 引用流解码
→ StreamManager 追加事件
→ SSE 推送；断线后按 offset 重放
```

主实现：[chat_pipeline](../../internal/application/service/chat_pipeline/chat_pipeline.go)、[StreamManager](../../internal/stream/memory_manager.go)。

## 4. 三种启动方式

### A. 完整产品

遵循仓库根目录 README 与 `.env.example`，使用 Docker Compose 启动 app、docreader、frontend、PostgreSQL、Redis 等。首次部署重点检查：

- `.env` 是否存在，必填密钥是否已替换；
- 数据库、Redis、对象存储是否连通；
- 模型配置是否包含 Chat、Embedding，检索是否还需要 Rerank；
- 端口与 `APP_EXTERNAL_URL`、前端代理是否一致。

不要只看容器 `Up`：还应验证注册/登录、模型连接、上传解析和一次问答。

### B. 前端开发调试

```bash
cd frontend
npm ci
# frontend/.env.local 中设置 VITE_DEV_PROXY_TARGET=http://<后端地址>
npm run dev
```

Vite 的 `/api`、`/mcp/`、`/files` 代理定义见 [vite.config.ts](../../frontend/vite.config.ts)。前端 `5173` 只是开发服务器，后端 API 仍由代理目标提供。

### C. mini 学习项目

```bash
cd python-rag-mini
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 127.0.0.1 --port 8000
```

mini 启动时读取 `.env`，把 `sample_data/` 解析入库。它是进程内单实例，不包含主项目的 Postgres、Redis 和异步 worker。

## 5. 分层阅读法

Go 后端大多数请求可以按下面顺序定位：

```text
router（路径与角色门槛）
→ middleware（认证、租户、RBAC、限流）
→ handler（HTTP 参数与响应）
→ application/service（业务编排）
→ application/repository 或 infrastructure（数据库/外部引擎）
→ types/interfaces（契约）
```

**练习**：从 `POST /knowledge-chat/:session_id` 开始，不跳读整个仓库，只追这一条调用链。

## 动手任务与验收

1. 画出上面两条数据流，标出至少 5 个真实文件名。
2. 启动 mini，访问 `/health` 和 `/docs`，再用前端提出一个问题。
3. 在主项目找到一个 SSE 路由和一个 gRPC RPC，分别说明用途。

**验收**：你能回答“上传后为什么不是同步立即返回所有富化结果？”以及“为什么 mini 不需要 Redis 而主项目多副本需要共享事件流？”
