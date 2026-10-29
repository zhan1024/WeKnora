# 04 · 向量数据库与 Embedding

## 学习目标

- 理解向量、相似度、维度、ANN 索引和阈值之间的关系。
- 看懂 WeKnora 的多引擎抽象与 pgvector 查询。
- 能为 mini 配置真实 embedding，并知道何时换模型必须重建索引。

## 1. 最小原理

Embedding 把文本映射到向量。常用余弦相似度：

```text
cos(a,b) = a·b / (||a|| ||b||)
```

越接近 1 越相似。余弦距离常写为 `1 - cos(a,b)`；pgvector 的 `<=>` 是余弦距离操作符，因此查询按距离升序。

mini 使用 [embeddings.py](../../python-rag-mini/rag/embeddings.py) 中的 `cosine`，真实模型由 `EMBEDDING_*` 环境变量配置；哈希 embedding 仅用于离线演示，不等于语义模型。

## 2. WeKnora 如何屏蔽不同向量引擎

接口契约见 [retriever.go](../../internal/types/interfaces/retriever.go)：`RetrieveEngine` 提供 `EngineType`、`Retrieve`、`Support` 等能力；注册与工厂负责按 KB/store 选择引擎。

实现目录：[repository/retriever](../../internal/application/repository/retriever/postgres/repository.go)，包括 PostgreSQL、Qdrant、Milvus、Weaviate、Elasticsearch、OpenSearch、Doris 等。

**统一的是检索语义，不是 SQL**。各引擎对距离分、BM25 分、过滤、索引和可用功能的支持不同，所以 WeKnora 在引擎边界和融合层做分数归一化。

## 3. pgvector 例子

PostgreSQL 实现见 [postgres/repository.go](../../internal/application/repository/retriever/postgres/repository.go)：

```sql
SELECT chunk_id,
       embedding::halfvec(dim) <=> $1::halfvec(dim) AS distance
FROM embeddings
WHERE ...
ORDER BY embedding::halfvec(dim) <=> $1::halfvec(dim)
LIMIT top_k;
```

代码里对索引表达式、halfvec 转换和 HNSW 参数做了兼容处理。学习时重点不是背 SQL，而是理解：

- 查询向量与存储向量必须维度一致；
- `ORDER BY` 表达式要与索引表达式匹配，才能有效使用对应索引；
- 距离还要转成统一的相似度语义；
- 过滤租户/KB/文档/启用状态与相似度同样重要。

## 4. 精确搜索与 HNSW

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| 全量精确计算 | 实现简单、召回确定 | 数据量大时慢，mini 就是全量内存计算 |
| HNSW 近似索引 | 大规模检索更快 | 构建成本、内存、召回率与参数权衡 |
| BM25/倒排 | 关键词高效 | 与向量索引是不同结构，需融合 |

TopK 不是越大越好：过大会增加重排与上下文成本；ANN 参数和过滤条件也会影响实际召回。

## 5. 选型思路

- **已有 PostgreSQL、规模中小、想减少组件**：pgvector 往往是最简单起点。
- **向量规模大、需要专用向量能力**：评估 Qdrant/Milvus 等专用引擎。
- **已有 ES/OpenSearch，全文检索是主场景**：可复用其 BM25 与生态，再评估向量能力。
- **任何选型都要验证**：中文 analyzer、过滤、备份、维度变更、删除一致性、监控与团队运维能力。

不要因为“支持最多引擎”就判断架构更好；每增加一个引擎都意味着一致性、迁移和测试成本。

## 6. 维度、阈值与重建索引

- 维度由模型决定，bge-m3 常见输出 1024 维；不应把不同模型的向量混在同一检索空间。
- 更换 embedding 模型或维度后，应创建新索引/集合并全量重建，验证后再切换。
- 阈值不是通用常数：不同模型、语料、语言、查询类型需要分别评测。
- 余弦分数高不代表事实正确，检索相关也不代表回答可以脱离引用。

## 7. mini 的真实模型配置

```dotenv
EMBEDDING_API_KEY=<你的密钥>
EMBEDDING_BASE_URL=<OpenAI 兼容端点>
EMBEDDING_MODEL=<模型名>
```

mini 当前以内存数组保存向量，进程重启会重新解析和计算默认语料；它不是持久化向量数据库。要进阶，可把 `HybridStore` 替换为 SQLite + 向量扩展、pgvector 或 Qdrant，同时保留 `vector_search` 的返回契约。

## 动手任务与验收

1. 用同一组“原文/同义改写/无关文本”比较两种 embedding 的相似度与区分度。
2. 把 mini 的向量维度和模型名写入日志，解释为什么只改模型名而不重建索引是错误的。
3. 阅读 pgvector 查询，指出距离排序方向、TopK 和过滤分别在哪体现。

**验收**：能解释 cosine distance 与 similarity 的区别、HNSW 的取舍，以及换模型重建索引的原因。
