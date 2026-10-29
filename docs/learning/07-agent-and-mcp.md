# 07 · Agent 与 MCP Server

## 学习目标

- 解释 ReAct、Tool Calling、工具结果回写和停止条件。
- 分清 WeKnora 作为 MCP Client 接入外部工具，与作为 MCP Server 对外提供知识能力。
- 读懂内置 MCP Server 的 endpoint 鉴权、工具过滤、KB 范围和无状态 Streamable HTTP。

## 1. Agent 与普通 RAG 问答的区别

普通 RAG 常是一轮“检索 → 生成”；Agent 可以多轮决定是否需要检索、调用什么工具、观察结果后继续。

```text
Think：模型根据系统提示、历史和工具列表推理
→ Act：若返回 tool_calls，校验并执行工具
→ Observe：把工具结果写回消息上下文
→ 再 Think
→ 无工具调用/得到最终答案/达到预算或错误停止
```

主引擎：[internal/agent/engine.go](../../internal/agent/engine.go)，拆分见 `think.go`、`act.go`、`tools/`。还包括审批、HITL、上下文压缩、checkpoint、技能和沙箱。

**停止条件必须明确**：无工具调用、达到最大迭代、超时、上下文/费用预算、用户停止、不可恢复错误。不能依赖模型“自己知道何时停”。

## 2. MCP 到底是什么

MCP（Model Context Protocol）规范了 Host/Client 与 Server 之间的工具、资源等能力发现和调用。典型方法包括：

- `initialize`：协议与能力协商；
- `tools/list`：列出当前主体可见的工具；
- `tools/call`：按名称和参数执行工具；
- 可选 `resources/list`、`resources/read` 等。

传输可以是 stdio，也可以是 Streamable HTTP。MCP 不是“某个模型厂商的 function calling 格式”，它解决的是应用与工具服务之间的标准连接。

## 3. WeKnora 的两个 MCP 方向

### A. 接入外部 MCP Server（Client）

`internal/mcp/` 负责外部服务连接、会话、OAuth、元数据等；Agent 工具层按空间配置和工具启用情况暴露给模型。路由 `/api/v1/mcp-services` 管理集成，见 [routes_infra.go](../../internal/router/routes_infra.go)。

### B. 对外提供 WeKnora 能力（Server）

内置实现：[internal/mcpserver/server.go](../../internal/mcpserver/server.go)。

- 路径：`/mcp/:endpoint_id`；
- 传输：无状态 Streamable HTTP，便于任意副本处理请求；
- 鉴权：endpoint 自己的 Bearer token；
- 授权：endpoint 的工具 allowlist 与 KB scope；
- 工具目录：`list_knowledge_bases`、`search_knowledge`、`grep_chunks`、`list_documents`、`read_document`、`ask`、Wiki 工具，以及可选写入工具。

工具定义见 [tools_retrieve.go](../../internal/mcpserver/tools_retrieve.go)、[tools_ask.go](../../internal/mcpserver/tools_ask.go)、[tools_ingest.go](../../internal/mcpserver/tools_ingest.go)。

> `mcp-server/` 下的独立 Python 包已标记 deprecated，仅为旧部署保留。新学习应以 Go 内置 MCP Server 为准，见 [弃用说明](../../mcp-server/README.md)。

## 4. tools/list 与 tools/call 都要授权

`server.go` 注册完整目录，但通过 `WithToolFilter` 过滤工具列表，并用 `WithToolHandlerMiddleware` 在真正调用时再次检查。原因：

- 客户端看到什么，不等于它有权执行什么；
- 恶意客户端可以绕过 list，直接构造 call；
- 写工具必须在 endpoint 显式开启后才可用。

鉴权见 [mcp_endpoint_auth.go](../../internal/middleware/mcp_endpoint_auth.go)：Bearer token 验证后，注入 endpoint 所属租户、合成机器用户、Viewer 角色与 scope。token 只从 `Authorization` 读取，**拒绝 query string token**，避免进入访问日志。

## 5. KB scope 与最小权限

一个 endpoint 不只是“属于某空间”，还可以限定知识库和工具。后续检索要在 endpoint scope 内执行，而不是拿着机器身份查全库。

设计新 endpoint 时应问：

1. 客户端只需要检索，还是也需要生成回答/写入？
2. 只允许哪些 KB？
3. 是否禁用破坏性工具，token 是否可撤销/轮换？
4. 是否有限流、审计、过期与异常告警？
5. 外部 MCP OAuth 回调、SSRF、凭据存储是否安全？

## 6. MCP 与 REST、RAG 的关系

- REST 是应用 API 风格；MCP 是模型/Agent 工具互操作协议，二者可映射同一业务能力。
- RAG 是检索增强生成方法；`search_knowledge` 可以作为一个 MCP 工具暴露。
- Agent 是循环决策者；MCP Server 不替 Agent 决定下一步，只提供可调用能力。
- Streamable HTTP 与 WeKnora 问答 SSE 都是流式交互，但协议消息与用途不同。

## 7. mini 当前边界

mini 已有 `server.py` HTTP/SSE 接口和前端，**没有实现 MCP Server、Agent ReAct、OAuth、沙箱和工具审批**。学习 MCP 时可以设计一个 `search_knowledge` 工具映射到 `hybrid_search`，但不要把它描述为已完成。

## 动手任务与验收

1. 写一段 ReAct 消息序列：用户问题 → tool_call → tool_result → 最终答案，并标出循环退出条件。
2. 用伪代码实现 endpoint 的 `filterTools` 和 `guardTool`，验证“列表不可见”与“直接调用”都被拒绝。
3. 设计一个只读 endpoint：仅允许一个 KB 的 `search_knowledge`，写出 token、scope、限流和撤销策略。

**验收**：能清楚区分 MCP Client/Server、`tools/list`/`tools/call`，并解释为什么调用时还要二次授权。
