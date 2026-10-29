# 06 · 记忆、会话与上下文管理

## 学习目标

- 区分短期对话历史、长期记忆、RAG 知识、上下文压缩和事件流。
- 理解 WeKnora 的长期记忆分类、确认、召回与冲突治理。
- 能解释为什么“把所有历史都塞进 Prompt”不是可靠方案。

## 1. 五种容易混淆的数据

| 数据 | 生命周期/用途 | 典型存储 |
|---|---|---|
| 会话历史 | 当前会话的近期问答，维持多轮对话 | messages/session 数据库 |
| 长期记忆 | 跨会话的用户偏好、资料、事实、任务、兴趣 | memory subject/items 等 |
| RAG 知识 | 企业文档，不等于用户个人记忆 | chunks + 向量/关键词索引 |
| 上下文 checkpoint/压缩 | 控制 Agent 长上下文，保留可继续执行的状态 | checkpoint/compaction |
| StreamManager 事件 | 一次回答的增量输出与断线重放，不是语义记忆 | 内存或 Redis |

**一句话**：记忆回答“以后还应知道什么”，事件流回答“这次回答已经发到哪里”。

## 2. 短期历史如何进入 Prompt

[load_history.go](../../internal/application/service/chat_pipeline/load_history.go) 负责近期消息加载；[common.go](../../internal/application/service/chat_pipeline/common.go) 中历史按问答对处理、剥离旧 think 标签，再拼入当前消息。

Agent 引擎则每轮从数据库重建历史，见 [engine.go](../../internal/agent/engine.go) 与 `service.LoadAgentHistory`。引擎不必在自己的内存里缓存所有跨轮状态。

还要考虑：

- 最大轮数与 token 预算；
- 旧消息里的过时引用协议不能直接重放；
- 多轮指代可通过问题改写改善检索；
- 上下文溢出需要 compaction，而不是无限截断 system prompt。

## 3. 长期记忆的类型与来源

[types/memory.go](../../internal/types/memory.go) 定义：

- 类型：`profile`、`preference`、`fact`、`task`、`interest`；
- 来源：`explicit`（用户明确要求记住）、`extracted`（后台提炼）、`manual`（用户手动维护）；
- 状态：`active`、`pending`、`superseded`、`archived`。

**`pending` 很重要**：系统推断的记忆等待用户确认，不注入 Prompt。否则一次错误猜测可能长期污染所有回答，破坏用户信任。

## 4. 常驻记忆与按需召回

稳定特征（profile/preference/interest）可形成常驻块；事实和任务更偏向按当前问题匹配召回。显式记忆也有特殊常驻资格，避免用户明确说“记住”后却因为词面不相似而永远想不起来。

预算不是无限：

- 常驻块约 900 runes；
- 每轮召回块约 600 runes，最多 5 条；
- 主动 `search_memory` 可使用更大的独立预算；
- 单条记忆应短，长文应进入知识库而非伪装成记忆。

检索 pipeline 中还有 [memory_recall.go](../../internal/application/service/chat_pipeline/memory_recall.go) 与 `memory_affinity.go`，Agent 可按需调用 [search_memory.go](../../internal/agent/tools/search_memory.go)。

## 5. 冲突、遗忘与用户控制

- 新事实与旧事实冲突时，旧项可 `superseded`，而不是无痕删除，便于解释“何时改变”。
- 数量超上限时可归档低排名项；过期项在查询层排除。
- 用户可以查看、创建、编辑、确认、拒绝、删除、导出与整理记忆。
- API 路径不包含 subject 参数，记忆空间由当前调用者身份推导，避免读取他人记忆，见 [routes_memory.go](../../internal/router/routes_memory.go)。

**面试高分点**：长期记忆不是单纯“向量库 + 相似度搜索”，还需要写入策略、确认、冲突、过期、预算、隐私和可解释性。

## 6. Agent 上下文压缩与 checkpoint

[compaction](../../internal/agent/compaction) 负责旧历史摘要/压缩；[checkpoint.go](../../internal/agent/checkpoint.go) 与 token 估算/校准配合，避免上下文窗口溢出。

要区分：

- 压缩是当前任务可继续执行的工程手段；
- 长期记忆是跨会话语义信息；
- 压缩后可能丢细节，因此引用、工具结果和未完成状态需要特别保护。

## 7. 事件流不是记忆

[memory_manager.go](../../internal/stream/memory_manager.go) 名称里有 memory，但它表示**内存实现的事件流**。`AppendEvent` 追加、`GetEvents(fromOffset)` 按偏移读取，支撑断线重放。Redis 实现用于多副本共享，不应拿它当长期对话记忆。

## 8. 常见风险

- 记忆污染：错误推断被永久注入；
- 隐私越权：集成 API Key 或他租户用户读取个人记忆；
- 回忆过度：每轮塞太多个人事实，影响回答且增加成本；
- 旧事实复活：冲突项没有 supersede/过期策略；
- 把文档摘要当记忆：内容过长，应进入知识库；
- 把断线重放当历史加载：前端看到完整回答不代表 Prompt 历史已正确恢复。

## 动手任务与验收

1. 设计“我喜欢简短回答”与“以后请详细解释”的冲突处理，写出两项状态和最终 Prompt 应包含哪项。
2. 为一条系统推断记忆设计 pending → confirm/reject 流程，验证 pending 不进入 Prompt。
3. 用一张表向同学解释短期历史、长期记忆、RAG、事件流的差异。

**验收**：能说出至少 5 个长期记忆治理点，并解释为什么需要用户确认与上下文预算。
