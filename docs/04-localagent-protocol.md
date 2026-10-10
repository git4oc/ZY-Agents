# 04 · 本地 Agent 协议规格

> 对应 `00-architecture-spec.md` §5。
> 协议命名空间：**`zy.localagent.v1`**（产品 `ZY-Agents`，定名日 `2026-09-29`）。
> 这是"不做服务端沙箱浏览器"之后**唯一**的浏览器能力入口——协议缺陷会直接变成产品缺陷。
> **本文实现的 spec 条款**见 `05-spec-compliance.md`（主要覆盖 S-18~S-30）——本文与 `00` 冲突时以 `00` 为准。

---

## 1. 形态与进程模型

| 项 | 决定 |
|---|---|
| 形态 | Node CLI + Playwright 管理的独立 Chromium |
| 分发 | 单文件可执行（Node SEA）+ `npm i -g` 两条路；私有化环境随交付物附带**离线安装包** |
| 运行身份 | 用户级后台进程（**非系统服务**，避免提权与合规问题） |
| 状态目录 | `%APPDATA%/zy/`（Windows）/ `~/.zy/`（macOS/Linux） |
| 密钥 | 私钥存 OS keychain（Windows Credential Manager / macOS Keychain / libsecret） |
| Chromium | Playwright 分发，**独立 `--user-data-dir`**，与用户日常浏览器完全隔离 |
| 目标平台 | 阶段 3 只做 Windows；平台抽象从第一天留出 |

**独立 profile 的必然后果**：不共享 Cookie → **既有登录态不会自动带上**。
不要试图读用户 Chrome 的 Cookie 文件——隐私与合规雷区，收益也不值。
"需要人登录"是必经流程，不是可选优化。

---

## 2. 身份体系

三层凭据，生命周期依次递减：

| 凭据 | 生成方 | 生命周期 | 存储位置 | 用途 |
|---|---|---|---|---|
| `device_keypair` | CLI | 长期（可重生） | OS keychain | 签名能力，不直接传输 |
| `device_credential` | 服务端签发 | 长期，可吊销 | OS keychain | 绑定 `user_id` + `device_id` |
| `ticket` | 服务端签发 | **60 秒，一次性** | 内存 | 建立 WS 连接的入场券 |

**为什么不让 CLI 直接带 `device_credential` 连 WS**：长连接会重连、会被代理/网关记录。用一个 60 秒一次性 ticket，即使被日志捕获也已失效。这是照抄"短时凭据交换长连接"的通用做法。

---

## 3. 配对流程（首次）

```
CLI 侧                                服务端                        用户 Web 端
────────────────────────────────────────────────────────────────────────────────
1. 生成 device_keypair
   私钥 → keychain
       │
       ├─ POST /api/v1/localagent/pairing/begin ──►
       │      {device_key_public,
       │       agent_version, os}
       │                            ◄── {pairing_id, code:"614203",
       │                                 expires_in_seconds:600}
2. 终端打印：
   "请在 Web 端 → 设置 → 我的设备 输入配对码：614203"
       │
       │  ┌─────────────────────────────── 轮询 ───────────────────────────────┐
       │  │ POST /api/v1/localagent/pairing/claim                                │
       │  │      {pairing_id, signature=sign(priv, pairing_id)}                  │
       │  │  ← 202 {status:"pending"}                                            │
       │  └──────────────────────────────────────────────────────────────────────┘
       │                                                          3. 用户输入 614203
       │                                                    POST .../pairing/confirm
       │                                                             {code}
       │                            ◄── 服务端校验 code_hash
       │                                绑定 user_id / org_unit_id
       │                                status → confirmed
       │  ◄── 200 {status:"claimed",
       │          device_credential:"dc_...",
       │          device_id, display_name}
4. 存 device_credential → keychain
   输出："配对成功，设备名【办公机-01】"
```

### 3.1 轮询与超时

- CLI 轮询间隔 **3 秒**，总时长 **10 分钟**（与配对码 TTL 一致），超时后退出并提示重新发起。
- 轮询用**指数退避**（3s → 3s → 5s → 5s → 8s…，上限 10s），避免 10 分钟内打 200 次请求。

### 3.2 配对码的安全要求

| 要求 | 理由 |
|---|---|
| 只存 `code_hash` | 数据库泄露不等于配对码泄露 |
| `attempts` 计数，**上限 5 次** | 6 位数字只有 100 万种，无限制尝试等于没有保护 |
| 超限 → `status: expired` + 需重新 begin | |
| `pairing_id` 与 `code` 分离 | `pairing_id` 可公开（CLI 持有），`code` 只在人眼通道传输 |
| TTL 索引自动清理 | 避免失败配对堆积 |

### 3.3 为什么用配对码而不是登录态

CLI 没有浏览器 UI 可承载登录态，Electron 壳那套"从既有会话取身份"在这里不成立。
配对码是"**人把两个通道接起来**"的标准做法：CLI 走机器通道拿 `pairing_id`，人走视觉通道输入 `code`，服务端把两者绑定。

---

## 4. 连接建立与鉴权

```
1. CLI 启动 / 重连：
   POST /api/v1/localagent/ticket
     Body:    {device_id, ts, nonce, signature=sign(priv, "{device_id}.{ts}.{nonce}")}
     Header:  X-Device-Credential: dc_...
   ← 200 {ticket:"tkt_...", expires_at}

2. 服务端 side：
   - 验 device_credential（哈希比对 + 未吊销 + 未过期）
   - 验签名（用 device 登记的 public_key）
   - 验 ts 在 ±300s 内（防重放）
   - 验 nonce 未被使用（Redis SETNX，TTL 600s）
   - 生成 ticket：{user_id, device_id, org_unit_id, exp: now+60}
     存 Redis，**取出即删**（一次性）

3. CLI：
   wss://{host}/localagent/connect?ticket=tkt_...

4. 服务端 WS 握手：
   - 从 ticket 解析身份 → **不接受任何客户端声明的 user_id**
   - 消费者删除 ticket（重复使用直接 401）
   - 版本门槛检查（见 §10）
   - 建立连接 → 写 Redis 在线注册表 → 回 welcome 帧
```

**吊销语义**：Web 端解绑 → 服务端吊销 `device_credential` → 下次 ticket 请求 401 → 连接被拒。
同时向该设备发送关闭帧（`close_code: 4003 device_revoked`）。
这是 CLI 形态下**唯一可靠的吊销点**，必须做。

---

## 5. 帧规格

所有帧为 UTF-8 JSON，一帧一行（NDJSON over WS text frame）。
单帧上限 **1 MiB**（`welcome.max_frame_bytes`），超限断开。

### 5.1 出站帧（服务端 → Agent）

| `type` | payload 字段 | 必填 | 说明 |
|---|---|---|---|
| `welcome` | `session_token` | ✅ | 后续帧的会话标识 |
| | `server_time` | ✅ | ISO-8601 |
| | `heartbeat_interval_ms` | ✅ | 建议 15000 |
| | `max_frame_bytes` | ✅ | 1048576 |
| | `protocol_version` | ✅ | `zy.localagent.v1` |
| `tool_call` | `call_id` | ✅ | ★ **由服务端生成** |
| | `tool` | ✅ | 动作名，如 `browser.click` |
| | `args` | ✅ | 动作参数 |
| | `session_id` | ✅ | 归属会话 |
| | `domain` | ✅ | 目标域名（决定 profile 目录） |
| | `timeout_ms` | ✅ | ★ 小于服务端自身超时（留 5s 余量） |
| | `trace_id` | ✅ | |
| `tool_cancel` | `call_id` / `reason` | ✅ | |
| `command` | `command` | ✅ | `show_browser` / `hide_browser` / `recording_start` / `recording_stop` |
| | `params` | ❌ | |
| `ping` | `seq` | ✅ | |

### 5.2 入站帧（Agent → 服务端）

| `type` | payload 字段 | 必填 | 说明 |
|---|---|---|---|
| `hello` | `protocol_version` | ✅ | 不匹配 → 关闭 `4004 protocol_mismatch` |
| | `agent_version` / `device_id` / `os` | ✅ | 用于版本门槛与排查 |
| | `capabilities` | ✅ | `["browser","screenshot"]` 等 |
| `tool_result` | `call_id` | ✅ | ★ **只回填，不生成** |
| | `ok` | ✅ | bool |
| | `result` | 二选一 | 成功时必填 |
| | `error` | 二选一 | `{code, message, details}`，失败时必填 |
| | `verification` | ✅ | ★ `{state: verified\|failed\|uncertain, evidence}` |
| | `duration_ms` | ❌ | |
| `tool_progress` | `call_id` / `phase` / `detail` | ✅ | `phase`: `starting` / `navigating` / `acting` / `verifying` / `waiting_human` |
| `auth_required` | `call_id` / `url` / `reason` | ✅ | `reason`: `login` / `captcha` / `2fa` / `confirm` |
| | `device_display_name` | ✅ | ★ Web 端必须能说清"去哪台机器操作" |
| `auth_completed` | `call_id` / `url` | ✅ | |
| `recording_event` | `recording_id` / `type` / `sequence` / `payload` | ✅ | 阶段 3 之后使用 |
| `pong` | `seq` | ✅ | |

### 5.3 设计要点（每条都有踩坑来源）

1. **`call_id` 由服务端生成，客户端只回填。**
   反例：客户端生成 `call_id` → 恶意/buggy 客户端可伪造"这是我上次那个调用"的对应关系，把结果错配到别的调用上。

2. **`timeout_ms` 由服务端下发且小于服务端自身超时（留 5s 余量）。**
   这样服务端**先**超时，还能补发 `tool_cancel` 让客户端清理；反过来则是客户端先放弃、服务端干等。

3. **`tool_progress` 是 CLI 形态下的主要感知补偿。**
   CLI 用户看不到"Agent 正在点哪里"，`phase` 是唯一反馈。翻页、上传这类长动作必须发。

4. **`auth_required` 必须带 `device_display_name`。**
   用户可能绑了多台设备（办公机 + 跳板机）。不说清去哪台，等于让人瞎猜。

5. **`tool_result.verification` 是三态，不是 bool。**
   `uncertain` 不能当成功用（见 §8）。

---

## 6. 连接状态机

### 6.1 CLI 侧

```
           ┌──────────┐  pair 成功
  unpaired │ 未配对    │ ──────────────► paired
           └──────────┘
                                      配对码过期/失败
                 ◄───────────────────────────────── 返回 unpaired（提示重试）

  paired ──连接──► connecting
                     │            handshake 失败
                     ├──────────────────────► failed
                     │      （版本过低 / ticket 失效 / 凭据被吊销）
                     │                         │
                     ▼ 成功                     │ 人工处理
                  connected ◄──────────────────┘
                     │
           心跳超时 / 网络断 / 服务端重启
                     ▼
                reconnecting ──指数退避 1s,2s,4s,8s…上限 60s──► connecting
                     │
             收到 4003 device_revoked
                     ▼
                  revoked（停止重连，提示重新配对）
```

**硬规则**：`reconnecting` 状态下**不重放**未完成的 `tool_call`。
未完成的调用由服务端判定超时并返回错误——浏览器动作不保证幂等，重放可能造成重复提交。

### 6.2 服务端注册表侧

```
offline ──hello 通过──► online        Redis: localagent:online:{org_unit_id}
online  ──WS 关闭────► offline       （标记带 connected_at + instance_id）
online  ──解绑/吊销──► revoked       同时断开连接
```

Redis 结构（**从第一天就是 Redis，不是进程内 dict**）：

```
Hash:    localagent:online:{org_unit_id}
           field = "{user_id}:{device_id}"
           value = {instance_id, capabilities[], agent_version, connected_at}
Pub/Sub: localagent:route:{user_id}
           任意 api 实例发布投递意图
           持有该 WS 的实例订阅并实际下发
```

**进程终止时必须释放标记**（`lifespan.shutdown`），否则留下幽灵设备——用户看到"在线"但调用永远超时。

---

## 7. 错误码表

### 7.1 服务端产生（`tool_result.error.code` 与 HTTP 错误共用）

| 码 | 含义 | 可重试 | 面向用户的话术方向 |
|---|---|---|---|
| `local_agent_offline` | 设备未连接 | ✅ | 「【办公机-01】当前离线，最后一次在线 10 分钟前」 |
| `local_agent_version_too_low` | 版本低于 `minimum` | ❌ | 「请升级本地 Agent 到 0.2.0 以上」 |
| `local_agent_disabled` | 服务端关闭了该能力 | ❌ | 「该能力已停用」 |
| `device_revoked` | 凭据被吊销 | ❌ | 「该设备已解绑，请重新配对」 |
| `device_ambiguous` | 多设备在线且会话未绑定设备 | ❌ | 「你有 2 台设备在线，请先选择」 |
| `tool_not_authorized` | Profile 中无此工具授权 | ❌ | 「你没有该操作权限」 |
| `tool_unknown` | 工具名不存在 | ❌ | 内部错误，报 trace_id |
| `approval_required` | 需审批（非异常，指状态） | — | 触发审批流程 |
| `approval_rejected` | 审批被拒 | ❌ | 「该操作被拒绝：{reason}」 |
| `approval_timeout` | 审批超时 | ✅ | 「审批超时，已取消」 |
| `action_timeout` | 动作执行超时 | ✅ | 「操作超时」 |
| `action_failed` | 动作执行失败 | ✅ | 转述 `message` |
| `verify_uncertain` | ★ 效果核验不确定 | ✅ | **需复核**，不可当成功 |
| `verify_failed` | ★ 效果核验判定失败 | ✅ | 「操作未生效」 |
| `browser_launch_failed` | Chromium 起不来 | ✅ | 「浏览器启动失败，请检查本地环境」 |
| `profile_locked` | profile 目录被另一个进程占用 | ✅ | 「浏览器进程冲突，请稍后重试」 |
| `domain_not_authorized` | 目标域名不在允许列表 | ❌ | 「该站点不在授权范围」 |
| `quota_exceeded` | 配额不足 | ❌ | 「本月额度已用完」 |

### 7.2 协议层（WS 关闭码）

| 关闭码 | 原因 | CLI 行为 |
|---|---|---|
| `4000` | `invalid_frame` — 帧格式错误 | 记录并重连 |
| `4001` | `ticket_expired` | 重新取 ticket 后重连 |
| `4002` | `protocol_mismatch` | 提示升级，停止重连 |
| `4003` | `device_revoked` | 清凭据，回 `unpaired` |
| `4004` | `version_too_low` | 提示升级，停止重连 |
| `4005` | `idle_timeout` | 重连 |

### 7.3 配对

| 码 | 含义 |
|---|---|
| `pairing_code_invalid` | 配对码不存在或已过期 |
| `pairing_attempts_exceeded` | 尝试次数超限，需重新发起 |
| `pairing_already_confirmed` | 该配对已被确认 |
| `pairing_signature_invalid` | 签名校验失败 |

---

## 8. 驱动分层与效果核验

这是驱动分层与效果核验**最值得投入的部分**，也是这套东西区别于"让 LLM 点按钮"的核心。

### 8.1 驱动接口（服务端侧）

```python
# localagent/drivers/base.py
class BrowserDriver(Protocol):
    async def observe(self, tc: ToolContext, *, domain: str) -> Observation: ...
    async def act(self, tc: ToolContext, action: Action) -> ActionOutcome: ...
    async def verify(self, tc: ToolContext, outcome: ActionOutcome,
                     expect: Expectation) -> Verification: ...

# ActionOutcome 里必须含 verification 三态
@dataclass(frozen=True)
class Verification:
    state: Literal["verified", "failed", "uncertain"]
    evidence: dict[str, Any]      # 截图引用 / DOM 摘要 / 断言结果
    confidence: float             # 0..1，仅用于排序不确定项的复核优先级
```

驱动选择：

```
drivers/fast_path.py   命中工作流缓存 → 直接重放，不做探索
drivers/explore.py     无缓存 → observe → decide → act 循环
drivers/form.py        表单类动作的结构化处理（填值、校验、提交）
```

### 8.2 效果核验为什么是必需的

**核心命题**：每次动作后必须回页面**确认结果真的发生了**，而不是相信 LLM 说"我点成功了"。

没有这层，内网自动化会产生**静默错误**——最危险的一类：Agent 报告"已提交"，实际表单没提交；操作员基于这个报告继续往下走，错误在几小时后才暴露。

```python
# localagent/verification/contract.py
@dataclass(frozen=True)
class Expectation:
    """一个动作声明"成功应表现为…"的契约。"""
    kind: Literal["element_appears", "element_disappears", "text_matches",
                  "url_matches", "value_changed", "no_error_banner"]
    selector: str | None
    pattern: str | None
    timeout_ms: int
```

### 8.3 三态判定与不确定性处理

```
判定           条件                                后续动作
──────────────────────────────────────────────────────────────
verified       期望全部满足                        正常返回
failed         期望明确不满足                      返回错误，Agent 转述
uncertain      超时 / 页面变化但语义不明 / 多义     ★ 不得当成功
```

**`uncertain` 的处理规则**（三条，缺一不可）：

1. 返回 `verify_uncertain` 错误码，**不返回成功**；
2. 附带 `evidence`（截图 + DOM 摘要），让人能快速判断；
3. 标记该动作需要复核，写入审计的 `result` 字段（不是 `ok`）。

**表单作用域**（`form_scope.py`）：同一个页面可能有多个表单，`form_scope` 负责锁定目标表单边界，避免"改错表单"——这是内网系统最常见的静默错误来源之一。

---

## 9. 浏览器 profile 隔离与模式切换

### 9.1 按域名隔离 profile

```
--user-data-dir=<state>/browser-profile/<domain-hash>
```

**不同内网系统不共享 Cookie。** 否则 A 系统的会话会串到 B 系统的请求里——这是串线事故的常见来源，且排查极其困难（表现是"偶发的权限错误")。

### 9.2 模式切换的硬约束

| 场景 | 模式 | 说明 |
|---|---|---|
| 常规自动化（已登录） | `headless` | 无窗口，省资源 |
| 需要人登录 / 验证码 / 二次确认 | `headed` + `show_browser` | 弹窗并置顶，Web 端同步提示 |
| 人工录制操作流程 | `headed` + `recording_start` | 人手动操作，事件回流 |

**同一个 profile 不能同时 headless 与 headed。**
切换需要**重启浏览器进程**（保留 profile 目录即可保留登录态）。
所以驱动必须有"**重启并保持登录**"这一原语，而不是试图原地切换。

---

## 10. 版本门槛与自更新

```
GET /api/v1/localagent/version  ← 无鉴权（CLI 可能尚未配对）
  → {latest: "0.3.1", minimum: "0.2.0", release_notes_url: "..."}
```

| 情况 | 行为 |
|---|---|
| `agent_version < minimum` | 拒绝建立 WS 连接（关闭码 `4004`），CLI 提示必须升级 |
| `minimum <= agent_version < latest` | 正常连接，Web 端设备列表显示"可升级" |
| 等于 `latest` | 正常 |

检查时机：CLI 启动时 + 每小时一次。
配对时记录 `agent_version` 与 `os`，便于排查现场问题。

**`minimum` 的作用**：协议不向后兼容时的唯一手段。0.1.x 与 0.2.x 帧格式若有不兼容变化，靠抬高 `minimum` 强制收敛，而不是在协议里写一堆版本分支。

---

## 11. CLI 命令集

```
zy pair                 # 发起配对，打印配对码
zy status               # 连接状态、设备名、当前 profile 数、版本
zy logs [-f]            # 查看日志（现场排查用）
zy run                  # 前台运行（默认后台常驻）
zy update               # 自更新
zy uninstall            # 清理状态目录与凭据
```

**三条命令是底线**（`pair` / `status` / `logs`）：CLI 形态下用户感知本来就弱，现场出问题时必须有一条不需要远程登录就能自查的路径。

---

## 12. 无本地 Agent 时的降级（与 §5.7 对齐）

| 层 | 行为 |
|---|---|
| **Profile 编译** | **不受影响**。`local` 工具是否入 Profile 由**静态授权**决定，与在线状态无关 |
| Web 端 | 顶部显示设备状态（在线 / 离线 / 版本过低）；`local` 工具置灰并提示"需安装本地 Agent" |
| 工具调用 | 返回 `local_agent_offline`（含设备名与最后在线时间），Agent 用自己的话转述 |
| `hybrid` 工具 | 降级为服务端部分能力，并在结果里**明示**"未使用浏览器渲染" |
| 产品叙事 | 服务端能力必须能独立成立——否则"客户还没装客户端"就成死结 |

**最容易写错的一条**：如果"是否含 local 工具"取决于 Agent 当前是否在线，那么用户每连一次、断一次，`profile_version` 都会变，**会话被迫中断**。
正解：编译时只看静态授权；在线性在**调用时**检查。
