# 09 · 核心 API 与数据模型速查

> 主项目业务 API 通常以 `/api/v1` 为前缀；内置 MCP 为 `/mcp/:endpoint_id`。实际请求字段以 [Swagger](../swagger.json) 和 handler 为准，本文重点记“用途、鉴权和数据流”，不要凭记忆硬写请求体。

## 1. 常用请求头

```http
Authorization: Bearer <JWT 或 endpoint token>
X-Tenant-ID: <目标空间 ID>
Content-Type: application/json
Accept: text/event-stream
```

- `Authorization`：身份或 endpoint 凭证；
- `X-Tenant-ID`：空间切换/平台 Key 的目标空间，服务端必须校验成员与权限；
- API Key 还可能使用项目约定的 Key 请求头，见 [auth.go](../../internal/middleware/auth.go)；
- MCP token 不支持放在 query string，防止进入日志。

## 2. 认证、空间与成员

| 方法/路径 | 用途 | 学习重点 |
|---|---|---|
| `POST /auth/register` | 注册 | 初始空间策略 |
| `POST /auth/login` | 登录 | JWT、active tenant、memberships |
| `GET /tenants` | 我的空间列表 | 同一用户多角色 |
| `GET/POST /tenants/:id/members` | 成员列表/添加 | 角色与资源权限不同 |
| `POST /tenants/:id/invitations` | 创建邀请 | Owner 权限、受邀人接受 |
| `GET /me/invitations` | 我的邀请 | 只能处理自己的邀请 |
| `POST /me/invitations/:inv_id/accept` | 接受邀请 | membership 变 active |
| `GET /tenants/:id/audit-log` | 空间审计 | Admin+、拒绝行为留痕 |

源码：[routes_auth_tenant.go](../../internal/router/routes_auth_tenant.go)。

## 3. 知识库与检索

| 方法/路径 | 用途 |
|---|---|
| `POST /knowledge-bases` | 创建知识库 |
| `GET /knowledge-bases/:id` | 知识库详情与配置 |
| `POST /knowledge-bases/:id/knowledge/file` | 从文件创建知识 |
| `POST /knowledge-bases/:id/knowledge/url` | 从 URL 创建知识 |
| `POST /knowledge-bases/:id/knowledge/manual` | 创建手工 Markdown 知识 |
| `GET /knowledge/search` | 跨知识记录搜索 |
| `POST /knowledge-bases/:id/hybrid-search` | 指定 KB 混合检索 |
| `POST /knowledge-search` | 不依赖会话的知识检索 |
| `POST /knowledge-bases/:id/graph-search` | 图谱检索 |

源码：[routes_knowledge.go](../../internal/router/routes_knowledge.go)、[routes_chat.go](../../internal/router/routes_chat.go)。

检索请求重点字段：`query`、`match_count/top_k`、KB/文档范围、阈值。响应重点：`chunk_id`、`knowledge_id`、score、match type、原文位置。不要只关注 content，还要追踪权限过滤与 score 语义。

## 4. 会话与流式问答

| 方法/路径 | 用途 |
|---|---|
| `POST /sessions` | 创建会话 |
| `GET /sessions/:id` | 会话详情 |
| `POST /knowledge-chat/:session_id` | 知识库 RAG 问答，SSE |
| `POST /agent-chat/:session_id` | Agent 问答，SSE |
| `POST /sessions/:session_id/stop` | 停止生成 |
| `GET /sessions/continue-stream/:session_id` | 断线后继续/重放事件流 |
| `POST /sessions/:session_id/fork` | 会话分叉 |
| `POST /sessions/:session_id/steer` | 生成中追加引导消息 |

源码：[routes_chat.go](../../internal/router/routes_chat.go)。

SSE 阅读顺序：先识别事件类型，再看 `message_id/request_id`，最后看增量内容和完成事件。重放时关注 offset 与重复帧合并，不要把每个 token 当成独立数据库消息。

## 5. 长期记忆

| 方法/路径 | 用途 |
|---|---|
| `GET/PUT /memory/settings` | 读取/更新个人记忆设置 |
| `GET/POST /memory/items` | 列出/创建记忆 |
| `PUT/DELETE /memory/items/:id` | 编辑/删除指定记忆 |
| `POST /memory/items/:id/confirm` | 确认待确认记忆 |
| `POST /memory/items/:id/reject` | 拒绝推断记忆 |
| `GET /memory/topics` | 记忆主题 |
| `GET /memory/documents` | 记忆相关文档 |
| `GET /memory/export` | 导出 |
| `POST /memory/consolidate` | 整理/合并记忆 |

这些接口从当前 principal 推导记忆主体，不提供任意 subject 参数；scoped integration key 不应访问个人记忆。源码：[routes_memory.go](../../internal/router/routes_memory.go)。

## 6. MCP 管理与调用

管理 API（`/api/v1` 前缀）：

| 路径 | 用途 |
|---|---|
| `/mcp-services` | 外部 MCP 服务集成管理 |
| `/mcp-services/:id/tools` | 获取外部工具 |
| `/mcp-services/:id/tool-approvals` | 工具审批配置 |
| `/mcp-services/:id/oauth/*` | OAuth 授权流程 |
| `/mcp-endpoints` | 内置 MCP Server 的端点管理（以 routes_mcp_endpoint.go 为准） |

内置 MCP 调用：

```http
POST /mcp/<endpoint_id>
Authorization: Bearer <endpoint-token>
Content-Type: application/json
```

逻辑方法：`initialize`、`tools/list`、`tools/call`。HTTP 200 不代表工具业务一定成功，还要检查 MCP result/error。源码：[routes_mcp_endpoint.go](../../internal/router/routes_mcp_endpoint.go)、[mcpserver/server.go](../../internal/mcpserver/server.go)。

## 7. mini API

mini 无 `/api/v1` 前缀，绑定本地 `127.0.0.1:8000`：

| 方法/路径 | 请求 | 响应 |
|---|---|---|
| `GET /health` | 无 | 状态、chunks、files、embedding |
| `POST /api/ingest` | `{"path":"sample_data"}` | 每文件任务状态、总分块数 |
| `POST /api/ask` | `{"question":"...","match":3}` | answer、retrieved、citations |
| `GET /api/ask/stream?question=...&session_id=demo` | query 参数 | SSE：meta/references/start/stream/complete |
| `GET /api/events/{session}/{message}?offset=0` | 路径与 offset | 事件列表、next_offset |

源码：[server.py](../../python-rag-mini/server.py)。`path` 只允许项目目录内；它是本地学习接口，不应未加认证直接暴露公网。

## 8. 前端与联调高频点

- 原生 `EventSource` 适合简单 GET SSE；自定义请求头、POST 或更灵活控制通常用 `fetch + ReadableStream`。
- SSE 按空行拆事件，不要假设一次 `read()` 正好是一条完整事件。
- `data:` 可能是多行，JSON 之外还有事件类型；按协议处理，不要直接 `JSON.parse` 整个响应。
- 断线重连需要保存 session/message/offset，服务端也要保留事件。
- Nginx 代理 SSE 要关闭不必要缓冲；mini 设置 `X-Accel-Buffering: no`。
- 渲染模型文本先转义；不要为了 Markdown 样式直接把模型 HTML 注入 `innerHTML`。
