# 01 · 工程详细设计

> 对应 `00-architecture-spec.md` §2（主服务分层）、§1（部署形态）、§9（前端边界）、§11（分期路线）、§12（品牌与白标）。
> 文中所有路径均已实现落地。
> **本文实现的 spec 条款**见 `05-spec-compliance.md`（覆盖主服务分层 S-05~S-08、品牌 S-60~S-64 等）——本文与 `00` 冲突时以 `00` 为准。

---

## 1. 工程原则

四条不可动摇的约束，后面所有决策都由它们推出：

1. **后端只做一个进程。** 不拆出与主服务共享 MongoDB 的旁路服务——那等于造分布式单体。
2. **`kernel/` 是全项目唯一接触 DSH 的位置。** 除此之外任何模块 import DSH 相关代码，CI 直接失败。
3. **仓储方法强制带 `RequestContext`。** 不提供"不带上下文"的查询方法，让越权成为类型期问题。
4. **阶段 1 零新增基础设施。** 不引入向量库、对象存储、除 Celery 外的消息中间件。

---

## 2. 仓库目录树

```
ZY-Agents/
├── README.md
├── docker-compose.yml
├── docker-compose.build.yml
├── .env.example
├── .env                                   # 不入库
├── Makefile                               # 本地开发与构建入口
│
├── docs/                                  # 本文档集
│
├── services/
│   ├── api/                               # ★ 主服务（Python / FastAPI）
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── app/
│   │   │   ├── main.py                    # 应用装配
│   │   │   ├── settings.py                # pydantic-settings
│   │   │   ├── deps.py                    # FastAPI 依赖注入
│   │   │   │
│   │   │   ├── core/                      # 基础设施（不含领域）
│   │   │   │   ├── mongo.py               # 客户端、索引初始化
│   │   │   │   ├── redis.py               # 客户端、pub/sub 封装
│   │   │   │   ├── errors.py              # 领域异常 + HTTP 映射
│   │   │   │   ├── clock.py               # 统一时间源（可注入，便于测试）
│   │   │   │   ├── ids.py                 # ULID / ObjectId 生成
│   │   │   │   └── logging.py             # 结构化日志 + trace_id
│   │   │   │
│   │   │   ├── identity/                  # 身份与权限
│   │   │   │   ├── context.py             # ★ RequestContext
│   │   │   │   ├── auth.py                # 登录态校验 → RequestContext
│   │   │   │   ├── users.py               # 用户仓储
│   │   │   │   ├── org_units.py           # 组织单元仓储
│   │   │   │   ├── roles.py               # 角色与绑定
│   │   │   │   ├── acl.py                 # 资源 ACL 判定
│   │   │   │   └── repository.py          # ★ 强制上下文的仓储基类
│   │   │   │
│   │   │   ├── kernel/                    # ★ 内核接入层（唯一接触 DSH 处）
│   │   │   │   ├── contract/              # 冻结契约（只增不改）
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── versions.py        # 契约版本常量
│   │   │   │   │   ├── commands.py        # CreateRuntime/Session/Send/Cancel
│   │   │   │   │   ├── handles.py         # Runtime/SessionHandle, SessionStatus
│   │   │   │   │   ├── events.py          # KernelEventEnvelope
│   │   │   │   │   ├── errors.py          # KernelError
│   │   │   │   │   └── protocol.py        # AgentKernelContract (Protocol)
│   │   │   │   ├── profile/               # Runtime Profile 编译
│   │   │   │   │   ├── models.py          # RuntimeProfileSnapshot
│   │   │   │   │   ├── compiler.py        # ★ 确定性编译
│   │   │   │   │   ├── catalog.py         # admin 行 → 编译输入
│   │   │   │   │   ├── tools.py           # 工具快照编译 + 类别/审批
│   │   │   │   │   ├── skills.py          # Skill 编译 + 依赖校验
│   │   │   │   │   ├── store.py           # Profile 缓存（按 content_hash）
│   │   │   │   │   └── host_payload.py    # ★ 唯一注入密钥的位置
│   │   │   │   ├── dsh/                   # DSH 适配器（上游 API 只在此出现）
│   │   │   │   │   ├── gateway.py         # implements AgentKernelContract
│   │   │   │   │   ├── transport.py       # NDJSON 解析 / 心跳 / 重连
│   │   │   │   │   ├── runner.py          # 单 turn 执行
│   │   │   │   │   ├── recovery.py        # 断线续跑
│   │   │   │   │   ├── host_process.py    # 宿主进程管理（本地开发用）
│   │   │   │   │   └── adapters/          # ★ 升级时唯一需要改的目录
│   │   │   │   │       ├── preset.py
│   │   │   │   │       ├── events.py
│   │   │   │   │       ├── tools.py
│   │   │   │   │       └── models.py
│   │   │   │   ├── inbound/               # ★ 内核回调入口
│   │   │   │   │   ├── credentials.py     # HMAC 短时 token 签发/校验
│   │   │   │   │   ├── model_endpoint.py
│   │   │   │   │   ├── tool_endpoint.py
│   │   │   │   │   └── approval_endpoint.py
│   │   │   │   └── turn/
│   │   │   │       ├── admission.py       # 准入：授权、并发抢占
│   │   │   │       ├── claims.py          # 单写者 claim
│   │   │   │       ├── finalization.py
│   │   │   │       └── cancellation.py
│   │   │   │
│   │   │   ├── collaboration/             # 会话与协同
│   │   │   │   ├── conversations.py
│   │   │   │   ├── participants.py
│   │   │   │   ├── messages.py
│   │   │   │   ├── projection.py          # ★ 按 viewer 角色分级 + 脱敏
│   │   │   │   ├── sensitivity.py         # 字段敏感度声明表
│   │   │   │   ├── share.py
│   │   │   │   └── stream.py              # SSE 推送 + 断线续传
│   │   │   │
│   │   │   ├── capability/                # 服务端工具（server 类）
│   │   │   │   ├── registry.py            # 工具注册表
│   │   │   │   ├── base.py                # 工具接口与上下文
│   │   │   │   └── tools/
│   │   │   │       ├── http_fetch.py      # HTTP 抓取（替代公网浏览器）
│   │   │   │       ├── knowledge_search.py
│   │   │   │       ├── doc_parse.py
│   │   │   │       └── data_utils.py
│   │   │   │
│   │   │   ├── localagent/                # ★ 本地 Agent 桥
│   │   │   │   ├── pairing.py             # 配对码签发与绑定
│   │   │   │   ├── devices.py             # 设备台账 + 凭据
│   │   │   │   ├── tickets.py             # 一次性 ticket
│   │   │   │   ├── registry.py            # Redis 在线注册表
│   │   │   │   ├── router.py              # 跨实例路由
│   │   │   │   ├── ws_endpoint.py         # /localagent/connect
│   │   │   │   ├── frames.py              # 帧模型（严格校验）
│   │   │   │   ├── drivers/
│   │   │   │   │   ├── base.py            # observe / act / verify
│   │   │   │   │   ├── fast_path.py       # 工作流缓存重放
│   │   │   │   │   ├── explore.py         # 观察-决策-动作循环
│   │   │   │   │   └── form.py            # 表单结构化处理
│   │   │   │   └── verification/
│   │   │   │       ├── contract.py        # 动作效果契约
│   │   │   │       ├── tracker.py         # 三态判定
│   │   │   │       └── form_scope.py
│   │   │   │
│   │   │   ├── knowledge/                 # 知识库（阶段 2）
│   │   │   │   ├── ingest.py
│   │   │   │   ├── chunker.py
│   │   │   │   ├── tokenizer.py           # jieba + bigram 回退
│   │   │   │   ├── indexer.py             # terms 写入 + df 维护
│   │   │   │   ├── retriever.py           # ★ 接口固定
│   │   │   │   └── bm25.py
│   │   │   │
│   │   │   ├── document/                  # 文档解析（阶段 2）
│   │   │   │   ├── pipeline.py
│   │   │   │   ├── parsers/               # pdf / docx / xlsx / pptx / md
│   │   │   │   └── storage.py             # 本地卷 + 签名 URL
│   │   │   │
│   │   │   ├── branding/                  # ★ 品牌与白标（00 §12）
│   │   │   │   ├── settings.py            # 单文档读写 + 幂等 seed
│   │   │   │   ├── assets.py              # logo / favicon 上传：内容嗅探 + 尺寸校验 + 服务端命名
│   │   │   │   ├── public_view.py         # ★ 公开白名单视图（未认证端点只返回此处列出的字段）
│   │   │   │   └── repository.py
│   │   │   │
│   │   │   ├── governance/
│   │   │   │   ├── approval.py            # 唯一生效路径
│   │   │   │   ├── audit.py
│   │   │   │   └── quota.py
│   │   │   │
│   │   │   └── api/                       # HTTP 层（薄，只做协议转换）
│   │   │       ├── router.py              # 汇总注册
│   │   │       ├── endpoints/             # 见 02 文档路由表
│   │   │       └── middleware.py          # trace_id、异常映射、鉴权
│   │   │
│   │   ├── scripts/
│   │   │   ├── check_layering.py          # ★ 分层依赖守卫
│   │   │   ├── check_contract_freeze.py   # 契约冻结校验
│   │   │   ├── seed_dev_data.py
│   │   │   └── kernel_upgrade/            # 见 03 文档
│   │   ├── tests/
│   │   └── kernel/                        # 内核版本声明物料（见 03 文档）
│   │       ├── VERSIONS.lock
│   │       ├── COMPATIBILITY.md
│   │       ├── compat-matrix.yaml
│   │       └── UPSTREAM_NOTICE.md
│   │
│   ├── kernel-host/                       # ★ Node 内核宿主
│   │   ├── package.json
│   │   ├── pnpm-lock.yaml
│   │   ├── Dockerfile
│   │   └── src/
│   │       ├── host.mjs                   # 入口：起 HTTP 服务
│   │       ├── protocol.mjs               # zy.dsh-host.v1 常量
│   │       ├── http-server.mjs            # 路由
│   │       ├── runtime-manager.mjs        # runtime 台账
│   │       ├── kernel-runtime.mjs         # ★ cordis 装配 DSH
│   │       ├── event-journal.mjs          # 事件落盘 JSONL
│   │       ├── model-adapter.mjs          # ★ LlmAdapter → 主服务模型网关
│   │       ├── tool-bridge.mjs            # ★ 工具注册 → 主服务工具网关
│   │       ├── approval-broker.mjs
│   │       ├── gateway-client.mjs         # 回调 HTTP 客户端
│   │       ├── auth.mjs                   # 宿主 token 校验
│   │       └── adapters/                  # ★ 上游兼容适配（升级爆炸半径）
│   │
│   └── localagent/                        # ★ 本地 Agent CLI（阶段 3）
│       ├── package.json
│       ├── src/
│       │   ├── cli.mjs                    # pair / status / logs / run
│       │   ├── config.mjs                 # 配置与状态目录
│       │   ├── keychain.mjs               # 私钥与凭据保管
│       │   ├── pairing.mjs                # 配对流程
│       │   ├── connection.mjs             # WS 连接与重连
│       │   ├── frames.mjs                 # 帧编解码
│       │   ├── browser/
│       │   │   ├── launcher.mjs           # 独立 profile 启停
│       │   │   ├── observe.mjs            # 页面快照
│       │   │   ├── act.mjs                # 点击/输入/导航
│       │   │   └── verify.mjs             # 效果核验
│       │   └── handlers/                  # 动作分发
│       └── tests/
│
├── apps/
│   ├── web/                               # 用户端 SPA（Vue 3 + Vite + TS）
│   │   ├── package.json
│   │   ├── vite.config.ts
│   │   └── src/
│   │       ├── api/                       # ★ 唯一出网位置
│   │       ├── sse/                       # 事件流消费与重连
│   │       ├── stores/
│   │       ├── features/
│   │       │   ├── auth/
│   │       │   ├── conversation/          # 会话、消息、事件渲染
│   │       │   ├── collaboration/         # 参与者、分享
│   │       │   ├── localagent/            # 设备状态、配对引导
│   │       │   └── knowledge/             # 阶段 2
│   │       └── components/
│   └── admin/                             # 管理端 SPA
│       └── src/features/
│           ├── org-units/
│           ├── members/
│           ├── models/                    # 写这张表 → 触发 Profile 重编译
│           ├── skills/                    # 含 server/local 类别与审批开关
│           ├── devices/                   # 设备台账与吊销
│           ├── audit/
│           ├── quota/
│           └── branding/                  # ★ 品牌设置：名称 / logo / 主题色 / 页脚厂商标识开关
│
├── deploy/
│   ├── nginx/nginx.conf
│   ├── bootstrap/                         # 密钥生成（见第 6 节）
│   └── branding.example.yaml              # ★ 部署预置品牌样例（BRANDING_SEED_FILE，见 00 §12.6）
│
└── .github/workflows/
    ├── ci.yml                             # lint / test / layering / contract
    └── kernel-upgrade.yml                 # 每周探测上游（见 03 文档）
```

---

## 3. 分层依赖规则

```
kernel/dsh/    → 任何 app.* 模块        允许（适配器需要调用宿主服务）
app.*          → kernel/dsh/            禁止（唯一例外：kernel/dsh/gateway.py 自身）
app.*          → kernel/contract/       允许
kernel/        → collaboration/         禁止（回调走 inbound/ 的独立端点）
capability/    → collaboration/         禁止（工具不感知会话，只收 ToolContext）
localagent/    → kernel/                禁止（本地 Agent 只经工具网关间接被用）
```

### 3.1 CI 守卫实现要点（`scripts/check_layering.py`）

用 `ast` 解析而非正则，避免字符串误报：

```
1. 遍历 services/api/app/**/*.py，收集 import 图
2. 规则匹配：对每条 import 检查 (源模块, 目标模块) 是否命中禁止表
3. 违规 → 打印 `来源文件:行号 → 禁止的 import` 并 exit 1
4. 例外清单写在脚本顶部 `ALLOWED_EXCEPTIONS`，每项必须带注释说明理由
```

**例外清单必须保持极短**（目标 ≤3 条）。新增例外需在 PR 描述里说明——例外清单变长就等于规则失效。

### 3.2 契约冻结校验（`scripts/check_contract_freeze.py`）

对 `kernel/contract/` 下的每个文件计算 AST 级签名摘要（类名、字段名、字段类型、方法名），与 `kernel/contract/.frozen.json` 比对：

- **新增**字段或方法 → 警告（向后兼容的扩展）
- **删除或改类型** → 失败（破坏性变更，必须升 `schema_version`）
- `versions.py` 里的常量值变化 → 失败，除非同时更新 `.frozen.json` 并在 PR 说明

---

## 4. 模块接口签名

签名用 Python 类型标注表示，省略函数体。所有仓储方法**第一个参数是 `ctx: RequestContext`**。

### 4.1 identity

```python
# identity/context.py
@dataclass(frozen=True, slots=True)
class RequestContext:
    user_id: str
    org_unit_id: str
    roles: frozenset[str]
    device_id: str | None = None
    trace_id: str = ""
    # 由服务端派生，永不来自请求体

# identity/auth.py
async def resolve_context(authorization: str | None) -> RequestContext: ...
    # 从 JWT/Session 解析；任何失败抛 Unauthenticated，绝不返回匿名上下文

# identity/repository.py
class ScopedRepository(Generic[TDoc]):
    def __init__(self, collection: AsyncCollection) -> None: ...
    def _scope(self, ctx: RequestContext) -> dict[str, Any]: ...
        # → {"org_unit_id": ctx.org_unit_id}；所有查询自动并入
    async def find_one(self, ctx: RequestContext, **filters) -> TDoc | None: ...
    async def find_many(self, ctx: RequestContext, **filters) -> list[TDoc]: ...
    # 不提供不带 ctx 的重载 —— 这是刻意的

# identity/acl.py
async def ensure(ctx: RequestContext, *, resource: ResourceType, resource_id: str, action: Action) -> None: ...
    # 失败抛 Forbidden；成功无返回
async def visible_ids(ctx: RequestContext, *, resource: ResourceType) -> set[str]: ...
    # 用于列表类查询的批量过滤
```

### 4.2 collaboration

```python
# collaboration/conversations.py
async def create(ctx: RequestContext, *, title: str, visibility: Visibility,
                 device_id: str | None = None) -> Conversation: ...
async def get(ctx: RequestContext, conversation_id: str) -> Conversation: ...
    # 非参与者 → Forbidden（不返回 404，避免探测存在性）
async def list_for_user(ctx: RequestContext, *, cursor: str | None, limit: int) -> Page[Conversation]: ...

# collaboration/participants.py
async def add(ctx: RequestContext, conversation_id: str, *, user_id: str, role: Role) -> Participant: ...
    # 仅 owner 可调用
async def remove(ctx: RequestContext, conversation_id: str, *, user_id: str) -> None: ...
    # 设 removed_at，同时吊销该用户的已签发文件 URL（见 7.4）
async def role_of(ctx: RequestContext, conversation_id: str) -> Role: ...

# collaboration/messages.py
async def append(ctx: RequestContext, conversation_id: str, *, seq: int, role: MessageRole,
                 content: list[ContentBlock], meta: dict[str, Any]) -> Message: ...

# collaboration/projection.py  ★ 脱敏唯一位置
def project(event: KernelEventEnvelope, *, viewer_role: Role) -> ViewerEvent | None: ...
    # owner / editor → FullViewerEvent（原样）
    # viewer        → RedactedViewerEvent（按 sensitivity 过滤）
    # 返回 None 表示该事件对该角色完全不可见

# collaboration/stream.py
def subscribe(ctx: RequestContext, conversation_id: str, *, after_cursor: int = 0
              ) -> AsyncIterator[ViewerEvent]: ...
```

### 4.3 kernel

```python
# kernel/contract/protocol.py
class AgentKernelContract(Protocol):
    async def create_runtime(self, request: CreateRuntimeRequest) -> RuntimeHandle: ...
    async def dispose_runtime(self, runtime_id: str) -> None: ...
    async def create_session(self, request: CreateSessionRequest) -> SessionHandle: ...
    async def resume_session(self, session_id: str) -> SessionHandle: ...
    async def send(self, request: SendRequest) -> str: ...
    async def cancel(self, request: CancelSessionRequest) -> dict[str, Any]: ...
    async def dispose_session(self, session_id: str) -> None: ...
    def subscribe(self, session_id: str, *, after_cursor: int = 0
                  ) -> AsyncIterator[KernelEventEnvelope]: ...

# kernel/profile/compiler.py
async def compile_profile(org_unit_id: str, user_id: str) -> RuntimeProfileSnapshot: ...
    # 纯函数式：同输入必得同 profile_version
    # 编译输入不得包含任何运行时易变状态（尤其 local Agent 在线性）

# kernel/profile/host_payload.py  ★ 唯一注入密钥处
def build_host_payload(profile: RuntimeProfileSnapshot, *, runtime_id: str,
                       model_token: str, tool_token: str, gateway_base: str) -> dict[str, Any]: ...

# kernel/turn/admission.py
async def admit(ctx: RequestContext, conversation_id: str, *, request_id: str) -> TurnAdmission: ...
    # 校验：Profile 是否存在、Skill 授权、并发 claim
    # 抢不到 → ConversationBusyError(holder_user_id)

# kernel/turn/claims.py
async def claim_turn(conversation_id: str, request_id: str, *, ttl_seconds: int) -> Claim | None: ...
async def release_turn(conversation_id: str, request_id: str) -> None: ...
async def transfer_turn(conversation_id: str, *, to_request_id: str) -> None: ...  # 显式接管
```

### 4.4 capability

```python
# capability/base.py
@dataclass(frozen=True)
class ToolContext:
    ctx: RequestContext
    conversation_id: str | None
    session_id: str | None
    call_id: str
    trace_id: str

class ServerTool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    category: Literal["server"] = "server"
    risk_level: Literal["read", "write", "destructive"]
    approval_required: bool
    timeout_ms: int
    async def execute(self, args: dict[str, Any], tc: ToolContext) -> ToolResult: ...

# capability/registry.py
def register(tool: ServerTool) -> None: ...
def get(name: str) -> ServerTool | None: ...
def all_tools() -> tuple[ServerTool, ...]: ...
```

### 4.5 localagent

```python
# localagent/pairing.py
async def begin_pairing(*, device_key_public: str, agent_version: str, os: str) -> PairingSession: ...
    # 返回 6 位码，TTL 10 分钟
async def confirm_pairing(ctx: RequestContext, *, code: str) -> None: ...
    # Web 端调用，把 pairing 与 user_id / org_unit_id 绑定
async def claim_pairing(*, pairing_id: str, signature: str) -> DeviceCredential: ...
    # CLI 轮询调用，用私钥签名证明持有权

# localagent/devices.py
async def list_devices(ctx: RequestContext) -> list[Device]: ...
async def revoke(ctx: RequestContext, device_id: str) -> None: ...
    # 吊销凭据 + 断开在线连接 + 发布 route 通道的失效通知

# localagent/tickets.py
async def issue_ticket(*, device_credential: str) -> Ticket: ...
    # TTL 60s，一次性（nonce 落 Redis，消费即删）

# localagent/registry.py  ★ Redis，非进程内
async def mark_online(*, org_unit_id: str, user_id: str, device_id: str, instance_id: str,
                      capabilities: list[str]) -> None: ...
async def mark_offline(*, org_unit_id: str, user_id: str, device_id: str) -> None: ...
async def online_devices(org_unit_id: str, user_id: str) -> list[OnlineDevice]: ...

# localagent/router.py  ★ 跨实例投递
async def dispatch(*, org_unit_id: str, user_id: str, device_id: str | None,
                   frame: OutboundFrame, timeout_ms: int) -> InboundFrame: ...
    # 多设备且未绑定 → AmbiguousDeviceError（报错要求选设备，不猜）
```

### 4.6 knowledge

```python
# knowledge/tokenizer.py
def tokenize(text: str) -> list[str]: ...              # jieba 主路径
def bigrams(text: str) -> list[str]: ...               # 短查询回退

# knowledge/indexer.py
async def index_document(doc_id: str) -> IndexResult: ...
async def rebuild_df(org_unit_id: str) -> None: ...

# knowledge/retriever.py  ★ 阶段 5 换实现不改调用方
def search(query: str, *, filters: SearchFilters, top_k: int) -> list[Hit]: ...
```

### 4.7 governance

```python
# governance/approval.py
async def request(ctx: RequestContext, *, conversation_id: str, tool_name: str,
                  args: dict[str, Any], call_id: str, timeout_seconds: int) -> ApprovalDecision: ...
    # 阻塞等待，超时抛 ApprovalTimeout（勿静默放行）
async def decide(ctx: RequestContext, approval_id: str, *, approved: bool, reason: str) -> None: ...

# governance/audit.py
async def record(event: AuditEvent) -> None: ...
async def query(ctx: RequestContext, *, filters: AuditFilters, page: Page) -> PagedResult[AuditRecord]: ...

# governance/quota.py
async def charge_model_tokens(ctx: RequestContext, *, session_id: str, prompt: int, completion: int) -> None: ...
    # 在 model gateway 入口统一扣减；超额抛 QuotaExceeded
async def charge_tool_call(ctx: RequestContext, *, tool_name: str) -> None: ...
```

### 4.8 branding

```python
# branding/settings.py
class BrandingSettings(BaseModel):        # extra="forbid"
    display_name: str                     # 客户公司全称（UI 主标题、浏览器 title）
    short_name: str                       # 简称（侧边栏、窄屏）
    theme: ThemeColors                    # primary / accent，仅 #RRGGBB
    vendor_badge_enabled: bool = True     # 页脚"由 ZY-Agents 提供技术支持"，默认开
    # 阶段 2 追加：login_title / login_welcome / footer_copyright / help_url / support_contact

async def load() -> BrandingSettings: ...          # 进程内缓存，带失效订阅（同 Profile 失效通道）
async def update(ctx: RequestContext, patch: BrandingPatch) -> BrandingSettings: ...
    # 需 brand.manage 权限；写库后发布失效事件；不要重启容器

async def seed_if_empty(source: Path | None) -> bool: ...
    # ★ 幂等：仅当集合为空时写入（来自 BRANDING_SEED_FILE）
    #   非空则跳过——否则每次重启都会覆盖实施人员在后台的修改

# branding/assets.py
async def put_asset(ctx: RequestContext, *, slot: Literal["logo_wide", "logo_square", "favicon"],
                    upload: UploadFile) -> AssetRef: ...
    # ★ 类型按内容嗅探（不信任扩展名）、像素与字节上限、文件名服务端生成
async def delete_asset(ctx: RequestContext, *, slot: str) -> None: ...

# branding/public_view.py
def public_payload(s: BrandingSettings) -> dict[str, Any]: ...
    # ★ 白名单：显式列出可外露字段。新增字段必须显式加进来——默认不外露
```

**两条不可省的约束**（对应 `S-62` / `S-63`）：

- 前端**不硬编码**任何产品名 / 公司名 / logo，一律走 `GET /api/v1/branding`；
- `public_payload()` 采用**显式白名单**（而非黑名单）——以后加了内部开关字段，默认不会被外露。

---

## 5. 应用装配

### 5.1 `main.py` 启动顺序

```
1. settings = Settings()                       # 缺关键配置直接 fail fast
2. 构建 FastAPI(app, lifespan=lifespan)
3. 注册中间件（顺序敏感）
     trace_id → 异常映射 → CORS（仅开发）
4. 注册路由
     公开路由    api/v1/*            （identity 鉴权）
     内部路由    internal/kernel/*   （HMAC 鉴权，独立 middleware）
     本地 Agent  localagent/connect  （ticket 鉴权，WS）
5. lifespan.startup
     a. mongo.connect() + ensure_indexes()
     b. redis.connect()
     c. capability.registry.bootstrap()   # 注册服务端工具
     d. localagent.registry.start_subscriber()  # 订阅 route 通道
     e. branding.seed_if_empty(settings.branding_seed_file)  # ★ 幂等 seed，仅空库时写入
     f. kernel.dsh.gateway.health_check()       # 探测宿主；失败仅告警不阻塞启动
6. lifespan.shutdown
     a. 停止接受新 turn
     b. 等待进行中 turn 收尾（上限 30s）
     c. 释放 Redis 在线标记（避免幽灵设备）
     d. 关闭 mongo / redis
```

**关键点**：宿主不可用时**不阻塞启动**。企业私有化环境里，运维需要能看到界面并排查问题，而不是整个服务起不来。

### 5.2 中间件顺序

```
TraceIdMiddleware          → 生成/透传 trace_id，注入日志上下文
ErrorMappingMiddleware     → DomainError → HTTP status + 统一错误体
AuthMiddleware(公开路由)    → resolve_context → request.state.ctx
HmacAuthMiddleware(内部)    → credentials.verify → request.state.kernel_claims
```

内部路由的鉴权**不复用**公开路由的中间件——两条路径的凭据类型完全不同。

---

## 6. 配置与环境变量

`.env.example` 必须包含以下全部键（值用占位，真实值由 bootstrap 生成）：

```
# ---- 基础 ----
APP_ENV=production
APP_BASE_URL=https://zy-agents.example.internal
LOG_LEVEL=INFO

# ---- Mongo ----
MONGO_URI=mongodb://mongo:27017
MONGO_DB=zy_agents

# ---- Redis ----
REDIS_URL=redis://redis:6379/0

# ---- 登录态 ----
JWT_SIGNING_SECRET=                      # bootstrap 生成
JWT_ISSUER=zy-agents
ACCESS_TOKEN_TTL_SECONDS=3600

# ---- 内核宿主 ----
KERNEL_HOST_URL=http://kernel:8101
KERNEL_HOST_TOKEN=                       # bootstrap 生成，宿主与主服务共享
KERNEL_HEALTH_TIMEOUT_MS=3000

# ---- 双网关自指（下发给内核的回调地址）----
KERNEL_MODEL_GATEWAY_URL=http://api:8000/internal/kernel/model/generate
KERNEL_TOOL_GATEWAY_URL=http://api:8000/internal/kernel/tools

# ---- 双网关签名密钥（域分离：工具网关内部自动加 "tool:" 前缀）----
KERNEL_MODEL_GATEWAY_SECRET=             # bootstrap 生成
KERNEL_TOOL_GATEWAY_SECRET=              # bootstrap 生成
GATEWAY_TOKEN_TTL_SECONDS=900

# ---- 文件与签名 URL ----
STORAGE_ROOT=/data/storage
ARTIFACT_URL_SECRET=                     # bootstrap 生成
ARTIFACT_URL_TTL_SECONDS=3600

# ---- 本地 Agent ----
LOCALAGENT_ENABLED=true
LOCALAGENT_MINIMUM_VERSION=0.1.0
LOCALAGENT_TICKET_TTL_SECONDS=60
LOCALAGENT_ACTION_TIMEOUT_MS=120000

# ---- 知识库 ----
KNOWLEDGE_ENABLED=false                  # 阶段 2 打开
KNOWLEDGE_BIGRAM_FALLBACK_MAX_QUERY_CHARS=4

# ---- 配额（默认值，可按组织单元覆盖）----
DEFAULT_MODEL_TOKEN_QUOTA_MONTHLY=0      # 0 = 不限

# ---- 品牌与白标（00 §12）----
BRANDING_SEED_FILE=                      # 可选：部署预置品牌文件（YAML/JSON）；仅空库时 seed，幂等
BRANDING_ASSET_MAX_BYTES=1048576         # 单个 logo / favicon 字节上限（1 MiB）
BRANDING_ASSET_MAX_PIXELS=4096           # 单边像素上限（防解压炸弹）
```

**纪律**：`.env.example` 里出现的键必须在 `Settings` 里有对应字段并写默认值；`Settings` 里的字段必须有 `.env.example` 条目。CI 校验两边一致（`scripts/check_settings_sync.py`）。

---

## 7. 容器与编排

### 7.1 阶段 1（6 容器）

| 服务 | 镜像 | 端口 | 卷 |
|---|---|---|---|
| `nginx` | nginx:1.27-alpine | 3000:80 | 静态只读挂载 |
| `web` | 构建产物（nginx 内置静态或独立静态镜像） | 静态 | — |
| `api` | zy-agents/api:local | 8000 | `storage-data:/data/storage` |
| `kernel` | zy-agents/kernel-host:local | 8101 | `kernel-sessions:/data/sessions` |
| `mongo` | mongo:7.0 | 27017 | `mongo-data:/data/db` |
| `redis` | redis:7.4-alpine | 6379 | `redis-data:/data` |

**阶段 2 只加一个** `doc-worker`（Celery worker，复用 `api` 镜像，改启动命令）。
**阶段 3 服务端拓扑不增加**——浏览器能力全在客户端。

### 7.2 编排强制项

```yaml
# 每个服务必须有
deploy:
  resources:
    limits: { memory: <明确值>, cpus: "<明确值>" }
healthcheck:
  test: ...
  interval: 10s
  timeout: 5s
  retries: 20
  start_period: 30s      # 首次启动要给足（api 需等 mongo）
restart: unless-stopped
```

`mem_limit` / `cpus` **不是可选项**。私有化交付最常见的现场故障就是某个容器把宿主机内存吃满，运维无从判断是哪一层。

### 7.3 nginx 关键配置

```nginx
proxy_buffering off;          # SSE / WS 必须
proxy_read_timeout 600s;
proxy_send_timeout 600s;

location /api/          { proxy_pass http://api:8000/; }
location /internal/     { deny all; }              # ★ 内部端点严禁外露
location /localagent/   { proxy_pass http://api:8000/localagent/;
                          proxy_http_version 1.1;
                          proxy_set_header Upgrade $http_upgrade;
                          proxy_set_header Connection "upgrade"; }
location /              { proxy_pass http://web:80/; }
```

**`/internal/` 必须显式 deny**：这三个端点是内核回调入口，走 HMAC 鉴权，但绝不能暴露在公网入口上。双重防线。

---

## 8. 前端页面清单

### 8.1 用户端（`apps/web`）

| 路由 | 页面 | 关键行为 |
|---|---|---|
| `/login` | 登录 | ★ **未登录即显示客户品牌**（名称 + logo + 主题色），数据来自 `GET /api/v1/branding`（公开端点） |
| `/conversations` | 会话列表 | 分组：我参与的 / 我创建的 |
| `/conversations/:id` | **会话主界面** | 消息流 + 事件渲染 + 参与者栏 + 设备状态 |
| `/conversations/:id/participants` | 参与者管理 | 仅 owner 可见操作 |
| `/knowledge` | 知识库（阶段 2） | 文档列表、上传、检索 |
| `/devices` | 我的设备 | 配对引导、设备列表、版本、最后在线、解绑 |
| `/settings` | 个人设置 | — |

**品牌注入的三处落点**（`apps/web/src/branding/`）：文档 `<title>`、favicon、CSS 变量（`--brand-primary` / `--brand-accent`）。**三处都从同一个品牌 store 取值**，不允许各自去读接口——否则换品牌会出现"标题变了、按钮没变"。

**页脚**：`vendor_badge_enabled` 为真时显示"由 ZY-Agents 提供技术支持"（`00` §12.5）。这是唯一允许出现厂商标识的位置。

**会话主界面的三类特殊态必须显式渲染**（这是 CLI 形态下的"感知补偿"）：

```
tool_progress   → 工具执行进度条（翻页、上传等长动作的中间态）
auth_required   → 醒目提示卡片，含 device_display_name：
                  "请在【办公机-01】上完成登录" + 跳转 URL
local_agent_offline → 提示设备离线 + 最后一次在线时间 + 引导安装
```

### 8.2 管理端（`apps/admin`）

| 路由 | 页面 | 副作用 |
|---|---|---|
| `/org-units` | 组织单元 | — |
| `/members` | 成员与角色 | — |
| `/models` | 模型提供商与实例 | ★ 写入即失效相关 Profile 缓存 |
| `/skills` | Skill 与工具 | ★ 含 `category`（server/local/hybrid）与审批开关 |
| `/devices` | 设备台账 | 吊销 |
| `/audit` | 审计查询 | — |
| `/quota` | 配额 | — |
| `/branding` | **品牌设置** | ★ **不影响任何 Profile 缓存**——改品牌不使会话失效（`00` §12.4），这是与 `/models`、`/skills` 的关键区别 |

### 8.3 明确不做的事

- ❌ 前端**绝不**做事件脱敏（后端唯一的 `projection.py` 负责）
- ❌ 前端**绝不**直连本地 Agent（一律经主服务中转，便于审计）
- ❌ 前端**不持有**设备凭据
- ❌ 不做 workflow 画布

---

## 9. 分期交付定义（工程视角）

架构文档 §11 给了产品视角的验收标准，这里补工程视角的"什么叫做完"。

| 阶段 | 工程交付 | 完成定义（DoD） |
|---|---|---|
| **1. 内核闭环** | 6 容器可起；`kernel/` 全套；SSE 对话；1 个 server 工具；审计落库；**品牌基础**（集合 + 公开端点 + 管理页 + bootstrap seed + 前端三处注入 + 模型网关运行时上下文注入） | ① `make up` 后 6 容器全 healthy ② 一次提问全链路跑通，事件可从 Mongo 回放 ③ `check_layering.py` 通过 ④ 契约冻结校验通过 ⑤ 杀掉 kernel 容器后 api 不崩、能明确报错 ⑥ 改公司名与 logo 后：登录页立即变化，且**已有会话仍可续**（`profile_version` 不变）⑦ 集合非空时重跑 seed **不覆盖**后台已改的品牌 |
| **2. 企业能力** | doc-worker；知识库（分词+倒排+BM25）；ACL；配额；审批；管理端；**品牌文案覆盖**（登录页文案 / 页脚版权行 / 帮助与支持链接） | ① 两个角色用户看到不同模型/知识/工具集 ② 中文查询命中并给出行号级位置 ③ 超额调用被拒并留审计 ④ 审批超时不放行 ⑤ 改文案在后台完成，**未触发镜像重新构建** |
| **3. 协同 + 本地执行** | 参与者模型；分级投影；文件 ACL；CLI 本地 Agent 最小闭环 | ① viewer 的 SSE 流里**不含**工具参数与文件路径（网络层验证，非前端） ② 本地 Agent 操作内网系统并核验成功 ③ 断网时返回 `local_agent_offline` 而非静默失败 ④ 同一 profile 的 headless/headed 切换走进程重启 |
| **4. 规模与升级** | Redis 注册表与跨实例路由；升级工具链 + CI；SBOM | ① Agent 连 A 实例、turn 跑 B 实例仍正常 ② 一次完整内核升级演练通过并可一键回滚 |
| **5. 检索升级** | 向量召回 + 融合 + rerank | ① 同一 query 语义召回明显优于纯全文 ② `retriever.py` 调用方零改动 |

---

## 10. 未决项

| # | 事项 | 影响 | 建议 |
|---|---|---|---|
| 1 | ~~**产品命名**~~ **已决** | 5 个契约命名空间、容器名、镜像名、Python 包名、CLI 命令名 | ✅ `2026-09-29` 定为 **`ZY-Agents`**（对外简写 `ZY`）；9 类标识映射见 `00` §0.5 / `README.md` §命名与标识 |
| 2 | **本地 Agent 目标平台** | 打包与测试矩阵 | 先只做 Windows；一开始就留好平台抽象 |
| 3 | 托盘常驻与开机自启 | 用户感知 | CLI 阶段不做，现场抱怨再加 |
| 4 | 人工录制 → Skill | 协议已预留 `recording_event` | 阶段 3 之后单独评估 |
| 5 | 向量库选型 | 阶段 5 | 阶段 5 前定 |
| 6 | 多实例下 kernel 会话 JSONL | 共享卷 or 亲和路由 | 阶段 4 定；阶段 1 先留 `runtime_id` 到实例的映射接口 |
| 7 | 项目级长期记忆 | 产品形态 | 取决于"跨会话记忆"是否是核心卖点 |
| 8 | **阶段 3 是否提前** | 若核心场景就是内网自动化，本地 Agent 应提到阶段 1 之后立即做 | 由业务判断，非技术判断 |
