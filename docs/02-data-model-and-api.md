# 02 · 数据模型与 HTTP API

> 对应 `00-architecture-spec.md` §1.3（身份语义）、§6.3（知识库集合）、§7（协同模型）、§8（治理）、§12（品牌集合与路由）。
> 单租户部署下 **`org_unit_id` 取代 `tenant_id`**，是唯一的隔离维度。
> **本文实现的 spec 条款**见 `05-spec-compliance.md`（主要覆盖 S-01、S-02、S-38、S-45、S-60、S-63、S-64）——本文与 `00` 冲突时以 `00` 为准。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 主键 | `_id` 用 ObjectId；对外暴露时统一序列化为字符串 `id` |
| 时间 | 全部存 **UTC 的 BSON Date**；API 出入均 ISO-8601 带时区 |
| 隔离键 | 除 `org_units` 外，**每个集合必须有 `org_unit_id`**，且必须建索引首列 |
| 软删除 | 参与者、设备、Skill 用 `removed_at` / `revoked_at` / `status`；业务实体不物理删除 |
| 审计 | 写操作必须落 `audit_logs`，不接受"这个操作不重要所以不审计" |
| 命名 | 集合名小写复数下划线；字段名小写驼峰 |

**索引初始化**：`core/mongo.py` 的 `ensure_indexes()` 用 `create_index` 幂等声明，**禁止**在应用启动时用 `drop` 重建。索引变更走独立迁移脚本。

---

## 2. 集合定义

### 2.1 identity

**`org_units`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` | ObjectId | |
| `name` | str | 显示名 |
| `code` | str | 唯一编码 |
| `parent_id` | ObjectId \| null | 支持层级（部门 / 项目组） |
| `status` | enum | `active` / `disabled` |
| `created_at` | Date | |

索引：`{code: 1}` unique

> **注意**：单租户下 `org_units` 通常只有一条根记录 + 若干子单元。但**不要**因此省略这个集合——未来转多租户只多加一层维度，查询不用重写。

**`users`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` | ObjectId | |
| `org_unit_id` | ObjectId | |
| `username` | str | 登录名 |
| `display_name` | str | |
| `email` | str \| null | |
| `password_hash` | str | argon2id |
| `status` | enum | `active` / `disabled` |
| `preferences` | dict | 时区、语言等 |
| `created_at` / `last_login_at` | Date | |

索引：`{org_unit_id: 1, username: 1}` unique

**`roles`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `code` | str | `admin` / `member` / `viewer` 等 |
| `name` | str | |
| `permissions` | list[str] | 如 `model.manage`、`skill.manage`、`audit.read` |

索引：`{org_unit_id: 1, code: 1}` unique

**`role_bindings`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `subject_type` | enum | `user` / `role`（用于资源授权） |
| `subject_id` | ObjectId | |
| `role_code` | str | |
| `scope` | enum | `org_unit` / `resource` |
| `resource_type` / `resource_id` | str \| ObjectId \| null | `scope=resource` 时必填 |
| `created_at` | Date | |

索引：`{org_unit_id: 1, subject_id: 1, role_code: 1, resource_id: 1}` unique

**`resource_grants`**（模型 / 知识库 / Skill / 工具 / 设备对用户的可见性）

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `resource_type` | enum | `model_instance` / `knowledge_base` / `skill` / `tool` / `device` |
| `resource_id` | ObjectId \| str | 设备用 `device_id` 字符串 |
| `subject_type` | enum | `user` / `role` / `all` |
| `subject_id` | ObjectId \| null | `subject_type=all` 时为 null |
| `effect` | enum | `allow` / `deny`（**deny 优先**） |

索引：`{org_unit_id: 1, resource_type: 1, resource_id: 1, subject_type: 1, subject_id: 1}` unique

> **deny 优先**是刻意的：私有化环境里"临时收回某人某个模型的权限"必须能立刻生效，不能因为还有一条 allow 就被放过。

### 2.2 模型

**`model_providers`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `provider_type` | enum | `openai_compatible` / `azure_openai` / `anthropic` / `dashscope` / `local` |
| `name` / `code` | str | |
| `base_url` | str | |
| `api_key_enc` | bytes | **加密存储**（AES-GCM，主密钥来自环境变量） |
| `status` | enum | `active` / `disabled` |
| `settings` | dict | 超时、重试、代理 |

索引：`{org_unit_id: 1, code: 1}` unique

**`model_instances`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `provider_id` | ObjectId | |
| `model_name` | str | 上游模型名 |
| `display_name` | str | |
| `capabilities` | list[str] | 至少含 `chat` |
| `max_context_tokens` | int | |
| `settings` | dict | `max_output_tokens` 等 |
| `status` | enum | |
| `created_at` | Date | |

索引：`{org_unit_id: 1, provider_id: 1, model_name: 1}` unique

> 写入这两张表 → **必须失效对应 Profile 缓存**（见 03 文档 §2.4）。

### 2.3 能力（工具与 Skill）

**`tools`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `name` | str | 对外工具名，**必须与 DSH 原生工具名不冲突** |
| `description` | str | 会进入模型 prompt |
| `category` | enum | ★ `server` / `local` / `hybrid` |
| `input_schema` | dict | JSON Schema |
| `output_schema` | dict \| null | `null` = 不做输出校验 |
| `risk_level` | enum | `read` / `write` / `destructive` |
| `approval_required` | bool | |
| `timeout_ms` | int | |
| `handler` | str \| null | `server` 类的实现键（如 `http_fetch`） |
| `local_action` | str \| null | `local` 类的动作名（如 `browser.click`） |
| `status` | enum | |

索引：`{org_unit_id: 1, name: 1}` unique

**`skills`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `key` | str | 稳定标识 |
| `name` | str | |
| `kind` | enum | `skill` / `workflow` / `writing_style` |
| `current_version` | str | 指向 `skill_versions` |
| `requires_tools` | list[str] | ★ 编译期校验依赖 |
| `status` | enum | |

索引：`{org_unit_id: 1, key: 1}` unique

**`skill_versions`**（不可变）

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `skill_id` | | |
| `version` | str | |
| `definition` | dict | 结构化定义（非纯文本） |
| `content_hash` | str | |
| `created_at` / `created_by` | | |

索引：`{skill_id: 1, version: 1}` unique

### 2.4 协同

**`conversations`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `owner_user_id` | ObjectId | |
| `title` | str | |
| `visibility` | enum | `private` / `shared` / `org` |
| `device_id` | str \| null | 绑定的本地执行设备 |
| `profile_version` | str | 发起者的 Profile，**参与者不改变它** |
| `runtime_id` / `session_id` | str \| null | 内核侧标识 |
| `active_run` | dict \| null | `{request_id, owner_user_id, started_at}` |
| `created_at` / `last_active_at` | Date | |

索引：
```
{org_unit_id: 1, owner_user_id: 1, last_active_at: -1}
{session_id: 1} unique, sparse        # 内核会话反查
{org_unit_id: 1, visibility: 1, last_active_at: -1}
```

**`session_participants`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `conversation_id` | | |
| `user_id` | ObjectId | |
| `role` | enum | `owner` / `editor` / `viewer` |
| `joined_at` / `removed_at` | Date \| null | |
| `last_read_seq` | int | 未读数计算 |

索引：`{conversation_id: 1, user_id: 1}` unique；`{org_unit_id: 1, user_id: 1, removed_at: 1}`

**`messages`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `conversation_id` | | |
| `seq` | int | 会话内单调递增 |
| `role` | enum | `user` / `assistant` / `system` / `tool` |
| `content` | list[dict] | `{type: text\|image\|document\|artifact_ref, data}` |
| `meta` | dict | 如 `request_id`、`model`、`usage` |
| `created_at` | Date | |

索引：`{conversation_id: 1, seq: 1}` unique

**`kernel_events`**（★ 全量事件只存一份）

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `conversation_id` | | |
| `session_id` / `runtime_id` | str | |
| `profile_version` | str | |
| `event_id` | str | 内核侧事件 id |
| `cursor` | int | 会话内单调递增 |
| `type` | str | 归一化后的事件类型 |
| `occurred_at` | Date | |
| `payload` | dict | 全量，含工具参数与结果 |
| `source` | dict | `{kernel, kernel_version, native_event_type}` |

索引：
```
{conversation_id: 1, cursor: 1} unique      # 续传与回放
{org_unit_id: 1, occurred_at: -1}           # 审计
{session_id: 1, occurred_at: -1}
```

**保留策略**：本集合是审计依据，**不设 TTL**。体量增长靠归档脚本（导出冷数据后按月删除），不靠自动过期。

**`artifacts`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `conversation_id` | | |
| `created_by` | ObjectId | |
| `kind` | enum | `file` / `image` / `document` |
| `filename` / `mime` / `bytes` | | |
| `storage_path` | str | 本地卷相对路径 |
| `sha256` | str | |
| `created_at` | Date | |

索引：`{conversation_id: 1, created_at: -1}`、`{sha256: 1}`

### 2.5 治理

**`approvals`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `conversation_id` / `session_id` | | |
| `call_id` | str | 内核侧调用 id |
| `tool_name` | str | |
| `arguments` | dict | 全量（审计需要） |
| `arguments_digest` | str | 用于幂等去重 |
| `requester_user_id` | ObjectId | |
| `status` | enum | `pending` / `approved` / `rejected` / `timeout` / `cancelled` |
| `decided_by` / `decided_at` / `reason` | | |
| `expires_at` | Date | |
| `created_at` | Date | |

索引：`{conversation_id: 1, status: 1, created_at: -1}`、`{org_unit_id: 1, call_id: 1, arguments_digest: 1}` unique

**`audit_logs`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `actor_user_id` | ObjectId \| null | 系统动作为 null |
| `action` | str | 如 `tool.execute`、`model.generate`、`device.revoke` |
| `resource_type` / `resource_id` | | |
| `conversation_id` / `session_id` / `turn_request_id` | | |
| `profile_version` | str \| null | |
| `device_id` | str \| null | ★ 在哪个设备执行 |
| `tool_name` / `execution_location` | | `execution_location`: `server` / `local` |
| `approval_id` / `approver_user_id` | | 谁批的 |
| `result` | enum | `ok` / `error` / `denied` |
| `latency_ms` / `error_code` | | |
| `trace_id` | str | |
| `occurred_at` | Date | |

索引：`{org_unit_id: 1, occurred_at: -1}`、`{org_unit_id: 1, action: 1, occurred_at: -1}`、`{trace_id: 1}`

**`quota_usage`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `user_id` | | |
| `period` | str | `YYYY-MM` |
| `model_tokens_prompt` / `model_tokens_completion` | int | |
| `tool_calls` | int | |
| `updated_at` | Date | |

索引：`{org_unit_id: 1, user_id: 1, period: 1}` unique
扣减用 `$inc` + `upsert`，**不用读改写**。

### 2.6 本地 Agent

**`devices`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `user_id` | | |
| `device_id` | str | CLI 生成（公钥指纹） |
| `display_name` | str | ★ 多设备时用户要看到"去哪台机器操作" |
| `os` / `agent_version` | str | |
| `capabilities` | list[str] | 如 `browser`、`terminal`、`filesystem` |
| `status` | enum | `active` / `revoked` |
| `bound_at` / `last_seen_at` / `revoked_at` | Date | |

索引：`{org_unit_id: 1, device_id: 1}` unique；`{org_unit_id: 1, user_id: 1, status: 1}`

**`device_credentials`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `device_id` | | |
| `user_id` | ObjectId | |
| `public_key` | str | CLI 密钥对公钥 |
| `secret_hash` | str | `device_credential` 的哈希（不存明文） |
| `issued_at` / `expires_at` / `revoked_at` | Date | |

索引：`{org_unit_id: 1, device_id: 1, revoked_at: 1}`

**`pairing_sessions`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | `org_unit_id` 在确认后才填 | |
| `pairing_id` | str | |
| `code_hash` | str | 6 位码的哈希（不存明文） |
| `device_key_public` | str | |
| `agent_version` / `os` | str | |
| `status` | enum | `pending` / `confirmed` / `claimed` / `expired` |
| `bound_user_id` / `bound_org_unit_id` | | 确认时填 |
| `attempts` | int | ★ 防爆破 |
| `expires_at` / `created_at` | Date | |

索引：`{pairing_id: 1}` unique；`{expires_at: 1}` **TTL index**（`expireAfterSeconds: 0`）

> 配对码必须存哈希 + 限制尝试次数。6 位数字只有 100 万种，无限制尝试等于没有保护。

### 2.7 内核

**`kernel_runtimes`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` | | |
| `runtime_id` | str | |
| `profile_version` | str | |
| `isolation_key` | str | `{org_unit_id}:{user_id}` |
| `instance_id` | str | 宿主实例标识（多实例时用） |
| `created_at` / `disposed_at` | Date | |

索引：`{runtime_id: 1}` unique；`{org_unit_id: 1, profile_version: 1}`

**`turn_records`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` / `org_unit_id` / `conversation_id` | | |
| `request_id` | str | |
| `owner_user_id` | ObjectId | 发起者 |
| `status` | enum | `running` / `completed` / `cancelled` / `failed` |
| `started_at` / `finished_at` | Date | |
| `cancel_cause` | str \| null | |

索引：`{conversation_id: 1, started_at: -1}`

> **turn 抢占用 Redis**（`SET NX EX`），不占 Mongo——它需要 TTL 语义和原子性。`turn_records` 只负责留痕。

### 2.8 知识库（阶段 2）

沿用 `00-architecture-spec.md` §6.3 设计，此处补全字段：

**`knowledge_documents`**

| 字段 | 说明 |
|---|---|
| `_id` / `org_unit_id` | |
| `title` / `source_type` / `source_ref` | |
| `checksum` | 幂等重入库依据 |
| `status` | `pending` / `indexing` / `ready` / `failed` |
| `char_len` / `chunk_count` | |
| `acl` | `{visibility: org\|restricted, allowed_user_ids[], allowed_roles[]}` |
| `created_at` / `created_by` | |

索引：`{org_unit_id: 1, source_ref: 1}` unique；`{org_unit_id: 1, status: 1}`

**`knowledge_chunks`**

| 字段 | 说明 |
|---|---|
| `_id` / `org_unit_id` / `document_id` / `seq` | |
| `text` / `terms[]` / `title_terms[]` | |
| `heading_path[]` | 章节路径 |
| `page_from` / `page_to` | 回指原文 |
| `acl` | 随文档，冗余存以支持单 chunk 过滤 |

索引：
```
{org_unit_id: 1, terms: 1}
{org_unit_id: 1, title_terms: 1}
{document_id: 1, seq: 1}
{org_unit_id: 1, text: "text"}          # 英文 / 精确串兜底
```

**`knowledge_terms`**（BM25 的 df 统计）

索引：`{org_unit_id: 1, term: 1}` unique

---

### 2.9 品牌

**`branding_settings`**（单文档集合，对应 `00` §12 · §0.5 客户品牌层）

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` | const | **固定 `"singleton"`**——集合永远最多一条，靠固定主键强制 |
| `display_name` | str | 客户公司全称 |
| `short_name` | str | 简称 |
| `theme` | `{ primary, accent }` | 仅 `#RRGGBB` |
| `logo_wide` / `logo_square` / `favicon` | AssetRef | `{ storage_key, content_type, bytes, width, height }` |
| `vendor_badge_enabled` | bool | 默认 `true`（页脚"由 ZY-Agents 提供技术支持"，客户可关） |
| `updated_at` / `updated_by` | Date / ObjectId | |
| （阶段 2 追加） | | `login_title` / `login_welcome` / `footer_copyright` / `help_url` / `support_contact` |

索引：`{_id: 1}` unique（即单文档约束）

> **为什么不放 `org_units` 里**：品牌是**客户部署**级的展示配置，不是**组织单元**级的业务数据——单租户下它不应随 org_unit 变化，将来转多租户也多半是"每部署一份"而非"每租户一份"。独立成集合是为了语义清晰，不做错误的耦合。

> **集合初始态**：bootstrap 幂等 seed（`00` §12.6）——为空时写入 `BRANDING_SEED_FILE` 或默认值；**非空则跳过**，避免每次重启覆盖后台修改。默认值：`display_name="ZY-Agents"`、`vendor_badge_enabled=true`。

**资产二进制不入 Mongo**（与 `artifacts` 同策略，见 `01` §目录树 `branding/assets.py`）：文件落 `branding-assets` 卷，Mongo 只存 `AssetRef`。文件名由**服务端生成**（UUID），**不接受**请求参数参与路径；logo 文件本身**不进入内容寻址**，也不进 Profile。

---

## 3. 事件投影的敏感度声明表

脱敏**只在 `collaboration/projection.py` 发生**，规则来自一张声明表，不在代码里散落 `if`。

| 事件类型 | 字段 | 敏感度 | viewer 可见 |
|---|---|---|---|
| `tool.call.requested` | `tool_name` | internal | ✅（保留名称） |
| | `arguments` | **secret** | ❌ |
| `tool.call.completed` | `result` | **secret** | ❌ |
| | `status` / `latency_ms` | public | ✅ |
| `artifact.created` | `filename` | internal | ✅ |
| | `storage_path` | **secret** | ❌ |
| | `download_url` | **secret** | ❌（连 URL 都不给） |
| `model.request` | `system` / `messages` | **secret** | ❌ |
| | `model_name` | internal | ✅ |
| `assistant.message` | `content` | public | ✅ |
| `localagent.auth_required` | `url` | internal | ✅（参与者需知道去哪登录） |
| | `device_display_name` | public | ✅ |
| `error` | `code` | public | ✅ |
| | `details` | internal | ⚠️ 按子字段判定 |

**规则**：
1. 表里未声明的字段一律按 `secret` 处理（**默认拒绝**，不是默认放行）。
2. viewer 的 SSE 推的是脱敏后的流。前端过滤不算过滤。
3. `assistant.message` 是 public，因为协同的核心价值就是让参与者读到答复。

---

## 4. HTTP API 路由表

### 4.1 鉴权约定

| 前缀 | 鉴权 | 说明 |
|---|---|---|
| `/api/v1/*` | Bearer JWT → `RequestContext` | 公开（登录后） |
| `/api/v1/branding` | **无**（匿名） | ★ **唯一**的免鉴权 `/api/v1/*` 端点，供登录页渲染品牌 |
| `/internal/kernel/*` | HMAC 短时 token | ★ nginx `deny all` |
| `/localagent/*` | WS 用一次性 ticket；HTTP 用设备凭据签名 | |

**统一错误体**：

```json
{ "error": { "code": "conversation_busy", "message": "…", "details": {}, "trace_id": "…" } }
```

### 4.2 认证与自我

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/login` | 返回 access token |
| POST | `/api/v1/auth/logout` | |
| GET | `/api/v1/auth/me` | 返回 `{user, org_unit, roles[], permissions[]}` |
| GET | `/api/v1/branding` | ★ **公开**（登录前也要能渲染品牌）；**只返回白名单字段**（`public_view()`，见 `01` §4.8）——不返回内部配置、密钥或未启用开关 |

### 4.3 会话与协同

| 方法 | 路径 | 角色要求 | 说明 |
|---|---|---|---|
| GET | `/api/v1/conversations` | — | 我参与的；`?cursor&limit&scope=mine\|participating` |
| POST | `/api/v1/conversations` | — | `{title, visibility, device_id?}` |
| GET | `/api/v1/conversations/{id}` | 参与者 | |
| PATCH | `/api/v1/conversations/{id}` | owner | 改标题 / visibility / 绑定设备 |
| DELETE | `/api/v1/conversations/{id}` | owner | 归档 |
| GET | `/api/v1/conversations/{id}/messages` | 参与者 | 分页 |
| GET | `/api/v1/conversations/{id}/stream` | 参与者 | ★ SSE，支持 `Last-Event-ID` |
| GET | `/api/v1/conversations/{id}/participants` | 参与者 | |
| POST | `/api/v1/conversations/{id}/participants` | owner | `{user_id, role}` |
| PATCH | `/api/v1/conversations/{id}/participants/{uid}` | owner | 改角色 |
| DELETE | `/api/v1/conversations/{id}/participants/{uid}` | owner | ★ 同时吊销其文件 URL |
| POST | `/api/v1/conversations/{id}/turns` | editor+ | 发起 turn；busy 时返回 409 |
| POST | `/api/v1/conversations/{id}/turns/{request_id}/cancel` | 持有者或 owner | |
| POST | `/api/v1/conversations/{id}/takeover/request` | editor+ | 请求接管 |
| POST | `/api/v1/conversations/{id}/takeover/accept` | 原持有者 | 确认转移 |
| GET | `/api/v1/conversations/{id}/events` | 参与者 | 回放（按 cursor），走投影 |

### 4.4 审批（人在环）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/conversations/{id}/approvals` | 待决列表（含参数，按角色投影） |
| POST | `/api/v1/approvals/{id}/decision` | `{approved, reason}` |

### 4.5 产物

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/artifacts/{id}/download` | ★ 签名 URL；校验签名 + ACL + 撤销状态 |

下载校验三步（**不靠 TTL 兜底**）：签名有效未过期 → 当前用户对该会话/知识库仍有 ACL → nonce 未被拉黑且参与关系未 `removed_at`。

### 4.6 设备与本地 Agent

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/v1/devices` | JWT | 我的设备 |
| DELETE | `/api/v1/devices/{device_id}` | JWT | 解绑 + 吊销凭据 + 断连 |
| POST | `/api/v1/localagent/pairing/begin` | 无（CLI） | ★ 生成配对码，TTL 10min |
| POST | `/api/v1/localagent/pairing/confirm` | JWT | ★ Web 端输入配对码 |
| POST | `/api/v1/localagent/pairing/claim` | 私钥签名 | CLI 领取 `device_credential` |
| POST | `/api/v1/localagent/ticket` | 设备凭据签名 | ★ 换 60s 一次性 ticket |
| GET | `/api/v1/localagent/version` | 无 | `{latest, minimum}` |
| WS | `/localagent/connect?ticket=` | ticket | ★ 长连接；身份从 ticket 解析 |

> **不接受任何客户端声明的 `user_id`。** 反面案例：把身份做成 `?user_id=` → header → bearer 三选一，全是客户端可控值。

### 4.7 知识库（阶段 2）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/knowledge/documents` | 列表 |
| POST | `/api/v1/knowledge/documents` | 上传（multipart） |
| GET | `/api/v1/knowledge/documents/{id}` | 详情 |
| DELETE | `/api/v1/knowledge/documents/{id}` | |
| POST | `/api/v1/knowledge/search` | ★ `{query, filters, top_k}` → `[Hit]` |

### 4.8 管理端

| 方法 | 路径 | 权限 |
|---|---|---|
| GET/POST/PATCH | `/api/v1/admin/org-units[/{id}]` | `org.manage` |
| GET/POST/PATCH | `/api/v1/admin/users[/{id}]` | `user.manage` |
| GET/POST/DELETE | `/api/v1/admin/role-bindings` | `user.manage` |
| GET/POST/PATCH | `/api/v1/admin/model-providers[/{id}]` | `model.manage` |
| GET/POST/PATCH | `/api/v1/admin/model-instances[/{id}]` | `model.manage` |
| GET/POST/PATCH | `/api/v1/admin/tools[/{id}]` | `tool.manage` |
| GET/POST/PATCH | `/api/v1/admin/skills[/{id}]` | `skill.manage` |
| GET | `/api/v1/admin/devices` | `device.manage` |
| DELETE | `/api/v1/admin/devices/{device_id}` | `device.manage` |
| GET | `/api/v1/admin/audit` | `audit.read` |
| GET | `/api/v1/admin/quota` | `quota.read` |
| GET/PATCH | `/api/v1/admin/grants[/{id}]` | `acl.manage` |
| GET/PATCH | `/api/v1/admin/branding` | `brand.manage` | ★ 读改 `branding_settings`；**不触发任何 Profile 失效**（与模型 / 工具不同） |
| PUT | `/api/v1/admin/branding/assets/{slot}` | `brand.manage` | 上传 logo / favicon（类型嗅探 + 尺寸/字节上限 + 服务端命名） |
| DELETE | `/api/v1/admin/branding/assets/{slot}` | `brand.manage` | 移除资产 |

### 4.9 内部（内核回调，HMAC）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/internal/kernel/model/generate` | 模型推理（流式 NDJSON） |
| POST | `/internal/kernel/tools/execute` | 工具执行 |
| POST | `/internal/kernel/tools/approval` | 审批请求 |
| GET | `/internal/kernel/health` | 供宿主探活（也走 HMAC） |

详细契约见 `03-kernel-contract.md`。

---

## 5. 索引清单（首版）

一次性建齐，避免上线后再补索引导致锁表风险（Mongo 4.2+ 用滚动构建，但仍应提前）：

```
org_units              {code:1} u
users                  {org_unit_id:1, username:1} u
roles                  {org_unit_id:1, code:1} u
role_bindings          {org_unit_id:1, subject_id:1, role_code:1, resource_id:1} u
resource_grants        {org_unit_id:1, resource_type:1, resource_id:1, subject_type:1, subject_id:1} u
model_providers        {org_unit_id:1, code:1} u
model_instances        {org_unit_id:1, provider_id:1, model_name:1} u
tools                  {org_unit_id:1, name:1} u
skills                 {org_unit_id:1, key:1} u
skill_versions         {skill_id:1, version:1} u
conversations          {org_unit_id:1, owner_user_id:1, last_active_at:-1}
                       {session_id:1} u sparse
                       {org_unit_id:1, visibility:1, last_active_at:-1}
session_participants   {conversation_id:1, user_id:1} u
                       {org_unit_id:1, user_id:1, removed_at:1}
messages               {conversation_id:1, seq:1} u
kernel_events          {conversation_id:1, cursor:1} u
                       {org_unit_id:1, occurred_at:-1}
                       {session_id:1, occurred_at:-1}
artifacts              {conversation_id:1, created_at:-1}
                       {sha256:1}
approvals              {conversation_id:1, status:1, created_at:-1}
                       {org_unit_id:1, call_id:1, arguments_digest:1} u
audit_logs             {org_unit_id:1, occurred_at:-1}
                       {org_unit_id:1, action:1, occurred_at:-1}
                       {trace_id:1}
quota_usage            {org_unit_id:1, user_id:1, period:1} u
devices                {org_unit_id:1, device_id:1} u
                       {org_unit_id:1, user_id:1, status:1}
device_credentials     {org_unit_id:1, device_id:1, revoked_at:1}
pairing_sessions       {pairing_id:1} u
                       {expires_at:1} TTL
kernel_runtimes        {runtime_id:1} u
                       {org_unit_id:1, profile_version:1}
turn_records           {conversation_id:1, started_at:-1}
knowledge_documents    {org_unit_id:1, source_ref:1} u
                       {org_unit_id:1, status:1}
knowledge_chunks       {org_unit_id:1, terms:1}
                       {org_unit_id:1, title_terms:1}
                       {document_id:1, seq:1}
                       {org_unit_id:1, text:"text"}
knowledge_terms        {org_unit_id:1, term:1} u
branding_settings       {_id:1} u        # 单文档
```

（`u` = unique）
