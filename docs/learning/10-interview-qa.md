# 10 · 面试官问、面试者答

> 使用方法：先遮住回答，按“结论 → 机制 → 实现 → 边界 → 项目例子”口述，再对照补充。答案是学习参考，不建议逐字背诵。

## A. RAG、检索与评估

### Q1：什么是 RAG？它解决了什么问题？

**答**：RAG 是在生成前从外部知识源检索相关内容，把内容作为上下文提供给模型，从而降低知识过时和无依据编造的概率，并能给出来源。完整工程链路包括文档解析、分块、索引、查询改写、召回、融合重排、上下文组装、生成和引用，不是简单“向量库 + LLM”。它仍可能因解析差、漏召回、上下文噪声或模型不遵循约束而答错，所以需要评估和拒答机制。

源码：[chat_pipeline](../../internal/application/service/chat_pipeline/chat_pipeline.go)。

### Q2：为什么向量检索之外还要 BM25？

**答**：向量检索擅长语义改写，BM25 擅长错误码、型号、专有名詞等精确字面匹配，两者互补。例如“怎么重启服务”可靠语义召回，但“ERR_1203”更应依赖关键词。WeKnora 将两路结果按 RRF 融合，mini 的 `hybrid.py` 也复刻了这一机制。是否提升效果要在自己的语料上评估，不能绝对化。

### Q3：RRF 是什么？为什么不直接相加两路分数？

**答**：RRF 按文档在各结果列表中的名次计算倒数分，而不是直接比较 cosine 和 BM25 原始分。BM25 无上界且随语料变化，与 0～1 的相似度量纲不同，直接相加会失衡。WeKnora 使用 `vW/(k+vRank)+kW/(k+kRank)`，默认 k=60、权重 0.7/0.3，并除以理论最大值归一化到 0～1。

源码：[Go 融合](../../internal/application/service/knowledgebase_search_fusion.go)、[Python 融合](../../python-rag-mini/rag/hybrid.py)。

### Q4：RRF 中的 k 和权重怎么调？

**答**：k 控制排名差距的平滑程度，k 越大，头部与后续名次的分数差异越平；权重决定向量与关键词两路的相对影响。术语密集语料可提高关键词权重，语义改写多则倾向向量。调参必须固定问题集，比较 Recall@K、MRR/nDCG、延迟和重排成本，而不是凭单次回答主观判断。

### Q5：分块大小怎么选？是不是越小越准？

**答**：小块更利于精准定位，但可能缺少上下文；大块上下文完整，却降低精度并增加 token。应根据文档结构、问题类型和模型窗口实验。WeKnora 还使用标题层级、面包屑、代码块保护、原文偏移和父子分块，说明“块大小”不是唯一变量。好的做法是用检索命中和答案忠实度共同评估。

### Q6：父子分块为什么有效？

**答**：子块用于精准向量召回，命中后回捞父块或扩展上下文，兼顾“找得准”和“看得全”。父块只入业务库，不必直接进入向量索引。代价是数据模型、扩展合并和去重更复杂，mini 当前未实现这一部分。

源码：[processChunks](../../internal/application/service/knowledge_process.go)。

### Q7：为什么文档解析产物统一成 Markdown？

**答**：它把 PDF、DOCX、HTML 等格式差异隔离在解析层，下游分块、检索和引用只处理统一结构。WeKnora 的 docreader 还返回图片、metadata 和 SourceBlock，因此不是只抽纯文本。统一中间表示降低耦合，但解析错误也会被下游忠实继承，所以引擎质量仍关键。

### Q8：docreader 为什么还需要 ReadStream？

**答**：大扫描 PDF 可能包含数百页图片，一次性 unary 返回容易触及 gRPC 消息上限并推高双方内存。ReadStream 先发一次 Markdown/meta，再每图一帧，控制单帧大小并及时释放图片数据。它与前端问答 SSE 是不同服务边界的两种流，用途不同。

源码：[docreader.proto](../../docreader/proto/docreader.proto)。

### Q9：Embedding 模型能随便换吗？

**答**：不能。不同模型的语义空间、维度和分数分布不同，查询与文档必须使用兼容空间。更换模型或维度通常要新建索引并全量重建，验证检索指标后切换，不能只在配置里改模型名继续使用旧向量。还要考虑中文效果、成本、长度限制、部署和隐私。

### Q10：pgvector 的 `<=>` 是什么？为什么升序？

**答**：它是余弦距离操作符，距离越小越相似，因此 `ORDER BY ... <=> query ASC`。它与余弦相似度不同，不能直接把距离当相似度阈值。项目还要关心索引表达式匹配、向量维度、过滤条件和 HNSW 参数。

源码：[postgres/repository.go](../../internal/application/repository/retriever/postgres/repository.go)。

### Q11：HNSW 与全量精确向量搜索如何取舍？

**答**：全量精确搜索结果确定、实现简单，适合小规模或 mini；HNSW 用图索引换取大规模下的查询速度，但属于近似搜索，召回率受参数、过滤和数据分布影响。生产选择要同时看 QPS、P95、内存、构建/更新成本和 Recall@K，而不是只看“支持 HNSW”。

### Q12：如何选择向量数据库？

**答**：先看现有基础设施和规模，再看中文全文能力、过滤、索引、更新删除、一致性、备份和团队运维能力。已有 PostgreSQL 且规模适中可优先 pgvector；向量规模很大可评估 Qdrant/Milvus；已有 ES 生态则可评估其 BM25 + 向量组合。WeKnora 多引擎接口减少迁移成本，但每多支持一个引擎仍有一致性和测试成本。

### Q13：Rerank 为什么有用？是否每次都必须调用？

**答**：初召回追求覆盖，rerank 用更细粒度模型对少量候选重新排序，通常能提高进入上下文结果的相关性。它增加延迟和费用，并非所有查询都必须执行；简单 FAQ、高置信精确命中或低成本场景可跳过/降级。候选数与最终 TopK 要分开控制，并设置超时和失败策略。

### Q14：怎样防止模型编造引用？

**答**：检索结果由服务端注册别名，模型只能输出类似 `<ref id="c1"/>` 的引用，输出阶段再按注册表展开，未知别名直接删除。还要限制模型基于给定资料回答、无依据时拒答，并评估引用是否真正支持对应句子。引用存在不等于引用内容一定正确，仍需忠实度评估。

源码：[citations.go](../../internal/modelcontext/citations.go)。

### Q15：如何评估一个 RAG 系统？

**答**：分三层：检索看 Recall@K、MRR/nDCG、分查询类型命中；生成看正确性、上下文忠实度、引用准确和拒答；系统看各阶段延迟、token/费用、失败率。需要固定带人工相关标注的问题集，按术语型、语义型、多跳、无答案分类。单次 Demo 成功不能证明系统质量。

## B. 多租户与权限

### Q16：WeKnora 的多租户模型是什么？

**答**：用户与租户是多对多，通过 `tenant_members` 记录关联、角色和状态。同一用户可在不同租户拥有不同角色，资源则通过 tenant_id、creator、KB 授权等隔离。认证后还要解析目标租户和该空间下的角色，再进入路由与资源级检查。

源码：[tenant_member.go](../../internal/types/tenant_member.go)。

### Q17：owner/admin/contributor/viewer 有什么区别？

**答**：owner 管完整生命周期与所有权，admin 管成员和空间配置，contributor 可创建资源并按属主规则编辑，viewer 主要只读。角色等级只是第一层，具体资源还要考虑创建者、KB 授权和组织共享。不能把 admin 理解为“系统里什么都可以做”。

### Q18：`X-Tenant-ID` 是否可以直接信任？

**答**：不能。它只是客户端表达目标空间的输入，服务端必须验证身份、目标空间可访问性、active membership，再注入该空间的 TenantRole 和资源上下文。若只按请求头过滤数据而不验证成员，就会形成典型横向越权。

源码：[auth.go](../../internal/middleware/auth.go)。

### Q19：为什么路由 RBAC 通过后还要资源级检查？

**答**：RBAC 只说明某角色能执行某类动作，不说明能操作哪一条资源。例如 contributor 可编辑自己创建的 KB，但不能因此编辑他人的 KB。还需属主、KB grant、组织共享、API Key scope 和 repository 租户过滤，形成纵深防御。

### Q20：API Key scope 与用户角色有何区别？

**答**：角色描述用户在空间中的职责，API Key scope 描述集成凭证被允许的能力和资源范围。一个高权限用户签发的 Key 也应可限制为只读检索或指定 KB，避免凭证泄露后拥有完整用户能力。个人记忆这类资源还应禁止 scoped Key 继承访问。

### Q21：权限测试最重要的场景是什么？

**答**：重点是横向越权：A 租户用户伪造租户头、KB ID、会话 ID、文件路径或 MCP endpoint 去访问 B 租户资源。还要覆盖 invited/suspended、viewer 写操作、contributor 操作他人资源、scope 外 API Key 和已撤销 token。测试不仅看 403/404，还要验证数据库、文件和向量过滤都生效。

## C. 记忆与上下文

### Q22：短期历史与长期记忆有什么区别？

**答**：短期历史服务当前会话多轮上下文，长期记忆保存跨会话仍有价值的用户资料、偏好、事实、任务和兴趣。短期历史通常按轮数/token 截断，长期记忆需要写入、确认、冲突、召回和隐私治理。二者进入 Prompt 的预算和时机也不同。

源码：[memory.go](../../internal/types/memory.go)。

### Q23：为什么系统推断的记忆要 pending？

**答**：推断可能错误，直接永久注入会污染之后所有回答。pending 让用户在记忆管理中确认或拒绝，未确认前不进入 Prompt，提升可控性和信任。用户明确要求记住的 explicit 与后台 extracted 也应有不同写入策略。

### Q24：用户偏好发生变化时怎么处理旧记忆？

**答**：新记忆生效，旧记忆可标记 superseded，并保留变化历史用于解释和审计，而非简单新增两条互相矛盾的 active 项。还应有过期、归档、去重和数量上限。最终 Prompt 只使用当前有效且符合召回条件的记忆。

### Q25：长期记忆是否等于向量检索？

**答**：不是。向量检索只是一种按需召回手段，长期记忆还包括来源、状态、用户确认、冲突取代、常驻/按需分类、预算、隐私和导出。profile/preference 可常驻，fact/task 更适合按查询召回；显式记忆也可能需要特殊常驻规则。

### Q26：上下文越来越长怎么办？

**答**：短期问答可限制历史轮数，Agent 则需要 token 估算、压缩旧历史、保留关键工具状态，并可用 checkpoint 支持后续轮次。压缩可能丢细节，因此要保护引用、未完成任务和关键约束。不能把长期记忆、历史压缩和事件流混为一个存储。

源码：[compaction](../../internal/agent/compaction)、[checkpoint.go](../../internal/agent/checkpoint.go)。

## D. Agent 与 MCP

### Q27：ReAct 循环是什么？

**答**：模型思考后选择工具，系统执行并把结果写回上下文，再继续思考，直到最终答案或停止条件。工程实现需要工具 schema、参数校验、并行/顺序控制、错误回写、最大轮数、超时和费用控制。WeKnora 在 `internal/agent` 中自研该循环，mini 目前是单轮 RAG，并未实现 ReAct。

### Q28：MCP 与 Function Calling 是一回事吗？

**答**：不是。Function Calling 通常是模型输出结构化工具调用的能力；MCP 是应用与工具服务之间发现、描述和调用能力的协议，并有 Client/Server、传输和鉴权等概念。MCP 工具最终可以参与 Function Calling，但两者所在层级不同。

### Q29：为什么 MCP 的 tools/list 和 tools/call 都要鉴权？

**答**：list 过滤只控制客户端“看见什么”，恶意客户端仍能直接构造 call 请求。call 中间件必须在执行时再次检查工具 allowlist、KB scope 和写权限，才能真正防止越权。默认不开放破坏性工具也是最小权限原则。

源码：[server.go](../../internal/mcpserver/server.go)。

### Q30：WeKnora MCP Server 的传输与鉴权怎么做？

**答**：内置 Server 使用无状态 Streamable HTTP，路径为 `/mcp/:endpoint_id`，每个 endpoint 有 Bearer token、工具 allowlist 和 KB scope。鉴权后注入租户与合成机器主体，便于下游继续执行统一权限检查。独立 Python `mcp-server/` 已 deprecated，新设计应看 Go 内置实现。

### Q31：MCP token 为什么不能放 query string？

**答**：URL 容易进入访问日志、代理日志、浏览器历史或监控系统，导致凭证泄露。WeKnora 只从 Authorization Bearer 读取 MCP token。还需要支持撤销/轮换、限流、审计和最小 KB 范围。

## E. SSE、前端与 mini

### Q32：SSE 断线为什么还能续传？

**答**：生成端不直接依赖某一条 HTTP 连接，而是把事件追加到 StreamManager；客户端连接只按 offset 拉取。断线后可以从历史 offset 重放并继续接收新事件，多副本时用 Redis 共享。若事件只存在当前响应局部变量里，就无法做到这一点。

源码：[memory_manager.go](../../internal/stream/memory_manager.go)。

### Q33：为什么引用标签需要流式解码器？

**答**：模型 token 流可能把 `<ref id="c1"/>` 拆在两个网络片段之间，直接推送会让前端看到残缺标签。解码器用 pending 暂存疑似标签，完整后查注册表展开，未知别名删除，普通小于号则放行。它同时解决协议完整性和引用可信性。

mini：[stream.py](../../python-rag-mini/rag/stream.py)。

### Q34：EventSource 与 fetch 流式读取怎么选？

**答**：EventSource 适合标准 GET SSE、自动重连等简单场景；需要 POST、自定义请求头、精细控制取消和读取过程时，常用 fetch + ReadableStream。无论哪种都不能假设一次 read 正好一个事件，需要按 SSE 空行与 data 行规则解析。

mini：[web/app.js](../../python-rag-mini/web/app.js)。

### Q35：mini 的核心架构是什么？

**答**：解析与分块形成文档入库管线；向量与 BM25 双路检索后经 RRF、重排进入问答；qa 注册来源并生成引用；stream 负责事件与引用解码；FastAPI 提供 HTTP/SSE；原生 JS 前端按帧渲染。真实服务使用 bge-m3 embedding 与 DeepSeek chat，哈希 embedding 只是离线兜底。

### Q36：mini 与 WeKnora 还有哪些差距？

**答**：主要缺少完整多租户/RBAC、异步 worker、持久化多向量引擎、父子分块、图片 OCR/VLM、图谱/FAQ/MMR、ReAct Agent、长期记忆和 MCP。它是用于理解核心算法和协议的教学项目，不应包装成完整企业级产品。

### Q37：mini 的 `POST /api/ask` 与 `GET /api/ask/stream` 分别做什么？

**答**：前者同步完成检索、重排和一次性回答，返回 answer、retrieved 与 citations；后者先检索，再以 SSE 发出 references/start/stream/complete 事件，并把事件保存到 MemoryStreamManager。事件接口 `/api/events/...` 可按 offset 重放。mini 服务只适合本地学习，公网部署还需认证和更完整治理。

源码：[server.py](../../python-rag-mini/server.py)。

## F. 生产工程与项目复盘

### Q38：AI 项目怎么做安全治理？

**答**：认证与租户隔离、资源级授权、API Key/MCP 最小 scope、SSRF 防护、凭据加密、沙箱与工具审批是基础；模型侧还要处理 Prompt 注入、不可信文档边界、引用校验和敏感信息日志。工具执行与文件访问不能只靠模型承诺安全，必须由代码强制。

### Q39：如何控制 RAG 的成本与延迟？

**答**：分阶段测量解析、检索、重排和生成耗时；控制候选池与最终上下文，按需跳过检索/重排，使用缓存、批量 embedding、合理并发与超时。还要看 token 单价、重排调用量和失败重试成本。优化应以 P95、Recall@K 和答案忠实度的联合结果为依据，不能为省钱牺牲质量。

### Q40：请介绍你的项目，最难的技术点是什么？

**答**：建议回答：“我把 WeKnora 的主链路拆成可运行 mini，难点不是调 LangChain，而是保持检索和引用协议语义一致。RRF 要处理分数不可比、单路退化和归一化；流式引用要处理标签跨 chunk、未知别名和断线重放。我用断言验证了公式和边界，并用真实 embedding/DeepSeek 跑通前端。主项目的 RBAC、记忆和 MCP 我完成了源码梳理，但不会把阅读等同于全部亲手实现。”随后选一个你真正做过测试的难点展开。
