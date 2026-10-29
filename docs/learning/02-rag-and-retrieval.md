# 02 · RAG、混合检索与问答链路

## 学习目标

- 用一条公式讲清混合检索与加权 RRF。
- 读懂 WeKnora 的事件驱动插件管线和 mini 的同步对照实现。
- 能讨论召回率、精确率、重排、上下文预算、引用和评估，而不只会“向量搜索 + LLM”。

## 1. RAG 的两个世界

**离线索引**：文档 → 解析 → 分块 → Embedding → 索引。
**在线问答**：问题 → 改写 → 检索 → 融合/重排 → Prompt → 生成 → 引用。

索引用什么 embedding 模型，查询也必须使用兼容的模型和向量空间。换模型通常意味着旧向量失效，需要重建索引。

## 2. 为什么需要向量 + 关键词

| 检索方式 | 擅长 | 短板 |
|---|---|---|
| 向量检索 | 同义改写、语义相近、跨表述 | 专有编号、精确错误码、罕见术语可能不稳定 |
| BM25/全文检索 | 精确术语、型号、代码、数字、关键词 | 不理解“重启服务”与“服务重新启动”的语义关系 |
| 混合检索 | 同时覆盖语义和字面 | 需要融合、阈值、成本和评估设计 |

WeKnora 还支持实体/图谱检索，但它不是每次查询都强行参与；mini 只实现向量与 BM25 两路。

## 3. 加权 RRF：核心公式

主项目：[knowledgebase_search_fusion.go](../../internal/application/service/knowledgebase_search_fusion.go)。
mini：[rag/hybrid.py](../../python-rag-mini/rag/hybrid.py)。

```text
rrf(chunk) = vectorWeight / (k + vectorRank)
           + keywordWeight / (k + keywordRank)

score = rrf / ((vectorWeight + keywordWeight) / (k + 1))
```

默认：`k=60`、向量权重 `0.7`、关键词权重 `0.3`。参数定义见 [retrieval_config.go](../../internal/types/retrieval_config.go)。

### 为什么用排名而不是直接加原始分

cosine 相似度通常在 0～1，BM25 分数无上界且随语料变化，两者量纲不同。RRF 只使用每路内部排名，天然避免 BM25 大分数淹没向量分。WeKnora 又除以理论最大值，使双路第一名得到 1，便于后续统一处理。

### 手算例子

A 向量第 1、关键词第 4：

```text
rrf(A) = 0.7/(60+1) + 0.3/(60+4)
score(A) = rrf(A) / (1/61)
```

如果某一路没有命中，该路贡献为 0。`k` 越大，头部名次之间的差距越平缓。

## 4. WeKnora 的插件管线

[chat_pipeline.go](../../internal/application/service/chat_pipeline/chat_pipeline.go) 的 `Plugin` 接收事件类型、`ChatManage` 和 `next()`，形成责任链。查询阶段大致包括：

```text
加载历史
→ query understand / rewrite / expansion
→ 并行 chunk/entity 检索
→ RRF 融合
→ rerank
→ merge / filter top-k
→ into chat message
→ chat completion stream
```

对应源码：

- [历史加载](../../internal/application/service/chat_pipeline/load_history.go)
- [问题理解](../../internal/application/service/chat_pipeline/query_understand.go)
- [并行检索](../../internal/application/service/chat_pipeline/search_parallel.go)
- [重排](../../internal/application/service/chat_pipeline/rerank.go)
- [上下文组装](../../internal/application/service/chat_pipeline/into_chat_message.go)

**为什么不是固定写死一条链**：事件和插件让不同意图可以跳过检索、让网页/图谱等能力按配置插入，也让错误类型和观测边界更清楚。

## 5. mini 的对应简化

```text
rag/hybrid.py: hybrid_search
→ vector_search + keyword_search
→ fuse_or_deduplicate
→ match_count 截断
→ rag/rerank.py
→ rag/qa.py: answer 或 stream_answer
```

mini 复刻了三条融合路径：

- 仅向量：保留 cosine 分并按 chunk 去重；
- 仅关键词：BM25 除以本列表最高分；
- 混合：加权 RRF 并归一化。

它复刻的是检索算法，不是主项目的全部插件编排、跨库并发、FAQ、MMR 和图谱策略。

## 6. Rerank 与 TopK 不是一回事

- `candidate/top_k`：初召回深度，决定候选池。
- `rerank`：用更精细模型或规则重新评估候选相关性。
- `match_count/rerank_top_k`：最终进入上下文或返回的数量。

候选太少会漏召回，候选太多会增加费用和延迟；最终塞入上下文过多还可能降低答案质量。WeKnora 对请求结果数量设上限，避免大规模计费重排，见 `MaxRequestedResults`。

## 7. 引用为什么需要协议

mini 和 WeKnora 都避免直接信任模型自造来源：

```text
检索结果注册为 c1/c2...
→ Prompt 要求模型输出 <ref id="c1"/>
→ 输出阶段查注册表展开
→ 未知别名 fail-closed 删除
→ 前端展示 [1] 或来源角标
```

主项目：[sources.go](../../internal/modelcontext/sources.go)、[citations.go](../../internal/modelcontext/citations.go)。
mini：[qa.py](../../python-rag-mini/rag/qa.py)、[stream.py](../../python-rag-mini/rag/stream.py)。

## 8. 面试必须会说的评估与兜底

- **检索评估**：固定问题集，记录命中分块、人工相关标记，计算 Recall@K、MRR/nDCG，按术语型/语义型/多跳分类。
- **生成评估**：答案正确性、上下文忠实度、引用准确率、无答案拒答率。
- **系统指标**：各阶段 P50/P95、LLM token 与费用、重排耗时、失败率。
- **失败兜底**：无结果时明确说不知道；模型/重排失败有错误事件；上下文超预算要截断/压缩；流断开后可重放。

不要声称“RRF 一定提高所有指标”，应通过自己语料的 A/B 实验下结论。

## 动手任务与验收

1. 在 mini 把 `DEFAULT_VECTOR_WEIGHT` 从 `0.7` 改为 `0.3`，同一问题跑两次，记录排名变化并恢复。
2. 构造一个含精确错误码的文档和一个语义改写问题，分别比较向量与 BM25 名次。
3. 给一个 `<ref id="c999"/>` 测试解码器，确认未知引用不会展示。

**验收**：能手算 RRF、解释三条融合路径，并说出至少 4 个评估指标及一个失败兜底。
