# 05 · 多租户、认证与 RBAC 权限

## 学习目标

- 分清用户、租户/空间、成员关系、角色、资源属主和 API Key scope。
- 读懂一次请求从认证到租户上下文、角色授权、资源访问检查的全过程。
- 能设计并验证“防横向越权”，而不只检查前端按钮是否隐藏。

## 1. 核心模型

```text
User 1 ── n TenantMember n ── 1 Tenant
                         │
                         └─ role + status + invited_by + joined_at

KnowledgeBase / Agent / Model / Session ... ── tenant_id + creator_id + 授权范围
```

[TenantMember](../../internal/types/tenant_member.go) 是 `(user_id, tenant_id)` 的成员记录。一个用户可在不同空间拥有不同角色。

成员状态：`active` 可访问；`invited` 尚未接受；`suspended` 保留审计但不可访问。不能把“表里有成员记录”直接等同于“有权限”。

## 2. 四级租户角色

| 角色 | 级别 | 典型能力 |
|---|---:|---|
| `owner` | 40 | 完整控制，包括删除空间、所有权转移、空间 API Key 管理 |
| `admin` | 30 | 成员、集成、模型、向量存储、MCP 等空间配置，不等于可删除空间 |
| `contributor` | 20 | 创建 KB/Agent，编辑自己创建的资源，读取空间其他资源 |
| `viewer` | 10 | 只读；运行明确允许 viewer 的 Agent |

角色比较通过 `TenantRole.Level()` 与 `HasPermission` 完成。注意还有系统管理员、组织角色、KB 级授权和资源属主规则，它们与租户角色相关但不能混为一个概念。

## 3. 一次请求的授权链

```text
Authorization: Bearer <JWT 或 API Key>
→ 验证身份/密钥
→ 确定目标 tenant（JWT 默认值或 X-Tenant-ID）
→ 检查该用户是否有权进入目标空间
→ 查询 active membership，解析 TenantRole
→ 注入 User / Tenant / Role / Principal / APIKeyScope 到 context
→ 路由 RequireRole 或 RequireOwnershipOrRole
→ service/repository 再按 tenant、creator、KB grant 过滤
→ 记录必要的拒绝审计
```

源码：[auth.go](../../internal/middleware/auth.go)、[rbac.go](../../internal/middleware/rbac.go)、[路由守卫](../../internal/router/rbac.go)。

`X-Tenant-ID` 不是“用户写什么就信什么”：服务端必须检查目标空间是否可访问，并重新解析该空间下的角色。

## 4. 角色门槛不够，还要资源级检查

- **RequireRole**：回答“这个角色能不能执行此类操作”。
- **RequireOwnershipOrRole**：回答“contributor 是否为资源创建者，或是否达到更高角色”。
- **KBAccessRead/Write**：回答“该 KB 是否通过属主、空间或组织共享规则向调用者开放”。
- **Repository scope**：查询必须带租户/KB/文档条件，避免只靠 controller 判断。

主项目访问策略还涉及组织共享和 API Key 细粒度 scope。安全设计应默认拒绝，不要因某字段缺失就放行。

## 5. API Key 与个人身份不同

API Key 可能具备 `retrieve/chat/ingest/manage...` 等 scope，且可能限定 KB。平台级 Key 还可能要求显式 `X-Tenant-ID`。个人长期记忆接口要求 full-access，因为记忆属于个人， scoped integration key 不应继承私人记忆空间，见 [routes_memory.go](../../internal/router/routes_memory.go)。

**面试高分点**：认证解决“你是谁”，RBAC 解决“你能做什么”，scope 解决“这把集成密钥被允许做什么”，资源授权解决“你能碰哪一条数据”。四层都要考虑。

## 6. 多租户数据隔离不止数据库 where

还要检查：

- 文件路径与对象存储 key 是否带租户并校验归属；
- 缓存、Redis key、事件流 key 是否含租户/会话；
- 向量库 filter 是否包含 KB/租户范围；
- 异步任务 payload 是否重新校验权限；
- MCP endpoint、分享链接、embed token 是否有独立最小权限；
- 日志与错误信息是否泄露其他空间资源是否存在。

对敏感资源常用 404 降低枚举信息；对已知资源但操作不允许可用 403，具体遵循项目契约。

## 7. 邀请与审计

路由见 [routes_auth_tenant.go](../../internal/router/routes_auth_tenant.go)：邀请创建/撤销、我的邀请列表、接受/拒绝、邀请链接、成员离开、空间审计等。接受邀请时应验证受邀人，而不是任何登录用户都能接受别人的邀请。

## 8. 权限测试矩阵

至少覆盖：

| 场景 | 期望 |
|---|---|
| viewer 创建/修改资源 | 403 |
| contributor 修改自己创建的资源 | 按资源规则允许 |
| contributor 修改他人资源 | 拒绝，除非更高权限或显式授权 |
| 非成员伪造 `X-Tenant-ID` | 拒绝 |
| invited/suspended 成员访问 | 拒绝 |
| A 空间用户读取 B 空间 KB/文件/会话 | 拒绝或 404 |
| scoped API Key 调用 scope 外接口 | 拒绝 |
| 被撤销的 MCP endpoint token | 拒绝 |

可参考 [middleware/rbac_test.go](../../internal/middleware/rbac_test.go) 与 [router/rbac_selfcheck_test.go](../../internal/router/rbac_selfcheck_test.go)。

## 动手任务与验收

1. 画出 JWT 用户切换空间的授权链，标出角色来自哪张成员记录。
2. 写一个最小 Go 或伪代码测试：viewer=10、contributor=20，验证角色等级与未知角色默认拒绝。
3. 为“跨租户读取文件”设计测试，不只测 API，还写出存储路径校验要求。

**验收**：能解释为什么隐藏前端按钮不是权限控制，以及为什么路由角色通过后仍需资源级检查。
