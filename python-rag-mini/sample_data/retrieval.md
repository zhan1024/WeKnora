# 检索引擎

WeKnora 支持混合检索:向量检索负责语义相似,关键词检索(BM25)负责精确命中术语。
两路结果通过加权 RRF(Reciprocal Rank Fusion)融合,默认参数 k=60,
向量权重 0.7,关键词权重 0.3。融合分数除以理论最大值归一化到 [0,1]。

支持的向量存储引擎包括 PostgreSQL(pgvector)、Qdrant、Milvus、Weaviate、
Elasticsearch、OpenSearch、Doris、腾讯云 VectorDB 等,通过 RETRIEVE_DRIVER
环境变量配置,支持多驱动同时写入。

检索之后可接重排(Rerank)模型对候选分块做精细排序,再把 Top-K 分块
注入大模型上下文生成回答,回答中的引用通过 ref 标签展开为来源角标。
