# ZY-Agents · 企业级多人在线智能体平台 · 架构设计规格

> **SPEC-00 · 最高权威** ｜ 产品 `ZY-Agents` ｜ 版本 `v0.3` ｜ 基线 `2026-09-29` ｜ 内核 `DSH 0.2.0-rc.1`
> 修订 `v0.2.1`（`2026-09-29`）：产品定名 `ZY-Agents`；全文标识替换（`ap-platform` → `ZY-Agents`，`ap.*.v1` → `zy.*.v1`）；新增 §0.5 命名与标识。
> 修订 `v0.2.2`（`2026-09-29`）：确认完整名 `ZY-Agents` / 对外简写 `ZY`；§0.5 形态定义由四种扩为**五种**（新增"简写"形态）；标识符形态由 `zyagents` 收敛为 **`zy`**——契约命名空间、kernel preset id、token `aud`、宿主事件类型、CLI 命令名、状态目录同步替换。
> 修订 `v0.3`（`2026-09-29`）：**结构性变更**。① 新增 §12「品牌与白标配置」——私有化交付要求展示层品牌可配置，并划定"厂商标识 / 客户品牌"两层边界；② §10.4 必查清单新增自研事件类型 `ignorable` 标记条款；③ 原 §12「遗留待定项」顺延为 §13。**新增 `S-59`~`S-64` 共 6 条约束**，故取次版本号（`v0.2.x` 已用于纯命名修订）。
>
> 本文是设计意图的**唯一权威源**。`01`–`04` 是它的施工级细化，**不得与本文冲突**；冲突时以本文为准，并须回改细化文档。代码与本文不一致视为**缺陷**，不得以"文档过时"为由默认接受。
> 本文中的硬约束（"必须 / 禁止 / 不接受 / 唯一"类表述）已统一编号为 **`S-nn`**，登记在 `05-spec-compliance.md`——那是监督执行的唯一清单。
>
> 内核 DeepSeek Harness (DSH) · 单租户私有化交付 · 团队 10 人以上
> 本地 Agent 形态：**CLI + 独立 Chromium** · 服务端沙箱浏览器：**不做** · 知识库：**全文检索起步**
> 内核策略：长期跟进 DSH 升级
>
> 本文只描述架构与技术边界，不含实现代码。文中所有路径均已实现落地。
> v0.2 相对 v0.1 的变化：锁定三项决策，并按决策后果改写 §5（本地执行层）、新增 §6（知识库）、简化 §9（前端）。

---

## 文档权威与修订

| 项 | 约定 |
|---|---|
| 权威等级 | 本文 > `01`–`04` > 代码。三者不一致时，**先改设计文档再改代码** |
| 冲突裁决 | 细化文档与本文冲突 → 以本文为准；若确需改变设计，先修订本文并升版本号 |
| 约束登记 | 本文新增或修改硬约束，**必须同步更新 `05-spec-compliance.md`**，否则下游无从监督 |
| 版本递增 | 结构或约束变更（增删条款、改不变量）→ 升次版本号（`v0.3`）；纯表述澄清或**命名/标识变更** → 修订号（`v0.2.x`，逐次递增） |
| 上游依赖 | 内核 `0.2.0-rc.1`。上游版本变化**不自动**修改本文，但必须重跑 `05` §4 的升级闸门 |
| 偏差记录 | 代码必须偏离本文时，在此处登记临时偏差（条款号 / 原因 / 复原计划），不允许口头豁免 |

**当前临时偏差**：无。

---

## 0. 约束与结论

### 0.1 已确认约束

| 维度 | 取值 | 对架构的直接影响 |
|---|---|---|
| 部署形态 | 单租户私有化（一客一部署） | 不做运行时租户路由；`tenant_id` 降级为**组织单元**语义；隔离靠部署而非靠代码 |
| 团队规模 | 10 人以上 | 可承担 2~3 个服务，但**后端只做一个进程**；多实例从第一天设计（禁止进程内状态） |
| 本地执行 | 需要（内网无 API 老系统） | 自建本地 Agent（无 GUI 壳，身份靠配对码派生） |
| 本地 Agent 形态 | **CLI + 独立 Chromium** | 无 GUI 壳 → 身份靠**配对码**而非浏览器登录态；人机交接靠可见的 Chromium 窗口 |
| 服务端沙箱浏览器 | **不做** | 浏览器能力**完全依赖客户端** → 产品必须能在纯 Web 形态下交付价值；容器数不因阶段 3 增长 |
| 知识库检索 | **全文检索起步** | 阶段 2 零新增基础设施；中文检索自己解决（见 §6） |
| 内核策略 | 长期跟进 DSH | 升级准入工具链 + 契约冻结 + 适配器集中 |

### 0.2 一句话架构

**一个 FastAPI 主服务承载全部领域逻辑，DSH 以独立 Node 进程运行并与主服务双向调用；主服务把模型、工具、Skill 编译成一份内容寻址的不可变 Runtime Profile 下发给内核；内核的模型调用与工具调用反向回调主服务——密钥与权限永不出宿主。浏览器自动化由用户机上的 CLI 型本地 Agent 执行，服务端只做编排、核验与审计。**

### 0.3 三项决策的架构后果

这三条不是配置项，它们各自改变了架构的一处形状。

**A. CLI + 独立 Chromium → 身份链路与交接方式都变了**

| 变化 | 具体后果 |
|---|---|
| 没有浏览器 UI 可承载登录态 | 设备身份必须走**配对码**绑定（§5.2），不能像 Electron 壳那样从既有会话里取 |
| 用户感知弱 | 必须有补偿：Web 端设备状态徽章、CLI 三条命令（pair / status / logs）、可见的 Chromium 窗口 |
| Chromium 是独立 profile | 与用户日常浏览器完全隔离 → **既有登录态不会自动带上**，"需要人登录"是必做流程而非可选优化 |
| 分发轻 | 单二进制或 `npm i -g`，私有化环境需提供离线安装包 |

**B. 不做服务端沙箱 → 工具必须分成三类，且 Profile 稳定性要单独处理**

| 变化 | 具体后果 |
|---|---|
| 浏览器能力成为可选增强 | 工具分 `server` / `local` / `hybrid` 三类（§4.4）；供应商方案的第一条卖点必须是服务端能力 |
| 简单抓取不能依赖浏览器 | 公网页面用服务端 HTTP 工具（httpx + 解析）覆盖，不需要 Chromium |
| **Profile 稳定性出现新坑** | 若"是否含 local 工具"取决于 Agent 当前是否在线，则连上/断开都会改 `profile_version` → 会话被迫中断。**正解：local 工具是否入 Profile 由静态授权决定，与在线状态无关**；未连接时工具调用返回明确的 `local_agent_offline`，由 Agent 转述 |
| 部署更简单 | 阶段 3 不新增服务端容器 |

**C. 全文检索起步 → 中文分词要自己解决**

| 变化 | 具体后果 |
|---|---|
| MongoDB `$text` 对中文不可用 | 它的分词按空格/标点，且没有中文分词器。必须自建倒排（§6.2） |
| 引入一个自维护组件 | 分词 + `terms` 多键索引 + 自算 BM25 + 一张 `df` 统计表 |
| 换取零新增基础设施 | 不装 Elasticsearch、不装向量库；文档到十万级仍可用 |
| 留好升级缝 | 检索接口固定为 `search(query, filters, top_k) -> [Hit]`，阶段 3 换混合检索时不改调用方 |

### 0.4 机制取舍总表

下表记录本项目对各项机制的选择与理由：**采用** = 采纳目标并自行实现；**重画** = 保留目标、重新设计实现；**收缩** = 削减范围；**不做** = 明确排除。

| 机制 | 决定 | 理由 |
|---|---|---|
| 双网关反向回调 | **采用** | 密钥/权限/配额/审计留在宿主，内核可替换。整套设计里最值钱的一条 |
| Runtime Profile 内容寻址编译 | **采用** | 权限与配置的单一真相源；`profile_version` 变了会话就不可续，是天然的一致性闸门 |
| `AgentKernelContract` Protocol | **采用** | 内核中立性；DSH API 只在适配层出现 |
| 内核事件 envelope 与投影分离 | **采用** | 前端与产品逻辑不消费 DSH 原生事件，升级不波及上层 |
| turn 级单写者抢占 | **采用，粒度已改** | 多人场景下"一人驱动、多人旁观 + 可显式接管" |
| 驱动分层 + **效果核验** | **采用** | 每次动作后回页面确认结果真的发生了。没有这层，内网自动化会产生静默错误 |
| 本地 Agent 桥协议 | **重画** | 身份从配对码派生；帧格式可参考，语义自己定 |
| 参与者模型 | **重画** | 参与者模型必须自带脱敏，且文件签名须绑定身份——多人场景的直接泄露面 |
| 服务拆分 | **收缩** | 共享同一数据库的多服务形态在 10 人团队里是分布式单体而非微服务。单元化，不做服务数量表演 |
| 部署复杂度 | **砍半** | 6 个容器起步；无沙箱浏览器、无向量库、无对象存储 |
| Electron 桌面壳 | **不做** | 选 CLI，前端因此不需要平台抽象层（§9 的一处简化） |
| Workflow 画布 | **不做** | "AI 自动编排，人只审阅关键决策"是正确的产品判断 |
| vendor DSH 源码 | **不做** | pin + lock + SBOM 足够；vendor 会让你失去整列车升级能力 |
| 早期接多套 IM | **不做** | 第一阶段只做 Web 端 |

---

### 0.5 命名与标识

产品名 **`ZY-Agents`**，定名日 **`2026-09-29`**，替代零代码阶段的占位名 `ap-platform`。

**`ZY` 取自项目代号（`ZY-Agents`）**——两字母缩写为项目命名约定。`ZY-Agents` 的含义是"ZY 的智能体平台"，而不是无来源的两字母缩写。版权信息：`Copyright 2026 ZY-Agents Contributors`。

命名派生为**五种形态，用途互斥、不可混用**：

| 形态 | 取值 | 用途 |
|---|---|---|
| 展示名（完整） | `ZY-Agents` | 文档标题、产品对外表述、UI |
| 简写（对外） | `ZY` | 口头与书面简称、UI 短标签、讨论中指代本产品 |
| 标识符形态 | `zy` | 契约命名空间、kernel preset id、token `aud`、宿主事件类型、CLI 命令名、状态目录 |
| 路径形态 | `zy-agents` | 仓库目录名、容器镜像名前缀、JWT issuer、Python 发行包名 |
| 环境形态 | `zy_agents` | MongoDB 库名、下划线环境变量 |

**分形态的判据是「可读性」还是「机器唯一性」**：需要人读的地方（目录、镜像名、JWT issuer、库名）用完整派生的 `zy-agents` / `zy_agents`；空间受限或需机器唯一的地方（契约命名空间、preset id、token `aud`、事件类型、CLI 命令、状态目录）用简写 `zy`。

**为什么契约命名空间取单段无连字符的 `zy`**：需与上下游的三段式命名空间（`<厂商>.dsh-host.v1`）保持同构；写成 `zy-agents.dsh-host.v1` 会被解析成四段，与适配层的命名空间解析假设不符。简写形态恰好满足这个约束，不额外引入第三种拼法。

**标识全量映射（9 类）**

| 类别 | 取值 | 出现处 |
|---|---|---|
| 目录 / 仓库名 | `ZY-Agents` | 仓库根 |
| 契约命名空间（5 个常量） | `zy.agent-kernel.v1`、`zy.kernel-event.v1`、`zy.dsh-host.v1`、`zy.localagent.v1`、`zy.runtime-profile.v1` | 本文 §3.3、§10.3；`03`；`04` |
| 容器 / 镜像名 | `zy-agents/api:local`、`zy-agents/kernel-host:local` | `01` §7.1 |
| JWT issuer | `JWT_ISSUER=zy-agents` | `01` §6 |
| MongoDB 库名 | `MONGO_DB=zy_agents` | `01` §6 |
| Kernel preset id | `zy-enterprise` | `03` §2.3 |
| CLI 命令名 | `zy`（子命令 `pair` / `status` / `logs` / `run` / `update` / `uninstall`） | `04` §11 |
| 状态目录 | `%APPDATA%/zy/`（Windows）、`~/.zy/`（macOS/Linux） | `04` §1 |
| Python 发行包名 | `zy-agents`（`[project] name`）；import 根包保持 `app` | `01` §2（目录树内 `pyproject.toml`） |

**改了即有成本**：`zy.*.v1` 五个常量会进契约校验与版本锁；`~/.zy/` 存的是用户机上的私钥、设备凭据与 Chromium profile，改它等于动客户端数据迁移。**本次改名在零代码阶段完成，成本已结清**；此后若要再改，须按 `05` §6 流程评估并包括客户端迁移。

> **本节定义的是「厂商标识」——不可变，且会进入协议、日志与磁盘。**
> 与之相对的是**客户品牌**（公司名 / logo / 主题色 / 文案等），它**每部署可配**，只作用于展示层与模型请求的运行时上下文。两层的边界、配置方式与安全要求见 **§12**。
> 这个区分必须写死在 spec 里，否则第一个此类需求（"能不能把 API 路径改成我们公司的"）就会把边界冲垮——客户品牌一旦渗进契约命名空间或 token `aud`，**每个客户部署的协议就各不相同**，升级、排障、支持会退化成 N 份。

---

## 1. 部署形态

### 1.1 分期拓扑

```
阶段 1（MVP，6 容器）      阶段 2（+1）              阶段 3（服务端 +0）    阶段 4（+0）
  nginx  :3000             + doc-worker (Celery)     + 本地 Agent（客户端）  + 第二 api 实例
  web    (静态)                                       （不在服务端拓扑内）
  api    :8000
  kernel :8101 (Node)
  mongo  :27017
  redis  :6379
  fs-vol (本地卷)
```

**关键纪律**：阶段 1 不引入向量库、不引入对象存储、不引入 Celery 之外的消息中间件。Redis 同时承担缓存、Celery broker、本地 Agent 注册表与跨实例路由。文档解析走同步 + 单 worker。

**容器数收敛的原因**：不做沙箱浏览器（省一个长期维护的 Chromium 镜像 + 反爬对抗），不做对象存储（本地卷 + nginx 直发够用），不做向量库（§6）。

### 1.2 服务清单

| 服务 | 端口 | 语言 | 职责 | 数据所有权 |
|---|---|---|---|---|
| `nginx` | 3000 | — | 单入口反代；SSE / WS 需 `proxy_buffering off` + 长超时 | — |
| `web` | 静态 | TS/Vue | 用户端 + 管理端（同镜像两入口） | — |
| `api` | 8000 | Python/FastAPI | **全部领域逻辑**（§2） | MongoDB 全部集合 |
| `kernel` | 8101 | Node 22 | DSH 运行时宿主；无状态，可水平复制 | 会话 JSONL（独立卷） |
| `mongo` | 27017 | — | 主数据 | — |
| `redis` | 6379 | — | 缓存 / Celery broker / **本地 Agent 注册表 / 跨实例事件路由** | — |
| `doc-worker` | — | Python | 阶段 2 加入；Celery worker，文档解析与索引 | 无（写 Mongo） |

### 1.3 单租户下的身份语义

单租户不等于没有隔离维度。保留三层，只是压进一个部署：

- `org_unit_id` —— 取代 `tenant_id`。部门 / 项目组级隔离，**所有查询强制带此条件**（由仓储基类注入，不靠人记）。
- `user_id` —— 登录态派生（§7.1）。
- `conversation_id` —— 协同与权限的最小单位。

未来若转多租户，只多加一层维度，不必重写查询。

### 1.4 客户端分发（CLI 形态新增）

| 项 | 方案 |
|---|---|
| 打包 | 单文件可执行（Node SEA）+ `npm i -g` 两条路；私有化环境随交付物附带**离线安装包** |
| 目标平台 | 阶段 3 只做 Windows；macOS / Linux 视客户分布后补（列在 §13） |
| Chromium | 用 Playwright 的 Chromium 分发（免自带 driver 维护），**独立 `--user-data-dir`** |
| 更新 | CLI 启动时查 `/api/localagent/version`；协议里上报 `agent_version`，服务端可设最低版本硬门槛 |
| 常驻 | 作为用户级后台进程运行（非系统服务，避免提权与合规问题） |

---

## 2. 主服务内部分层（`services/api/`）

**不要拆成多个后端服务。** 拆成两个以上共享同一 MongoDB 的服务，就等于造一个分布式单体——多个服务共享同一批集合就是这个形态。用一个进程 + 强制分层，换事务边界清晰与部署简单。

```
services/api/
  app/
    main.py                    # 应用装配：注册路由、lifespan 初始化
    kernel/                    # ★ 内核接入层：全项目唯一接触 DSH 的位置
      contract/                # 冻结契约（版本化，只增不改）
        protocol.py            #   AgentKernelContract (Protocol)
        commands.py            #   CreateRuntimeRequest / CreateSessionRequest / SendRequest / CancelSessionRequest
        handles.py             #   RuntimeHandle / SessionHandle / SessionStatus
        events.py              #   KernelEventEnvelope
        versions.py            #   契约版本常量
      dsh/                     # DSH 适配器（上游 API 只在这里出现）
        gateway.py             #   DshKernelGateway 实现 AgentKernelContract
        transport.py           #   NDJSON 事件流解析 / 心跳 / 重连
        runner.py              #   单 turn 执行器
        recovery.py            #   断线续跑
        adapters/              #   ← 升级时唯一需要改动的目录
          preset.py
          events.py
          tools.py
          models.py
        host/                  #   Node 侧宿主的 Python 侧管理（进程 / 健康 / 版本探测）
      profile/                 # ★ Runtime Profile 编译器（§3）
      inbound/                 # ★ 内核回调入口（§4）
        model_endpoint.py
        tool_endpoint.py
        approval_endpoint.py
        credentials.py         #   HMAC 短时 token 签发与校验
      turn/                    # turn 生命周期
        admission.py           #   准入：权限、Skill 授权、并发抢占
        claims.py              #   单写者 claim
        finalization.py
        cancellation.py
    identity/                  # 登录态、用户、组织单元、角色、资源 ACL、**设备凭据**
    collaboration/             # ★ 会话、参与者、消息、事件投影、分享（§7）
      conversation.py
      participants.py
      messages.py
      projection.py            #   按 viewer 角色分级投影（脱敏在这里）
      share.py
      stream.py                #   SSE 推送
    capability/                # 企业内部工具（服务端执行）
      registry.py
      tools/http_fetch.py      #   HTTP 抓取（替代公网场景的浏览器）
      tools/...
    localagent/                # ★ 本地 Agent 桥（§5）
      pairing.py               #   配对码签发与设备凭据
      devices.py               #   设备台账（绑定 / 吊销）
      registry.py              #   Redis 在线注册表
      router.py                #   跨实例路由
      ws_endpoint.py
      drivers/                 #   驱动分层
      verification/            #   动作效果核验
    knowledge/                 # 知识库（§6，阶段 2）
      ingest.py                #   入库与切块
      tokenizer.py             #   jieba 分词 + bigram 回退
      indexer.py               #   terms 写入 + df 统计维护
      retriever.py             #   ★ 检索接口（换实现不改调用方）
    document/                  # 文档解析（阶段 2）
    governance/                # 审批、审计、配额
      approval.py
      audit.py
      quota.py

  scripts/kernel_upgrade/      # ★ DSH 升级工具链（§10）
  scripts/check_layering.py    # 分层依赖守卫
```

### 2.1 分层依赖规则（用 lint 强制，不靠自觉）

```
kernel/      ← collaboration/ · capability/ · governance/       允许
collaboration/ → kernel/  ：只允许 import contract/，禁止 import dsh/
任何模块      → dsh/      ：禁止（唯一例外 kernel/dsh/gateway.py）
kernel/       → collaboration/  ：禁止（回调走 inbound/ 的独立端点）
```

在 CI 里跑。这条规则的价值在于：**升级 DSH 时，你知道爆炸半径只可能在 `kernel/dsh/` 里。**

---

## 3. Runtime Profile：内容寻址编译

整套设计的地基。

### 3.1 编译链路

```
admin 数据（可变）
  模型实例 + 工具定义 + Skill 定义 + 写作风格 + 用户的静态工具授权
        │
        ▼  确定性编译（kernel/profile/compiler.py）
  规范化 JSON（sort_keys + 无分隔空白）→ sha256
        │
        ▼
  profile_version = "rp-" + hash[:24]
        │
        ▼  运行时注入（仅此一次）
  host_payload() 加上 gatewayUrl / accessToken / toolGatewayUrl / toolAccessToken
        │
        ▼
  下发给内核宿主
```

### 3.2 四条不变量

1. **Profile 不含任何密钥。** 快照只有 `provider_type` / `model_name` / `context_window` 这类元数据；`api_key` 与网关 token 只在 `host_payload()` 调用时拼接。
2. **同输入必得同 `profile_version`。** 配置没变 → 版本号不变 → 已建会话仍可 resume；配置一变，版本号必变，**旧会话自动不可续**（明示报错，不静默降级）。这是一致性闸门，不是副作用。
3. **Profile 是每人一份。** `subject_user_id` 参与编译。用户 A 的 Skill 授权、可用模型、工具集合与用户 B 不同，因此 profile 不同。多人协同的会话绑定**发起者的 profile**，参与者不改变它。
4. **Profile 只承载"能力与权限"，不承载"当下的信息"。** 编译输入**不包含任何运行时易变状态**：
   - `local` 类工具是否进入 Profile，只由**静态授权**（该用户是否被授予本地执行权限）决定，与"本地 Agent 此刻是否在线"无关——否则连上/断开都会改版本号，会话会被迫中断。在线性在调用时检查，不在编译时检查；
   - **品牌配置**（公司名 / logo / 主题色 / 文案）**同样不进 Profile**（§12.4）——客户改一次公司名不应使所有会话失效；
   - 需要随每次 turn 变化的运行时信息（品牌名、当前日期、检索结果、项目记忆）一律由**模型网关在请求构造时注入**一段 turn 级运行时上下文，不进内容寻址、不影响 `profile_version`。

### 3.3 建议的 Profile 字段（`kernel/profile/models.py`）

```
schema_version      "zy.runtime-profile.v1"
profile_version     "rp-{sha256[:24]}"
content_hash        sha256 全量
org_unit_id         组织单元
subject_user_id     编译主体（每人一份）
model_*             模型实例 / 提供商 / 上下文窗口 / 最大输出
tool_versions[]     工具版本快照
tools[]             工具定义（名称、描述、JSON Schema、类别、超时、审批要求）
skills[]            Skill 定义（kind: skill | workflow | writing_style）
skill_versions[] / workflow_versions[]
```

`tools[]` 里的**类别**字段（`server` / `local` / `hybrid`）是本地执行层的接线点。

### 3.4 Skill / Workflow 的编译约束

Skill 不是"塞进 prompt 的一段文本"，而是**编译进 Profile 的结构化定义**：

- Skill 可用性由编译期决定，运行时无法越权加载；
- Skill 版本进入 `profile_version`，会话与 Skill 版本一一对应，可复现；
- Skill 声明它需要的工具，编译器校验该工具是否在此用户 Profile 内——缺依赖直接**编译失败**，而不是运行时才发现。

---

## 4. 双网关反向回调

### 4.1 端点

| 端点 | 方法 | 用途 |
|---|---|---|
| `/internal/kernel/model/generate` | POST | 模型推理（含流式） |
| `/internal/kernel/tools/execute` | POST | 工具执行（服务端工具 + 转发到本地 Agent） |
| `/internal/kernel/tools/approval` | POST | 审批请求（人在环） |

这三个端点是**内核唯一的出口**。不出现在公开路由表里，走独立前缀 + 独立鉴权中间件。

### 4.2 鉴权：HMAC 短时 token

- 会话创建时，主服务通过 `PUT /v1/runtimes/{id}/model-credential` 与 `.../tool-credential` 下发；
- token 内含 `aud`（区分 model / tool）、`runtime_id`、`profile_version`、`exp`；
- 有效期分钟级，turn 开始前刷新，turn 结束后作废；
- 校验时同时比对 `aud` 与 `runtime_id`——**model token 不能用来调工具端点**。

### 4.3 为什么要反向

| 内核直连模型 | 反向回调 |
|---|---|
| API Key 落在内核配置里，进程可被读 | Key 只在主服务内存，内核拿不到 |
| 配额 / 限流要在内核实现 | 在宿主实现一次，所有内核版本通用 |
| 换模型提供商要改内核配置并重启 | 改 Profile，下次 turn 生效 |
| 审计日志散在内核 | 统一在主服务落库 |
| 换内核 = 重写全部模型接入 | contract 不变，换适配器 |

### 4.4 工具执行的完整路径

工具分三类，这是"不做沙箱浏览器"的直接产物：

| 类别 | 执行位置 | 例子 | 是否需客户端 |
|---|---|---|---|
| `server` | 主服务 | HTTP 抓取、文档解析、数据处理、知识检索 | ❌ |
| `local` | 用户机本地 Agent | 浏览器自动化、本地文件、终端 | ✅ |
| `hybrid` | 先服务端，必要时转本地 | 先 HTTP 抓取；遇 JS 渲染 / 需登录 → 转本地 | 视情况 |

```
内核 loop 决定调用工具
   → POST /internal/kernel/tools/execute  (tool token)
       → 准入：Profile 里是否声明该工具？用户是否有资源权限？
       → 是否需要审批？（工具定义里 approval_required）
           是 → 创建审批单，SSE 推送给人，阻塞等待（带超时）
       → 按类别分派：
           server  → capability/tools/ 直接执行
           local   → localagent/router.py 转发到用户机器，等待回执
           hybrid  → 先服务端尝试，失败或需要渲染再转 local
                     （local 不可用时，hybrid 降级为"返回服务端结果 + 明示能力受限"）
       → 效果核验（§5.5）
       → 审计落库（参数、结果、耗时、审批人、执行位置）
   → 返回 { ok, result | error, receipt }
```

**local 工具不可用时的语义**：返回结构化错误 `local_agent_offline`（含设备名与最后一次在线时间），由 Agent 用它自己的话告知用户。不要静默失败，也不要假装成功。

**hybrid 类要慎用**：它是"两条路都能走"的诱惑，但会显著增加测试矩阵。只有当服务端结果确实明显不如浏览器时才标记为 hybrid，否则老老实实归到 `local`。

---

## 5. 本地执行层（CLI 型本地 Agent）

**驱动分层与效果核验这两块值得自建时重点投入**——动作契约、效果核验、幂等回执、表单规则、工作流缓存是这个能力域的核心资产。

### 5.1 形态：为什么是 CLI

| 维度 | CLI + 独立 Chromium（选定） | Electron 壳（未选） |
|---|---|---|
| 分发体积 | 小（单二进制 + Chromium 按需下载） | 大（含整个运行时） |
| 前端工作 | 不需要写第二套 UI；**不需要平台抽象层**（§9） | 需要 electron 分支与 IPC 桥 |
| 用户感知 | 弱 → 靠 Web 端状态 + 可见窗口补偿 | 强 |
| 更新 | 自更新一条命令 | 需签名 / 公证 / 增量更新 |
| 与日常浏览器 | 完全隔离的独立 profile | 同样隔离 |

**独立 profile 的一个必然后果**：CLI 的 Chromium 与用户日常浏览器不共享 Cookie → **既有登录态不会自动带上**。所以"需要人登录"不是可选优化，是必经流程（§5.6）。不要试图去读用户 Chrome 的 Cookie 文件——那是隐私与合规的雷区，收益也不值得。

### 5.2 配对与身份

CLI 没有浏览器登录态可用，所以设备身份走**配对码**：

```
首次配对
  CLI 首次运行        → 生成 device_keypair，本地保存私钥（OS keychain）
                      → 调 POST /api/localagent/pairing/begin
                      → 服务端返回 6 位配对码（TTL 10 分钟）+ 一句提示
                      → CLI 在终端打印配对码与引导语
  用户在 Web 端       → 设置 → 我的设备 → 添加设备 → 输入配对码
                      → 服务端把 pairing 会话与 user_id / org_unit_id 绑定
  CLI 轮询            → POST /api/localagent/pairing/claim
                      → 拿到 device_credential（长期，绑定 user_id + device_id）
                      → 存入 keychain

之后每次连接
  CLI                → POST /api/localagent/ticket（device_credential 签名）
                      → 换一次性 ticket（TTL 60s）
                      → wss://<host>/localagent/connect?ticket=...
  服务端              → 从 ticket 解析出 user_id / device_id / org_unit_id
                      → 不接受任何客户端声明的身份
```

**撤销**：Web 端"我的设备"可解绑 → 服务端吊销 `device_credential` → 下次 ticket 请求失败，连接被拒。这是 CLI 形态下唯一可靠的吊销点，必须做。

> 反面案例：把身份做成 `?user_id=` → `X-User-Id` header → bearer 三选一，**全是客户端可控值**。这是常见且容易中招的姿势——从第一行代码就做对，成本是零；事后补，成本是重画。

### 5.3 桥协议

**出站帧（服务端 → Agent）**

```
{"type":"tool_call",   "payload":{"call_id","tool","args","session_id","domain","timeout_ms","trace_id"}}
{"type":"tool_cancel", "payload":{"call_id","reason"}}
{"type":"command",     "payload":{"command":"show_browser"|"hide_browser"|"recording_start"|"recording_stop", ...}}
{"type":"ping"}
```

**入站帧（Agent → 服务端）**

```
{"type":"hello",            "payload":{"agent_version","device_id","os","capabilities":[]}}
{"type":"tool_result",      "payload":{"call_id","ok","result"|"error"}}
{"type":"tool_progress",    "payload":{"call_id","phase","detail"}}
{"type":"auth_required",    "payload":{"call_id","url","reason","device_display_name"}}   # 需要人介入登录
{"type":"auth_completed",   "payload":{"call_id","url"}}                                  # 人已完成登录
{"type":"recording_event",  "payload":{"recording_id","type","sequence","payload"}}
```

设计要点：

- `call_id` 由**服务端**生成，客户端只回填——避免客户端伪造调用对应关系；
- `timeout_ms` 由服务端下发且**小于**服务端自身超时（留 5s 余量），保证服务端先超时后能发 `tool_cancel`；
- `tool_progress` 让长动作（翻页、上传）有可见的中间态；CLI 形态下这是主要的"用户感知补偿"；
- `auth_required` 携带 `device_display_name`，因为 Web 端需要明确告诉用户**去哪台机器上操作**——多设备时这一条不可省。

### 5.4 注册表与跨实例路由

注册表若是**进程内内存 dict**，多 worker 下各实例状态互不可见（这是易犯的错，参考实现里就出现过"Replace with Redis pub/sub for multi-worker"的自陈注释）。你多实例部署，从第一天就得是：

```
Redis Hash:     localagent:online:{org_unit_id}  → { user_id → {instance_id, device_id, capabilities, connected_at} }
Redis Pub/Sub:  localagent:route:{user_id}       → 任意 api 实例发布投递意图
                                                  持有该 WS 的实例订阅并实际下发
```

这样"用户的 Agent 连在实例 A、turn 跑在实例 B"能正常工作。

**多设备**：一个用户可能有多个设备（办公机 + 跳板机）。`instance_id` 之外再加 `device_id`，投递时按 `session` 绑定的设备路由；未绑定时，若只有一台在线就直接用，多台在线则**报错要求选设备**——不要猜。

### 5.5 驱动分层与效果核验（最值得投入的部分）

```
localagent/drivers/
  base.py          # 驱动接口：observe / act / verify
  fast_path.py     # 命中工作流缓存 → 直接重放，不做探索
  explore.py       # 无缓存 → 观察 → 决策 → 动作 循环
  form.py          # 表单类动作的结构化处理（填值、校验、提交）

localagent/verification/
  contract.py      # 动作效果契约：每个动作声明"成功应表现为…"
  tracker.py       # 动作后观察 → 比对契约 → 判定成功 / 失败 / 不确定
  form_scope.py    # 表单作用域（避免改错表单）
```

效果核验是这套东西区别于"让 LLM 点按钮"的核心：**每次动作后回到页面确认结果真的发生了**，而不是相信 LLM 说"我点成功了"。没有这层，内网自动化会产生静默错误——最危险的一类。

判定结果必须是三态（成功 / 失败 / **不确定**），"不确定"不能当成功用，要走复核或上报人工。

### 5.6 浏览器可见性与人机交接

CLI 形态下，Chromium 窗口是唯一的人机交接载体，必须设计清楚：

| 场景 | 模式 | 说明 |
|---|---|---|
| 常规自动化（已登录） | `headless` | 无窗口，省资源 |
| 需要人登录 / 验证码 / 二次确认 | `headed` + `show_browser` | 弹窗并置顶，Web 端同步提示"请在 XX 机器上完成登录" |
| 人工录制操作流程 | `headed` + `recording_start` | 人手动操作，事件回流成可重放的 Skill 素材 |

**按域名隔离 profile**：`--user-data-dir=<state>/browser-profile/<domain-hash>`。不同内网系统不共享 Cookie——否则 A 系统的会话会串到 B 系统的请求里，这是串线事故的常见来源。

**模式切换的硬约束**：同一个 profile 不能同时 headless 与 headed。切换需要重启浏览器进程（保留 profile 目录即可保留登录态）。所以驱动要有"重启并保持登录"这一原语，而不是试图原地切换。

### 5.7 无本地 Agent 时的降级

这是"不做服务端沙箱"之后必须写清楚的一块：

| 层 | 行为 |
|---|---|
| Profile 编译 | 不受影响。`local` 工具是否入 Profile 由静态授权决定（§3.2 第 4 条） |
| Web 端 | 顶部显示设备状态（在线 / 离线 / 版本过低）；`local` 类工具显示为灰色并提示"需安装本地 Agent" |
| 工具调用 | 返回 `local_agent_offline` 结构化错误，Agent 转述为自然语言 |
| `hybrid` 工具 | 降级为服务端部分能力，并在结果里明示"未使用浏览器渲染" |
| 产品叙事 | 服务端能力必须能独立成立——否则一客一部署时"客户还没装客户端"就成了无法演示的死结 |

### 5.8 自更新与版本门槛

- CLI 启动与每小时检查 `/api/localagent/version`，返回 `{latest, minimum}`；
- 低于 `minimum` → 拒绝建立 WS 连接并提示升级（协议不向后兼容时的唯一手段）；
- 低于 `latest` → 正常连接，Web 端提示可升级；
- 配对时记录 `agent_version` 与 `os`，便于排查现场问题。

---

## 6. 知识库（阶段 2：全文检索）

### 6.1 为什么不先上向量库

- 全文检索能覆盖相当比例的"找文档"需求（搜标题、搜术语、搜工单号、搜人名地名）；
- 零新增基础设施 → 私有化部署少一个失败点，这是"人无我有"的部分反而在于稳；
- 向量库的真正收益要到文档量级与语义查询占比都上来才显现；
- 但**接口必须一开始就按可替换的方式定义**，否则阶段 3 换不动。

### 6.2 中文检索自己解决

MongoDB 的 `$text` 索引对中文**不可用**——它按空格与标点切分，且没有中文分词器。三条路：

| 方案 | 做法 | 评价 |
|---|---|---|
| **jieba 分词 + terms 多键索引 + 自算 BM25**（选） | 入库时对正文分词，写入 `terms[]`；查询同样分词后匹配 | 中文效果可用，索引膨胀小，打分可控 |
| n-gram 滑窗 | 2-gram 全切，不需要词典 | 对未登录词与新词友好，但索引膨胀 3~5 倍，噪声大——作为**回退**用于短查询 |
| Elasticsearch | 直接上专业引擎 | 违背零新增基础设施；中文需装 IK 插件，又多一个运维面 |

**选定**：jieba 为主 + bigram 回退。两者在 `knowledge/tokenizer.py` 里是同一接口的两个实现，检索时按查询长度与召回结果数决定是否回退。

### 6.3 集合与索引设计

```
knowledge_documents
  _id, org_unit_id, title, source_type, source_ref, checksum,
  status: pending | indexing | ready | failed,
  char_len, chunk_count, created_at, created_by

knowledge_chunks
  _id, org_unit_id, document_id, seq,
  text, terms[], title_terms[],
  heading_path[],                                   # 章节路径，用于展示与上下文
  page_from, page_to,                               # 便于回指原文
  acl: { visibility: org | restricted, allowed_user_ids[], allowed_roles[] }

knowledge_terms                                     # df 统计表（BM25 需要）
  org_unit_id, term, df
```

索引：

```
knowledge_chunks  { org_unit_id: 1, terms: 1 }              召回
knowledge_chunks  { org_unit_id: 1, title_terms: 1 }        标题召回（加权）
knowledge_chunks  { document_id: 1, seq: 1 }                取上下文
knowledge_chunks  { org_unit_id: 1, text: "text" }          英文 / 精确串兜底
knowledge_documents { org_unit_id: 1, source_ref: 1 }       幂等重入库
```

### 6.4 检索接口与排序

```python
# knowledge/retriever.py —— 阶段 3 换混合检索时，这个签名不变
def search(query: str, *, filters: SearchFilters, top_k: int) -> list[Hit]: ...

# SearchFilters: org_unit_id, user_context, source_types[], doc_ids[], time_range
# Hit: chunk_id, document_id, text, heading_path, score, highlights[]
```

排序流程：

1. 查询分词 → `terms`
2. `$in` 召回候选 chunk（`title_terms` 命中的加权提高）
3. 自算 BM25 打分（`df` 从 `knowledge_terms` 取，`N` 取该 org_unit 的 chunk 总数）
4. **ACL 过滤放在打分之后、返回之前**——先过滤会让召回集变小、分数不可比；后过滤只需丢掉越权结果，代价是少量无用计算
5. 同文档去重（保留最高分 chunk），保证结果多样性
6. 上下文扩展：命中 chunk 时取其前后各 N 个 chunk 一并作为 evidence

### 6.5 升级到混合检索的迁移点

阶段 3 引入向量库时，只做两件事：

1. 新增 `knowledge_chunks.embedding`（或在向量库建集合），离线批量回填；
2. `retriever.py` 内部改为 BM25 召回 + 向量召回 → 融合（RRF 或加权）→ rerank。

**调用方、API、`Hit` 结构都不变。** 这就是把接口先定下来的收益。

> **阶段 2 变更登记（S-34，按 `05` §6）**：混合检索已提前到阶段 2 落地，且走
> **嵌入式向量**而非独立向量库——`knowledge_chunks.embedding` 字段存 1024 维向量
> （网关嵌入模型，`/embeddings`），余弦在检索进程内算（纯 Python），**无独立向量库、
> 无新增 compose 服务**。重排走网关 `/rerank` 精排池 top-20，失败回退融合序。
> §6.1"不引入向量库"的决策不变；改动仅限 `retriever.py` 内部（S-32 签名冻结），
> 索引时逐批嵌入（`indexer.py` 第 6 步），存量 chunk 一次性回填。

---

## 7. 多人协同（必须重新设计）

这条线的已知限制需自行评估：无脱敏、参与者能读到 owner 的全部工具调用参数与结果、文件签名 URL 不绑身份且撤销后 1 小时内仍可访问。这些在"个人助手"场景是可接受取舍，在"多人协同平台"里是主要风险源。

### 7.1 三层身份

```
登录态（JWT / Session）
   → user_id          从 token 派生；任何入口不接受客户端声明
   → org_unit_id      从用户档案派生
   → RequestContext   { user_id, org_unit_id, roles[], device_id?, trace_id }
```

所有仓储方法签名强制带 `RequestContext`，`org_unit_id` 由基类注入查询条件。**不提供"不带上下文"的查询方法**——让越权变成类型期问题，而不是靠 code review。

### 7.2 会话模型

```
conversations
  _id, org_unit_id, owner_user_id, title,
  visibility: private | shared | org,
  device_id,                        # 绑定的本地执行设备（可空）
  created_at, last_active_at, active_run

session_participants
  conversation_id, user_id, org_unit_id,
  role: owner | editor | viewer,
  joined_at, removed_at, last_read_seq
  unique(conversation_id, user_id)
```

| 角色 | 发起 turn | 看全量事件 | 看工具参数 | 审批 | 管理参与者 |
|---|---|---|---|---|---|
| owner | ✅ | ✅ | ✅ | ✅ | ✅ |
| editor | ✅ | ✅ | ✅ | ✅（被授权工具） | ❌ |
| viewer | ❌（可申请接管） | ❌（脱敏视图） | ❌ | ❌ | ❌ |

### 7.3 事件投影分级（脱敏在这里）

```
kernel 事件流 → 持久化（全量，一处）→ 投影服务 → 按 viewer 角色出两套视图
                                            ├─ full      （owner / editor）
                                            └─ redacted  （viewer：隐藏工具参数、文件路径、内网 URL、
                                                           保留最终答复与工具名称 / 状态）
```

- **全量事件只存一份**，脱敏发生在投影层，不在存储层——审计完整、规则可迭代；
- 脱敏是**声明式**的：字段标 `sensitivity: public | internal | secret`，投影器按角色过滤，不在代码里到处写 `if`；
- viewer 的 SSE 推的是脱敏流，**不是前端过滤**——前端过滤等于没过滤。

### 7.4 文件与产物的访问控制

```
签名 URL = sign(user_id, artifact_id, expires_at, nonce)

下载时校验三件事：
  1. 签名有效且未过期
  2. 当前 user_id 对该 artifact 所属会话 / 知识库仍有 ACL
  3. 签名未被撤销（nonce 已拉黑 / 会话参与关系已 removed_at）
```

不要只靠 TTL。TTL 只是兜底，撤销后立刻失效才是正确语义。

### 7.5 单写者与接管

```
claims.claim_turn(conversation_id, message_id, request_id) → claim | None
```

- 抢不到 → 抛 `ConversationBusyError`，前端提示"XX 正在运行，是否旁观"；
- 接管走显式流程：请求接管 → 原持有者收到通知 → 确认后转移 claim（不静默抢占）；
- 多人同时提问的正确处理是**排队**，不是并发——同一会话并发跑两个 turn 会让事件流交错、上下文互相污染。

### 7.6 实时推送

- 每个参与者一条 SSE：`GET /api/conversations/{id}/stream`；
- 事件带 `cursor`（单调递增），断线重连带 `Last-Event-ID` 续传；
- **持久化优先**：先落库再推送，推送失败不影响正确性；
- turn 结束推终态事件，前端据此结束 loading。

---

## 8. 治理层

| 模块 | 职责 | 落点 |
|---|---|---|
| 审批 | 工具级人在环。工具定义里 `approval_required`，执行前阻塞等待 | `governance/approval.py` |
| 审计 | 谁、何时、用哪个 Profile、调了什么工具、在哪个设备执行、结果如何、审批人是谁 | `governance/audit.py` |
| 配额 | 按组织单元 / 用户限制 token 与调用次数；在 model gateway 入口统一扣减 | `governance/quota.py` |
| 资源 ACL | 模型、知识库、Skill、工具、**设备**对用户的可见性 | `identity/acl.py` |

**审批只保留一条生效路径**：内核发起的工具审批，走 `inbound/approval_endpoint.py`。不要再挂一套平行的审批运行时（历史上出现过这类全仓无引用的死代码）。一个机制两条路径，半年后没人说得清线上走的是哪条。

---

## 9. 前端职责边界

CLI 形态带来一处**简化**：不再需要 Electron 壳，因此**不需要平台抽象层**（v0.1 里的 `src/platform/` 去掉）。前端只做一件事——向服务端查"我的设备在线吗"。

```
apps/
  web/                  # 用户端 SPA（Vue 3 + Vite + TS + Tailwind）
    src/features/
      conversation/     #   会话、消息、事件渲染（含 tool_progress / auth_required 等交接态）
      collaboration/    #   参与者、分享、权限
      localagent/       #   ★ 设备状态、配对引导、设备列表与解绑
      knowledge/        #   阶段 2
    src/api/            #   HTTP 客户端（唯一出网位置）
    src/sse/            #   事件流消费与重连
  admin/                # 管理端 SPA
    src/features/
      members/          #   组织单元、成员、角色
      models/           #   模型提供商与实例（写这张表 → 触发 Profile 重新编译）
      skills/           #   Skill 与工具管理与授权（含 server / local 类别与审批开关）
      devices/          #   设备台账：谁绑了哪台机器、版本、最后在线时间、吊销
      audit/            #   审计查询
      quota/
```

### 9.1 明确的分工线

| 职责 | 前端 | 后端 |
|---|---|---|
| 事件脱敏 | ❌ 绝不做 | ✅ 唯一位置 |
| 权限判断（能否显示某按钮） | ✅ 仅用于 UI 提示 | ✅ 唯一执法位置 |
| 身份 | 只持有 token | 从 token 派生，不接受声明 |
| 设备身份 | 只展示状态 | 配对码签发、凭据保管、吊销 |
| Profile 版本 | 只读展示 | 唯一编译者 |
| 本地 Agent 通信 | 不直连（由主服务中转，便于审计） | ✅ |
| 流式渲染 | ✅ | 只负责推事件 |

原则一句话：**前端可以"少显示"，但不能"多拥有"。** 任何"前端已经过滤了所以后端可以返回"的想法都是漏洞。

---

## 10. 内核升级机制（长期跟进 DSH）

### 10.1 三件套 + 流水线

```
services/api/scripts/kernel_upgrade/
  evaluate.py            # 候选版本评估：隔离工作区 npm pack + 静态契约检查
  checks/
    tool_names.py        # 上游新增原生工具名是否与自有工具冲突
    preset.py            # preset 组合是否仍可装配
    session_migration.py # 旧版本建会话 → 候选版本恢复 → 逐字段比对
    contract.py          # 冻结契约是否被动过
    supply_chain.py      # integrity / shasum 校验
  generate_sbom.py       # CycloneDX 清单

services/api/kernel/
  VERSIONS.lock          # 声明版本 + 每包 integrity
  COMPATIBILITY.md       # 升级策略（人读）
  compat-matrix.yaml     # 升级策略（机读，CI 比对，改一个字就报错）
  UPSTREAM_NOTICE.md     # 上游许可与 commit 记录
```

CI：

- 每周定时探测上游新版本并跑评估（只产出报告，不动线上）；
- 任何改动 `kernel/dsh/` 的 PR，强制跑全部 check + 双端回归测试；
- 候选版本**只在隔离工作区评估**，禁止原地替换已安装运行时。

### 10.2 版本纪律

- 全部 DSH 包**精确 pin 同版本**，无 `^` 无 `~`；
- 配置写死 `floating_versions: false`，CI 校验"任何一个包版本不等于当前列车就失败"；
- 维护一个**回滚列车**版本号，升级失败可一键回退。

### 10.3 契约冻结点

冻结的必须是**你自己的**契约，不是 DSH 的：

| 冻结项 | 内容 |
|---|---|
| `zy.agent-kernel.v1` | 命令与句柄模型 |
| `zy.kernel-event.v1` | 事件 envelope |
| `zy.dsh-host.v1` | 宿主 HTTP 协议（主服务 ↔ Node 宿主） |
| `zy.localagent.v1` | 本地 Agent 桥协议（帧格式 + 配对流程） |

上游 API 变化时，**改 `kernel/dsh/adapters/`，不改契约**。这是这套架构能长期跟进的唯一原因。

### 10.4 升级时的必查清单

1. 工具名冲突（上游新增原生工具是否撞名）
2. Preset 组合变化（可能重命名或替换组合）
3. **会话持久化格式**（最高危，可能单向迁移）
4. 事件字段增删（适配器是否需要补映射）
5. **自研事件类型是否带 `ignorable` 标记** —— DSH 的会话持久化**读路径**会拒绝解释含未知事件类型的日志，未打标记会导致该会话**整份读不出来**（见 `03` §7.3）。**宿主启动期必须 fail-fast 断言**，不能等升级时才发现
6. `readBytes` / inline token 上限一类的能力边界变化
7. 磁盘上已存在的会话能否正常恢复并续跑
8. 候选版本跑通镜像 smoke test 后才允许发布固定版本

任何一项失败 → 维持当前版本，产品开发照常推进。**升级失败不阻塞产品**是这条流程能长期活着的前提。

---

## 11. 分期路线

| 阶段 | 目标 | 交付内容 | 验收 |
|---|---|---|---|
| **1. 内核闭环** | 单人能用，跑通一次完整 turn；部署即有客户品牌 | 6 容器；`kernel/` 全套（contract + dsh + profile + inbound + turn）；SSE 对话；一个 `server` 工具；审计落库；**品牌基础**（公司名 + logo + favicon + 主题色 + 公开读取端点 + 管理端设置页 + bootstrap seed） | 一次提问 → 模型推理 → 工具调用 → 答复，全链路事件可回放；登录页显示客户公司名与 logo，且**改品牌不使任何会话失效** |
| **2. 企业能力** | 有知识、有权限、有配额 | `doc-worker`；知识库（分词 + 倒排 + BM25）；资源 ACL；配额；审批；管理端；**品牌文案覆盖**（登录页文案 / 页脚版权行 / 帮助与支持链接） | 两个不同角色的用户，看到的模型 / 知识 / 工具集合不同；中文查询能命中并给出原文位置；客户可在后台自行改品牌，**无需重新构建镜像** |
| **3. 协同 + 本地执行** | 多人协同 + 内网自动化 | 参与者模型；分级投影；文件 ACL；**CLI 本地 Agent**（配对 + 截图 / 点击 / 输入最小闭环 + 效果核验） | 两人同会话，viewer 看不到 owner 的工具参数与文件；本地 Agent 操作一个内网系统并核验成功；拔出网线后能被明确告知而非静默失败 |
| **4. 规模与升级** | 多实例 + 长期跟进 | Redis 注册表与跨实例路由落地；升级工具链与 CI；SBOM；多设备路由 | 用户的本地 Agent 连在 A 实例、turn 跑在 B 实例仍正常；一次完整的内核版本升级演练通过 |
| **5. 检索升级** | 混合检索 | 向量召回 + 融合 + rerank，`retriever.py` 内部替换 | 同一 query 下语义召回明显优于纯全文；调用方零改动 |

**顺序上的一个例外**：如果核心业务场景从一开始就是内网老系统自动化，把阶段 3 的"CLI 本地 Agent 最小版"提到阶段 1 之后立即做——它是你区别于普通聊天产品的唯一理由，晚做等于先做了一个没有差异化的产品。把阶段 2 往后挪。

**品牌基础为什么也在阶段 1**：交付形态是**单租户私有化**（§0.1），阶段 1 就要能给客户装一套演示环境。若此时界面还是厂商自己的名字，客户看到的第一眼就不是"自己的系统"。品牌基础的成本很低（一个单文档集合 + 两个端点 + 一处前端注入 + 一个管理页），但**事后补做的代价高得多**——前端已经到处硬编码，改造要动全站。文案覆盖（阶段 2）则可以从容加。

---

## 12. 品牌与白标配置

产品以**单租户私有化**形态交付（§0.1），每个客户部署都是独立实例。因此**展示层的品牌必须可配置**：客户登录后看到的应当是自己的公司名与 logo，而不是 ZY-Agents。

### 12.1 两层标识，方向相反，不可混用

本节最重要的一条边界，也是最容易被"顺手加个配置项"冲垮的地方。

| 层 | 内容 | 可变性 | 作用范围 |
|---|---|---|---|
| **厂商标识**（§0.5） | 契约命名空间 `zy.*.v1`、token `aud`、kernel preset id、宿主事件类型、Mongo 库名、CLI 命令名 `zy`、状态目录 `~/.zy/`、镜像名、Python 包名、`JWT_ISSUER` | **不可变** | 协议、日志、代码、客户端磁盘、数据库名 |
| **客户品牌**（本节） | 公司名 / 简称 / logo / favicon / 主题色 / 登录页文案 / 页脚版权行 / 帮助与支持链接 | **每部署可配** | 仅 UI 与模型请求的运行时上下文 |

一句可直接使用的判据：**问"这个字符串会不会被写进日志、协议、磁盘路径或数据库名"——会，就是厂商层（不可配）；不会，才是客户层（可配）。**

混用的两个方向都是错的。客户品牌渗进契约命名空间 → 每个部署协议不同，升级与支持退化成 N 份；厂商标识被迫进展示层 → 客户无法白标，与私有化交付的核心预期冲突。

### 12.2 配置边界

**不可品牌化**（对应 `S-61`）：契约命名空间与 `schema_version`、token `aud`、kernel preset id、宿主事件类型名、`MONGO_DB`、CLI 命令名与子命令、客户端状态目录、镜像名前缀、Python 发行包名、`JWT_ISSUER`。

**可品牌化 —— 阶段 1**：显示名（完整 / 简称）、logo（横版 / 方版）、favicon、主题色（主色 + 强调色）、页脚厂商标识开关。

**可品牌化 —— 阶段 2 追加**：登录页标题与欢迎语、页脚版权行、帮助与支持链接、技术支持联系方式。

**明确不做**（防止 scope 蔓延）：不做模板系统（客户不能改页面结构）、不做多语言文案包、不做导出产物（PDF / Word）的页眉页脚、不做通知邮件模板。前三项若确有客户要求，各自单独立项评估。

### 12.3 数据与消费路径

- **唯一存储**：`branding_settings` 单文档集合（见 `02` §2.9）。
- **唯一读取口径**：客户端（`apps/web` / `apps/admin`）**只从服务端读**，源码里**不得**硬编码产品名、公司名或 logo（对应 `S-62`）。这是"换客户部署不需要重新构建镜像"的前提。
- **未认证也要可见**：登录页在登录前就要显示客户品牌，因此 `GET /api/v1/branding` 是**公开端点**，且**只返回白名单字段**（对应 `S-63`）——绝不返回内部配置、密钥或未启用的开关。
- **本地 Agent CLI 不承载品牌**。理由：它的命令名（`zy`）与状态目录（`~/.zy/`）都是厂商层的技术标识、本身不可白标；为一个不面向客户的工具引入品牌读取依赖不划算。CLI 是技术人员用的工具，不是展示界面。

### 12.4 与 Runtime Profile 的关系（关键取舍）

**品牌配置绝不进入 Runtime Profile 的编译输入**（对应 `S-60`）。

理由是 §3.2 的不变量 2：配置一变，`profile_version` 必变，**旧会话自动不可续**。若公司名成为编译输入，客户改一次公司名，**所有进行中的会话会立刻失效**。品牌名的变更频率远高于模型与工具配置，把它放进编译输入，等于把一致性闸门变成误伤源。

但 Agent 又应当知道自己在为哪家公司工作。这由 §3.2 不变量 4 的**一般化**解决：

> 运行时易变信息（品牌名、当前日期、项目记忆、检索结果）**一律不进 Profile**，而是由**模型网关在请求构造时注入**一段 turn 级运行时上下文。

落点：`kernel/inbound/model_endpoint.py` 在转发给模型提供商之前，把品牌信息拼成一段独立的上下文块。它不进内容寻址、不影响 `profile_version`、不改变会话可续性；它**随每次 turn 重新读取**，因此客户改了公司名，**下一个 turn 就生效**。

这条把不变量 4 从"只讲 `local` 工具"提升为一条通则：**Profile 承载"能力与权限"，不承载"当下的信息"。**

### 12.5 厂商层与客户层的交界：页脚标识

页脚默认显示"由 ZY-Agents 提供技术支持"，客户可在管理端关闭（**默认开启，可关**）。**合同层面不强制保留**——私有化客户买的是自己的系统。

常见做法是把 logo 移除开关与"是否强制保留"绑定，代价是直接与"客户白标"这个核心诉求冲突。本节保留那个开关，但**不做强制**：私有化交付中客户对界面的控制权是刚性预期。

### 12.6 部署预置（客户运维的入口）

私有化交付的常见场景是**自动化脚本部署**，而不是"上线后由实施人员在 UI 里点一遍"。因此：

- bootstrap 支持从**挂载的配置文件或环境变量**读入品牌（`BRANDING_SEED_FILE`）；
- seed **幂等**：仅在 `branding_settings` 为空时写入，之后**以库为准**——否则每次重启都会覆盖实施人员在后台的修改；
- 后台修改**不需要重启容器**（下一 turn / 下次页面加载即生效，见 §12.4）。

### 12.7 安全：品牌值是不可信输入（对应 `S-64`）

品牌是**客户可控**的，会进入 HTML、CSS 与文件名，因此按不可信输入处理：

| 输入 | 校验要求 |
|---|---|
| logo / favicon 上传 | 类型按**内容**嗅探（不只看扩展名）、文件与像素尺寸上限；存储文件名由服务端生成，**禁止**请求参数参与路径拼接 |
| 主题色 | 限 `#RRGGBB` 格式，**不接受**任意 CSS 串 |
| 文案字段 | 纯文本，**不得**作为模板表达式执行；有长度上限 |
| 链接字段 | 仅 `https://`，**禁止** `javascript:` / `data:` |

理由是这类字段最终会被拼进 HTML 与 CSS：一个未校验的"主题色"就是一个 CSS 注入入口，一个未限制的 logo 文件名就是一个路径穿越入口。

---

## 13. 遗留待定项

1. **本地 Agent 的目标平台** —— 先只做 Windows，还是同步出 macOS / Linux？（影响打包与测试矩阵）
2. **是否需要托盘常驻与开机自启** —— CLI 阶段不做，若现场抱怨"忘了启动"再加。
3. **是否提供人工录制 → Skill 的能力** —— 协议已预留 `recording_event`，但"录制转可重放 Skill"是一整块工程（工作流缓存是其主要产出）。建议阶段 3 之后单独评估。
4. **向量库选型**（阶段 5 前定）—— Weaviate / pgvector / 直接上 Milvus 的取舍。
5. **多实例部署形态**（阶段 4）—— 同机多进程还是多机；`kernel` 宿主的会话 JSONL 是本地卷，多实例时需要共享卷或按 runtime 亲和路由。
6. **是否做知识库之外的"项目级长期记忆"** —— 常见实现是词频打分召回（project memory 一类），是否值得做取决于产品形态。

### 13.1 阶段 4 前置项（由实现过程中发现，登记备查）

以下不是"设计上有疑问"，而是**实现已落地、但阶段 1 做了简化**的地方。阶段 4 动多实例 / RBAC 时必须回头处理，否则会出事。

7. **审批等待态的跨实例唤醒**（**死锁风险**；触发条件 = 引入 `--workers N` 或多实例的那一刻）
   `governance/approval.py` 的 `_approval_events` 是**进程内 dict**：审批请求阻塞在实例 A 的 `request()`，若管理员的决策请求被负载均衡到实例 B，`decide()` 只更新 Mongo、**唤不醒 A 的 waiter** → 该工具调用**永久挂起**（不是变慢，是永不返回）。

   **当前是安全的，且已核对过启动命令**：`Makefile:61/73` 的 uvicorn 命令**均无 `--workers`**（只有 `--reload`），即单进程。**所以这条不是"阶段 4 才触发"，而是"任何人加上 `--workers 2` 的那一刻就触发"**——包括本地开发为了提速而加 `--workers` 的情况。

   → **加 `--workers` 前必须先改这里**：等待态改成 Redis Pub/Sub 通知或 Redis 阻塞列表（参照 `_approval_events` 处的注释）。多实例部署同理。

8. **`admin` 角色码 = 全权限的捷径要下沉**（阶段 4 RBAC 前置）
   `identity/acl.py::has_permission` 目前对 `admin` 角色码**直接放行**，不区分"管理类授权"与"底线约束类限制"。私有化单租户下这是预期（管理员该能配一切），但一旦引入"**即便管理员也不得做**"的红线（禁止删审计日志、禁止跨租户读取等），本函数拦不住 admin。
   阶段 4 细化 RBAC 时应拆两层：① 角色码→权限码展开后的**授权**判定（admin 也受约束）；② 独立的**底线约束**判定，对 admin 同样生效。

9. **`org_unit_id` 的类型一致性**（阶段 4 迁移前置）
   `02` §2.1 规定 `org_unit_id` 为 ObjectId，但 `RequestContext.org_unit_id` 恒为 **str**（来自 JWT claim），且**所有按 org 的查询都拿它做等值匹配**。因此当前写入侧一律写 str（`identity/seed.py` 亦然）。
   曾尝试让 seed 写原始 ObjectId "对齐 schema"，结果**回归**了已验证环境：登录解析角色、ACL 隔离全部查不到绑定，表现为"登录成功但 roles 恒空"。
   若要统一到 ObjectId，**必须连同 ctx 类型与全部按 org 的查询一起改**，属独立迁移任务，不能只改写入侧。

10. **S-14 字面检测的 CI 口径**（阶段 4 CI 完善）
    "遍历公开 OpenAPI 断言无 `/internal/`"这条自检会**误报**——`/api/openapi.json` 本身需要鉴权（401），匿名遍历取不到路径列表。属检查口径问题（应改为带凭据遍历，或改为扫描源码/路由表），非代码缺陷。

11. **种子账号改名会被 seed 静默回退**（部署注意，非安全漏洞）
   `identity/seed.py::_seed_users` 的幂等判定按 `{org_unit_id, username}` 查找。若实施人员把种子账号 `admin` 改名为 `root`，下次启动时 seed 按 `username="admin"` 查不到 → **重新建一条原始 `admin` 账号**，等于把改名静默回退、且凭空多出一个更高权限的账号。
   正确做法：改名后**同步** `SEED_ADMIN_USERNAME` 配置项；生产更推荐直接删除种子账号（`deploy/SEED-ACCOUNTS.md`）。
