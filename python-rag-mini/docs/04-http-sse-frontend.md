# 04 · mini HTTP/SSE 与前端联调

## 1. 服务启动与生命周期

[server.py](../server.py) 使用 FastAPI lifespan：

```text
加载 .env
→ 初始化 embeddings
→ 解析 sample_data
→ 建立内存 HybridStore
→ 开始接收请求
```

若默认语料入库失败，服务启动失败，而不是带着空知识库假装健康。`/health` 可检查 chunks、files 和 embedding 类型。

## 2. 接口对照

| 接口 | 用途 | 注意点 |
|---|---|---|
| `GET /health` | 健康状态 | 不等于所有外部模型长期可用 |
| `POST /api/ingest` | 项目内文档入库 | path 经过项目根目录约束 |
| `POST /api/ask` | 一次性回答 | 返回 answer/retrieved/citations |
| `GET /api/ask/stream` | SSE 流式回答 | query 携带 question/session_id |
| `GET /api/events/...` | 事件重放 | offset 从 0 或上次 next_offset 开始 |

`store_lock` 保护内存索引的检索与入库临界区；它是单进程学习实现，不等价于分布式锁。

## 3. SSE 帧结构

```text
event: chat.stream
data: {"type":"chat.stream","data":{"content":"一段增量文本"}}

```

帧之间是空行。首帧 `meta` 带 message/session，随后 references/start/stream/complete。

`StreamingResponse` 还设置：

- `Cache-Control: no-cache`；
- `X-Accel-Buffering: no`，减少代理缓冲；
- `text/event-stream` 媒体类型。

## 4. 前端为什么用 fetch 而不是 EventSource

[app.js](../web/app.js) 需要灵活处理 query、事件和后续状态，采用 `fetch + ReadableStream`：

```javascript
const response = await fetch(url);
const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const frames = buffer.split('\n\n');
  buffer = frames.pop(); // 最后半帧留到下次
  // 对每个完整帧找 data: 行，再 JSON.parse
}
```

核心知识：TCP chunk、LLM token、SSE event 是三种不同边界，不能假定一次 `read()` 就是一帧。

## 5. 页面状态如何变化

| 事件 | 页面动作 |
|---|---|
| `chat.references` | 更新“已找到 N 段资料”，渲染来源卡片 |
| `chat.start` | 显示生成状态 |
| `chat.stream` | 隐藏加载状态，追加增量文本并滚动 |
| `chat.complete` | 使用最终内容收尾，恢复输入 |

参考资料先于正文到达，因此用户能先看到“回答依据来自哪里”。

## 6. XSS 与渲染

模型输出和文档内容都属于不可信文本。前端先 `escapeHtml`，仅把 `[数字]` 转为固定 `<sup class="citation">`，再把换行转 `<br>`。不要直接把模型返回的 HTML/Markdown 未消毒插入 `innerHTML`。

文档来源卡片同样要转义，因为上传文档可能包含恶意 HTML。

## 7. 静态前端为何不需要 npm

`web/` 是原生 HTML/CSS/JavaScript，FastAPI 通过 `/static` 直接托管，`/` 返回 `index.html`。优点是学习链路短、修改即刷新；缺点是缺少组件化、类型检查、构建优化和大型状态管理。它用于学习，不替代 WeKnora 的 Vue 前端。

## 8. 当前生产化缺口

- 没有用户认证、租户、权限和速率限制；
- 内存索引与事件流重启即失，不能多副本共享；
- ingest 路径虽限制在项目目录，仍应只在可信本地环境使用；
- 无上传大小/并发配额和完整观测；
- 没有停止生成、持久化会话与真实恢复；
- 重复入库的去重/替换策略需要进一步完善。

## 动手验证

1. `curl -N` 调用 stream，保存全部帧并统计类型。
2. 从响应首帧取 message_id，再请求 `/api/events/...?offset=0` 验证可重放。
3. 在浏览器发送问题，确认 references 先出现、正文逐步增加、complete 后输入恢复。
4. 在 `app.js` 故意去掉 `frames.pop()` 的半帧缓存，解释为什么会产生偶发 JSON 解析错误，再恢复。
