# 03 · 内核契约、双网关与版本锁

> 对应 `00-architecture-spec.md` §3（Runtime Profile）、§4（双网关）、§10（升级机制）、§12.4（品牌不进 Profile）。
> 内核版本：**`0.2.0-rc.1`**（决策确认）。本文第 7 节是**实测记录**，不是推断。
> **本文实现的 spec 条款**见 `05-spec-compliance.md`（主要覆盖 S-09~S-17、S-52~S-56、S-59、S-60）——本文与 `00` 冲突时以 `00` 为准。

---

## 1. 契约冻结策略

冻结的必须是**自己的**契约，不是 DSH 的。

| 冻结项 | 内容 | 冻结起点 |
|---|---|---|
| `zy.agent-kernel.v1` | 命令与句柄模型 | 阶段 1 |
| `zy.kernel-event.v1` | 事件 envelope | 阶段 1 |
| `zy.dsh-host.v1` | 主服务 ↔ Node 宿主 HTTP 协议 | 阶段 1 |
| `zy.localagent.v1` | 本地 Agent 桥协议（见 04 文档） | 阶段 3 |
| `zy.runtime-profile.v1` | Profile schema | 阶段 1 |

**规则**：
- 上游 API 变化时，**改 `kernel/dsh/adapters/`，不改契约**。
- 契约只增不改：新增字段/方法允许（向后兼容），删字段/改类型必须升 `schema_version`。
- 由 `scripts/check_contract_freeze.py` 在 CI 强制。

---

## 2. 契约层定义（`kernel/contract/`）

### 2.1 版本常量

```python
# kernel/contract/versions.py
AGENT_KERNEL_CONTRACT_VERSION = "zy.agent-kernel.v1"
KERNEL_EVENT_VERSION = "zy.kernel-event.v1"
RUNTIME_PROFILE_SCHEMA_VERSION = "zy.runtime-profile.v1"
DSH_HOST_PROTOCOL_VERSION = "zy.dsh-host.v1"
```

### 2.2 基础模型

所有跨内核边界的值继承同一个严格基类——**不可变 + 拒绝多余字段**：

```python
class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
```

`extra="forbid"` 是刻意的：内核升级后若上游多发一个字段，**这里会炸**，而不是静默忽略。
在"内核会被换掉"的架构里，静默忽略比报错危险得多。

### 2.3 命令与句柄

```python
class SessionStatus(str, Enum):
    CREATING = "creating";  IDLE = "idle";       RUNNING = "running"
    WAITING = "waiting";    CANCELLING = "cancelling"
    FAILED = "failed";      DISPOSED = "disposed"

class SendMode(str, Enum):
    PROMPT = "prompt"       # 新输入
    FOLLOWUP = "followup"   # 续接
    STEER = "steer"         # 打断并改向

class CreateRuntimeRequest(ContractModel):
    schema_version: Literal["zy.agent-kernel.v1"] = AGENT_KERNEL_CONTRACT_VERSION
    org_unit_id: str            # ★ 组织单元维度（不引入独立 tenant 概念）
    profile_version: str
    isolation_key: str          # "{org_unit_id}:{user_id}"

class RuntimeHandle(ContractModel):
    schema_version; runtime_id; kernel: Literal["dsh"]; kernel_version
    profile_version; model_instance_id | None; isolation_key

class SessionSpec(ContractModel):
    conversation_id; org_unit_id; user_id
    profile_version
    preset_id: str = "zy-enterprise"          # 自定名（不复用参照实现的 preset id）
    execution_location: Literal["server"] = "server"
    metadata: dict[str, Any] = {}

class CreateSessionRequest(ContractModel):
    schema_version; runtime_id; session_spec: SessionSpec

class SessionHandle(ContractModel):
    runtime_id; session_id; conversation_id; profile_version
    preset_id; status: SessionStatus

class ContentBlock(ContractModel):
    type: Literal["text", "image", "document", "artifact_ref"]
    data: dict[str, Any]

class TemporalContext(ContractModel):
    """服务端可信时钟快照。内核不自己取时间。"""
    captured_at_utc: datetime       # 必须 tz-aware
    user_local_time: datetime       # 必须 tz-aware
    user_timezone: str              # 必须是合法 IANA 时区

class SendRequest(ContractModel):
    schema_version; session_id; request_id
    mode: SendMode = SendMode.PROMPT
    content: list[ContentBlock]     # min_length=1
    temporal_context: TemporalContext | None
    turn_context: dict[str, Any] = {}

class CancelSessionRequest(ContractModel):
    schema_version; session_id; cause: str

class KernelError(ContractModel):
    schema_version
    code: str          # pattern ^[a-z][a-z0-9_.-]*$
    message: str
    retryable: bool = False
    details: dict[str, Any] = {}
```

**保留的设计**：
- `TemporalContext` 由服务端注入 —— 让"现在几点"成为服务端事实，而不是内核进程的本地时间。多人跨时区协同时这是正确性的前提。
- `SessionSpec.preset_id` 的命名空间是自己的（`zy-enterprise`），不复用参照实现的 preset id。

### 2.4 事件 envelope

```python
class KernelEventSource(ContractModel):
    kernel: Literal["dsh"] = "dsh"
    kernel_version: str
    native_event_type: str | None   # ★ 上游原始类型名，只为排查保留

class KernelEventEnvelope(ContractModel):
    schema_version: Literal["zy.kernel-event.v1"]
    event_id: str
    runtime_id: str
    session_id: str
    parent_session_id: str | None
    profile_version: str
    cursor: int                      # ge=0，会话内单调递增
    type: str                        # 归一化后的事件类型（自己定义）
    occurred_at: datetime            # 必须 tz-aware
    payload: dict[str, Any]
    source: KernelEventSource
```

`source.native_event_type` 是**唯一**允许把上游类型名带进产品层的位置，且只用于排查。
产品逻辑与前端**永远**只看 `type`——这就是"上游事件变化不波及上层"的实现方式。

### 2.5 Protocol

```python
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
```

DSH 只是其中一个实现（`kernel/dsh/gateway.py`）。换内核 = 换实现，契约不动。

---

## 3. Runtime Profile

### 3.1 编译链路

```
admin 数据（可变）
  模型实例 + 工具定义 + Skill 定义 + 写作风格 + 用户的静态工具授权
        │
        ▼  compile_profile(org_unit_id, user_id)   # kernel/profile/compiler.py
  规范化 JSON（ensure_ascii=False, sort_keys=True, separators=(",",":")）→ sha256
        │
        ▼
  profile_version = "rp-" + sha256[:24]
        │
        ▼  build_host_payload(...)   # ★ 唯一注入密钥处
  加上 gatewayUrl / accessToken / toolGatewayUrl / toolAccessToken
        │
        ▼
  下发宿主（PUT /v1/runtimes/{id}/model-credential + /tool-credential）
```

**规范化参数必须逐字一致**，否则同输入得到不同 hash，整个"内容寻址"失效：

```python
canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

### 3.2 四条不变量

1. **Profile 不含任何密钥。** 快照只有 `provider_type` / `model_name` / `context_window` 这类元数据；`api_key` 与网关 token 只在 `build_host_payload()` 时拼接。
2. **同输入必得同 `profile_version`。** 配置没变 → 版本号不变 → 已建会话仍可 resume；配置一变，版本号必变，**旧会话自动不可续**（明示报错，不静默降级）。
3. **Profile 是每人一份。** `subject_user_id` 参与编译。多人协同的会话绑定**发起者的 profile**，参与者不改变它。
4. **编译输入不含任何运行时易变状态。** 尤其：`local` 类工具是否入 Profile，**只由静态授权决定**，与"本地 Agent 此刻是否在线"无关。否则连上/断开都会改版本号，会话被迫中断。

### 3.3 Profile 字段

```
schema_version       "zy.runtime-profile.v1"
profile_version      "rp-{sha256[:24]}"
content_hash         全量 sha256
org_unit_id
subject_user_id      编译主体
model_instance_id / provider_id / provider_type / provider_name
model_name / display_name / capabilities[]
context_window / max_output_tokens
tools[]              工具定义（name, description, input_schema, output_schema,
                     category, risk_level, approval_required, timeout_ms）
tool_versions[]
skills[]             kind: skill | workflow | writing_style
skill_versions[] / workflow_versions[]
```

`tools[].category`（`server` / `local` / `hybrid`）是本地执行层的接线点。

> **品牌不进 Profile（对应 `00` §12.4 / `S-60`）**：上面这个 schema **不包含**任何品牌字段。品牌名这类"当下的信息"由模型网关在每次请求构造时以 turn 级运行时上下文注入（见 §4.1），不进内容寻址、不影响 `profile_version`。

### 3.4 Skill 的编译约束

Skill 不是"塞进 prompt 的一段文本"，而是**编译进 Profile 的结构化定义**：

- 可用性由**编译期**决定，运行时无法越权加载；
- Skill 版本进入 `profile_version`，会话与 Skill 版本一一对应，可复现；
- Skill 声明 `requires_tools`，编译器校验依赖是否在此 Profile 内——**缺依赖直接编译失败**，而不是运行时才发现。

### 3.5 Profile 缓存与失效

```
写入 model_providers / model_instances / tools / skills / resource_grants
        ↓
发布失效事件（Redis channel: profile:invalidate:{org_unit_id}）
        ↓
各实例清理本地缓存；下次 compile_profile 重算，hash 自然变化
```

**不要用"手写失效清单"**——漏一个写入口就会留下陈旧 Profile。

---

## 4. 双网关反向回调

### 4.1 端点

| 端点 | 方法 | 用途 |
|---|---|---|
| `/internal/kernel/model/generate` | POST | 模型推理（流式 NDJSON） |
| `/internal/kernel/tools/execute` | POST | 工具执行（server 直执行 / local 转发） |
| `/internal/kernel/tools/approval` | POST | 审批请求（人在环） |

独立前缀 + 独立鉴权中间件；nginx 层 `location /internal/ { deny all; }`。

**模型网关的 turn 级运行时上下文注入**（对应 `00` §12.4 / `S-60`）：当内核经 `/internal/kernel/model/generate` 回调请求推理时，主服务在转发给模型提供商**之前**，读取 `branding_settings`，把品牌信息拼成一段独立的系统上下文块注入本次请求。要点：

- 它**不进入 Runtime Profile 的内容寻址**，因此不影响 `profile_version`，也不影响已有会话的可续性；
- 它**随每次 turn 重新读取**，客户改了公司名，下一个 turn 即生效，无需重启、无需失效 Profile 缓存；
- `branding` 模块对 `kernel/` 的依赖方向与 `collaboration/` 一致：只允许 `kernel/branding/` 或经 `kernel/inbound/` 调用，**不破坏分层**（与 `S-06` 同口径）。

### 4.2 HMAC 短时 token

```python
# kernel/inbound/credentials.py
class GatewayClaims:          # aud 区分用途
    org_unit_id; user_id; profile_version
    tool_names: frozenset[str]; scopes: frozenset[str]
    issued_at; expires_at

def issue(*, aud: Literal["model", "tool"], ...) -> str: ...
def verify(token: str, *, expect_aud: str) -> GatewayClaims: ...
```

**密钥派生做域分离**（已验证的稳妥做法）：

```python
model_key = sha256(secret.encode()).digest()              # 模型网关
tool_key  = sha256(("tool:" + secret).encode()).digest()   # 工具网关
```

这样即使 model token 被拿到，也**不可能**用来调工具端点——不是靠代码检查，是靠密码学。

Token 载荷必须含 `aud`（`zy-model-gateway` / `zy-tool-gateway`），校验时**同时比对 `aud`**。

有效期：**900 秒**，turn 开始前通过 `PUT /v1/runtimes/{id}/{model,tool}-credential` 下发，turn 结束后作废。

### 4.3 工具执行的完整路径

```
内核 loop 决定调用工具
  → POST /internal/kernel/tools/execute        (tool token)
      → 准入：Profile 里是否声明该工具？用户是否有资源权限？
      → 需要审批？（工具定义 approval_required）
          是 → 创建审批单 → SSE 推给人 → 阻塞等待（带超时）
      → 按 category 分派：
          server → capability/tools/ 直接执行
          local  → localagent/router 转发到用户机器，等待回执
          hybrid → 先服务端尝试；失败或需渲染再转 local
      → 效果核验（见 04 文档 §8）
      → 审计落库（参数、结果、耗时、审批人、执行位置、设备）
  → 返回 { ok, result | error, receipt }
```

**`local` 工具不可用时的语义**：
返回结构化错误 `local_agent_offline`（含设备名与最后一次在线时间），由 Agent 用**它自己的话**转述给用户。
**不要静默失败，也不要假装成功。**

### 4.4 三个端点不暴露在公开路由

| 内核直连模型 | 反向回调（采用） |
|---|---|
| API Key 落在内核配置，进程可被读 | Key 只在主服务内存 |
| 配额/限流要在内核实现 | 宿主实现一次，所有内核版本通用 |
| 换模型要改内核配置并重启 | 改 Profile，下次 turn 生效 |
| 审计散在内核 | 统一在主服务落库 |
| 换内核 = 重写模型接入 | 契约不变，换适配器 |

---

## 5. 宿主 HTTP 协议（`zy.dsh-host.v1`）

主服务 ↔ `services/kernel-host`。全部鉴权走 `Authorization: Bearer <KERNEL_HOST_TOKEN>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | `{ok:true, kernel:"dsh", version, protocolVersion, runtimes[]}` |
| POST | `/v1/runtimes` | 创建 runtime（含 profile 快照） |
| GET | `/v1/runtimes` | 列表 |
| GET | `/v1/runtimes/{id}` | 描述 |
| DELETE | `/v1/runtimes/{id}` | 释放 |
| PUT | `/v1/runtimes/{id}/model-credential` | ★ 下发模型网关地址与 token |
| PUT | `/v1/runtimes/{id}/tool-credential` | ★ 下发工具网关地址与 token |
| POST | `/v1/runtimes/{id}/sessions` | 建会话 |
| GET | `/v1/runtimes/{id}/sessions/{sid}` | 会话状态 |
| POST | `/v1/runtimes/{id}/sessions/{sid}/resume` | 恢复 |
| POST | `/v1/runtimes/{id}/sessions/{sid}/send` | 发起 turn |
| POST | `/v1/runtimes/{id}/sessions/{sid}/cancel` | 取消 |
| GET | `/v1/runtimes/{id}/sessions/{sid}/events` | 历史事件（JSON） |
| GET | `/v1/runtimes/{id}/sessions/{sid}/event-stream` | ★ **NDJSON 流**，turn 期间持续推送 |
| GET | `/v1/runtimes/{id}/sessions/{sid}/approvals` | 待决审批 |
| POST | `/v1/runtimes/{id}/sessions/{sid}/approvals/{aid}/decision` | 提交决策 |
| DELETE | `/v1/runtimes/{id}/sessions/{sid}` | 释放会话 |

**传输格式**：NDJSON（一行一个 JSON 对象），不是 RPC。
主服务侧 `transport.py` 按行解析、逐条归一化后投影事件流，再分发给前端 SSE。

**启动握手**：宿主启动后往 stdout 打一行

```json
{"type":"zy-dsh-runtime-ready","host":"127.0.0.1","port":8101,"kernel":"dsh",
 "kernelVersion":"0.2.0-rc.1","protocolVersion":"zy.dsh-host.v1","pid":12345}
```

主服务据此确认宿主就绪（也可只用 `/health` 轮询）。

**鉴权 token 传递**：优先用 `--auth-token-file`（读后即删），其次 `KERNEL_HOST_TOKEN` 环境变量。
文件方式优于环境变量：环境变量会出现在 `/proc/*/environ`、容器 inspect、崩溃转储里。

---

## 6. Node 宿主装配（`kernel-host`）

宿主是**无状态**的（除会话 JSONL 落盘），可水平复制。

```
host.mjs                 解析参数 → 起 HTTP 服务 → 打印 ready 事件 → 处理 SIGTERM
  └─ http-server.mjs     路由 + 宿主 token 校验
      └─ runtime-manager.mjs    runtime 台账（Map<runtimeId, KernelRuntime>）
          └─ kernel-runtime.mjs ★ cordis 装配 DSH
              ├─ llm-register：ctx.llm.registerAdapter([provider], adapter)
              ├─ tools-register：ctx.tools.register(definition) × N
              ├─ event-journal.mjs   事件落 JSONL
              └─ approval-broker.mjs 审批桥
```

### 6.1 两个适配器

**模型适配器**（`model-adapter.mjs`）：

```js
import { LlmAdapter, attributionHeaders } from '@deepseek-ai/dsh-llm'

class GatewayLlmAdapter extends LlmAdapter {
  // 必须实现
  async *stream(options) { /* POST gatewayUrl，解析 NDJSON chunk 流 */ }

  // 0.2.0 起需要提供（有默认实现，但配置型适配器应覆盖）
  providerInfo(provider) { ... }
  async listModels(provider) { ... }
  async resolveModel(provider, model) { ... }
}
```

★ **每个 provider HTTP 请求必须带 `attributionHeaders()`** —— 0.2.0 的硬性文档要求（见 §7.2）。

**工具桥**（`tool-bridge.mjs`）：为 Profile 里每个工具 `ctx.tools.register({...})`，`execute` 内 `POST` 工具网关。

### 6.2 工具定义形状（0.2.0 实测）

```js
ctx.tools.register({
  name,                                   // string
  description,                            // string
  parameters,                             // JSON Schema（字段名就是 parameters）
  output: {                               // ★ 0.2.0 标注为 mandatory
    schema,                               //   输出 JSON Schema
    render: (args, value) => [{ type: 'text', text: JSON.stringify(value) }],
  },
  timeoutMs,                              // 可选，协作式超时
  isConcurrencySafe: (args) => riskLevel === 'read',   // 仅 true 才并行
  execute: async (args, exec) => { ... },  // 返回 canonical JSON 值
})
```

**关键差异（0.1.6 与 0.2.0-rc.1 的写法对比）**：

| 项 | 0.1.6 | 0.2.0-rc.1 实测 |
|---|---|---|
| 工具名冲突防护 | 自写 `tool-name-policy.mjs` 手工查重名，命中即 `throw` | ★ 内建 `ToolRestriction { allow, deny }`，按 `ScopeKey` 过滤 |
| 拒绝语义 | 自写 `tools/pre-execute` 钩子 | ★ 内建 `ToolGuard`：**没有 allow 结果**，故监听器顺序无法把拒绝翻回允许（fail-closed） |
| 工具呈现 | 无 | 新增 `presentCall` / `presentResult`；`mode: native \| ptc \| both` |

**结论**：自建的 `tool-name-policy` 可以**大幅简化**——保留"启动时检测重名并告警"，把过滤本身交给 `ToolRestriction`，把审批拒绝交给 `ToolGuard`。

---

## 7. 0.2.0-rc.1 核实记录（实测）

> 方法：`npm view` 核实版本存在性；`npm pack` 拉取 tarball 后直接读 `lib/types/*.d.ts` 与 `lib/index.js`。
> **不是**从任何适配层代码推断，全部以实测为准。

### 7.1 包可用性（实测）

| 项 | 实测结果 |
|---|---|
| 17 个 `@deepseek-ai/dsh*` 包 | ✅ `0.2.0-rc.1` 全部存在 |
| `@deepseek-ai/cordis` | ⚠️ **不在这条版本列车上**；独立版本，最新 `4.0.4`（0.1.6 时代用的是 4.0.2） |
| `@deepseek-ai/cordis-plugin-timer` | ⚠️ 同上；最新 `1.1.6`（0.1.6 时代用的是 1.1.4） |
| npm dist-tags | `{alpha: 0.1.7-alpha.2, latest: 0.1.7-rc.2, next: 0.2.0-rc.1}` |

**修正说明**：此前把"19 个包同版本列车"作为描述是不准确的。准确表述是：
**17 个 `dsh*` 包同列车 + 2 个 `cordis*` 包独立版本 + `fflate`**。
锁文件必须把这两类分开处理——`cordis` **不能**跟着 dsh 版本走。

### 7.2 扩展点核实

| 扩展点 | 结论 | 证据 |
|---|---|---|
| `LlmAdapter` | ✅ 存在，`abstract class` | `dsh-llm/lib/types/index.d.ts:130` |
| `LlmAdapter.stream()` | 唯一 `abstract` 方法 | 同上 |
| `LlmAdapter.providerInfo / providerRetryPolicy / imageRequestPricing / listModels / resolveModel / prepareCall` | ✅ 存在，**有默认实现**（可覆盖） | 同上 |
| 适配器注册方式 | ★ **`ctx.llm.registerAdapter(providers, adapter)`** | `index.d.ts:253` |
| `attributionHeaders()` | ✅ 导出：`(identity?) => Record<string,string>` | `dsh-llm/lib/types/attribution.d.ts:46` |
| `ToolRuntime.register(definition)` | ✅ 存在，返回 disposer | `dsh-tools/lib/types/index.d.ts:636` |
| `ToolDefinition.output` | ★ 标注为 **mandatory canonical output declaration** | `dsh-tools/.../index.d.ts:106-115` |
| `ToolSchema` 字段 | `{ deferLoading?, name, description, parameters }`；`parameters` 是 JSON Schema 对象 | `dsh-llm/lib/types/types.d.ts:455` |
| `isConcurrencySafe` | ✅ 同签名为 `(args) => boolean`；**仅 `true` 才并行**，省略/异常/非 true 均视为独占 | `dsh-tools/.../index.d.ts` |
| `validateJsonSchemaValue` | ✅ 导出 | `dsh-tools/lib/types/json-schema.d.ts:105` |
| `assembleContextFor` | ✅ 导出 | `dsh-agent/lib/types/dispatch.d.ts:109` |
| `SessionId` | ✅ 类型存在 | `dsh-session/lib/types/index.d.ts:123` |
| `scopeOf(ctx)` | ✅ 导出 | `dsh-scope/lib/types/index.d.ts:84` |
| cordis Context 增强 | ✅ `declare module '@deepseek-ai/cordis' { interface Context { llm: LlmRuntime } }` | `dsh-llm/lib/types/index.d.ts:28-30` |

### 7.3 三条必须写进设计的实测发现

**① `SESSION_FORMAT_VERSION = 4`**

```js
// dsh-session/lib/index.js:56
const SESSION_FORMAT_VERSION = 4;
```

0.1.6 线是 **V3**。所以：

- 我们**全新起步，不涉及迁移**——这是选 0.2.0-rc.1 的一个隐性收益；
- 那套现成的"旧版本建会话 → 候选版本恢复"升级探针**不能直接复用**：它针对的是 V3→V4，而我们的基线就是 V4，要防的是 V4→V5。
- `SessionHeader.version` 是 `typeof SESSION_FORMAT_VERSION`，且有"历史物理 header 在进入接口前被翻译"的机制——说明上游自己维护了物理格式兼容层。**这层不能被我们绕过**。

**② 读路径会拒绝未知事件类型（最高危）**

`dsh-session/lib/types/known-event-types.d.ts` 原文：

> The persistence read path **refuses to interpret** a log containing a type outside this set **unless the event carries the envelope's `ignorable` marker** … such a log was likely written by a newer harness, and silently skipping a required event would reconstruct a wrong session.

**对我们的直接约束**：宿主若注册自有插件并发出**自定义 session 事件**，必须：

1. 事件类型若不在 `KNOWN_SESSION_EVENT_TYPES` 内 → **必须**打 `ignorable` 标记；
2. 否则该会话日志会变成**不可读**——不是降级，是读不出来。

这条在阶段 1 就要建立检查：宿主启动时断言"我们发出的每个事件类型要么在 known 集合内，要么显式标 ignorable"。

**③ `ToolGuard` 是 fail-closed 的，审批应基于它**

> Because guards have **no allow result**, listener ordering cannot turn a denial back into permission.

这正好是审批语义需要的形状：**守卫只能说"不许"，不能说"允许"**。
所以审批链路应该是：`ToolGuard` 在 `tools/pre-execute` 之后、工具体之前做最终拒绝判定。顺序无法绕过。

---

## 8. 版本锁物料

```
services/api/kernel/
├── VERSIONS.lock          声明版本 + 每包 integrity（人可审计）
├── COMPATIBILITY.md       升级策略（人读）
├── compat-matrix.yaml     升级策略（机读，CI 逐字比对）
└── UPSTREAM_NOTICE.md     上游许可与 commit 记录
```

### 8.1 `VERSIONS.lock`（内容示意）

```yaml
dsh_train: 0.2.0-rc.1
rollback_train: 0.1.7-rc.2
admitted_at: 2026-09-29
policy:
  floating_versions: false
  mutate_installed_runtime_in_place: false
  dsh_core_fork: prohibited
packages:                     # 17 个，全部 == dsh_train
  "@deepseek-ai/dsh":            { version: 0.2.0-rc.1, integrity: sha512-... }
  "@deepseek-ai/dsh-agent":      { version: 0.2.0-rc.1, integrity: sha512-... }
  # …（其余 15 个）
independent:                  # ★ 不跟 dsh_train 走
  "@deepseek-ai/cordis":            { version: 4.0.4, integrity: sha512-... }
  "@deepseek-ai/cordis-plugin-timer": { version: 1.1.6, integrity: sha512-... }
  "fflate":                          { version: 0.8.3,  integrity: sha512-... }
source_mapping: unverified-by-upstream
```

### 8.2 `compat-matrix.yaml`

```yaml
floating_versions: false
mutate_installed_runtime_in_place: false
forbidden:
  - fork_dsh_core
  - patch_vendored_source
frozen_contracts:            # 改动即 CI 失败
  host_protocol: zy.dsh-host.v1
  kernel_contract: zy.agent-kernel.v1
  event_envelope: zy.kernel-event.v1
session:
  format_version: 4
  cross_version_probe_required: true
probe_schedule:
  cron: "23 3 * * 1"         # 每周一 03:23
```

CI 逐字比对：**改一个字就报 "policy changed without contract review"**。

### 8.3 供应链诚实性

上游 npm 元数据**不提供**源码到包的映射。所以：

- npm `integrity` 哈希 = **可部署产物身份**；
- 上游 git commit = **另一个独立审查的源码基线**；
- **不得声称二者有密码学对应关系。**

知道 gap 在哪并写下来，比假装闭环要靠谱。`UPSTREAM_NOTICE.md` 需记录：DSH 的 MIT 许可全文、上游 commit、`source_mapping: unverified-by-upstream`。

---

## 9. 升级工具链与 CI

```
services/api/scripts/kernel_upgrade/
├── evaluate.py             候选版本评估：隔离工作区 npm pack + 静态契约检查
├── checks/
│   ├── tool_names.py       上游新增原生工具名是否与自有工具冲突
│   ├── preset.py           preset 组合是否仍可装配
│   ├── session_migration.py  旧版本建会话 → 候选版本恢复 → 逐字段比对
│   ├── contract.py         冻结契约是否被动过
│   └── supply_chain.py     integrity / shasum 校验
└── generate_sbom.py        CycloneDX 清单
```

### 9.1 CI 工作流

| 工作流 | 触发 | 行为 |
|---|---|---|
| `ci.yml` | 每次 PR | lint / test / `check_layering.py` / `check_contract_freeze.py` / `check_settings_sync.py` |
| `kernel-upgrade.yml` | 每周一 03:23 + 手动 | 探测上游新版本 → 隔离工作区评估 → **只产出报告，不动线上** |
| 改动 `kernel/dsh/**` 的 PR | 自动 | 强制跑全部 check + 双端回归 |

**硬约束**：候选版本**只在隔离工作区评估**，禁止原地替换已安装运行时。

### 9.2 版本纪律

- 17 个 `dsh*` 包**精确 pin 同版本**，无 `^` 无 `~`；
- `cordis*` 与 `fflate` **单独 pin**，不走 dsh 列车；
- `floating_versions: false` 写死，CI 校验"任何 dsh 包版本不等于当前列车就失败"；
- 维护一个**回滚列车**版本号（当前建议 `0.1.7-rc.2`），升级失败可一键回退。

---

## 10. 升级必查清单（针对 0.2.0-rc.1 基线）

1. **工具名冲突** —— 上游新增原生工具是否撞名。
   0.2.0 已有 `ToolRestriction`，但**自有工具名与上游重名仍要避免**——重名会让 Profile 语义歧义。
2. **Preset 组合变化** —— 上游可能重命名或替换组合（0.1.6 就用 PTC runtime 替换了 Code runtime）。
3. ★ **Session 持久化格式（最高危）** —— 当前 V4。**可能单向迁移**。
   探针必须：用候选版本建会话 → 用当前版本恢复 → 逐字段比对；并验证历史保留、续轮完成。
4. ★ **自定义 session 事件的 `ignorable` 标记** —— 见 §7.3②。**这项必须是常驻检查，不是升级时才查。**
5. **事件字段增删** —— 适配器是否需要补映射。
6. **`attributionHeaders()` 要求是否加严** —— 是否从"文档要求"变成"运行时断言"。
7. **`ToolPresentationMode`（native / ptc / both）行为变化** —— PTC 模式下 `toolOrder` 里的 native 名是非法的，配置需跟着改。
8. **磁盘上已存在的会话能否正常恢复并续跑**。
9. **候选版本跑通镜像 smoke test** 后才允许发布固定版本。

**任何一项失败 → 维持当前版本，产品开发照常推进。**
**升级失败不阻塞产品**是这条流程能长期活着的前提。
