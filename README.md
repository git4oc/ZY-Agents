# ZY-Agents

ZY-Agents 是单租户自托管的企业级多智能体平台，采用**管理面与执行面分离**的架构：主服务负责认证、编排与数据，内核宿主负责 Agent 运行时与本地工具执行。

## 架构概览

```
┌───────────────┐   ┌────────────────┐
│   apps/web    │   │   apps/admin   │
│  Vue3 用户端   │   │   Vue3 管理端   │
└──────┬────────┘   └───────┬────────┘
       │   HTTP (REST)      │
       └─────────┬──────────┘
                 ▼
       ┌─────────────────────┐
       │    services/api     │
       │  FastAPI 业务面      │
       │  认证 / 编排 / 知识库 │
       │  技能 / 配额 / 审计   │
       └─────┬─────────┬─────┘
             │         │
      model gateway   tool gateway   ← 双网关（HMAC 签名回调）
             │         │
             ▼         ▼
       ┌─────────────────────────┐
       │  services/kernel-host   │
       │  Node.js DSH 执行面      │
       │  Runtime Profile 内容寻址│
       │  回合循环 / 工具桥 / 审验  │
       │  审批 / 事件日志          │
       └───────────┬─────────────┘
                   │ 本地 Agent 配对
                   ▼
          ┌────────────────┐
          │   本地 Agent    │
          │  CLI + Chromium │
          └────────────────┘
```

## 核心特性

- **回合制 Agent 编排** — 管理面下发任务，执行面按回合循环驱动模型与工具，状态可恢复
- **知识库检索** — 全文检索起步，可扩展嵌入与重排
- **技能注入** — 技能以 Runtime Profile 内容寻址方式进入 Agent 上下文
- **审批 broker** — 敏感工具调用经人工审批后执行
- **事件日志全链路** — 每个回合、每次工具调用均可追溯
- **白标 / 品牌化** — 展示层品牌（名称 / logo / 主题色）按部署配置，与契约层隔离
- **本地 Agent 配对** — CLI + 独立 Chromium 的本地执行，适配内网无 API 的存量系统

## 快速开始

### 前置依赖

- Python >= 3.11（主服务）
- Node.js >= 22（内核宿主与前端构建）
- MongoDB 与 Redis（本地开发可用 `make mongo-up` / `make redis-up` 拉起，或使用系统安装的实例）

### 启动步骤

```bash
# 1. 生成 .env 密钥（JWT、双网关签名、签名 URL 等）
make bootstrap

# 2. 编辑 .env，配置 OpenAI 兼容模型服务：
#    MODEL_PROVIDER_BASE_URL=...
#    MODEL_PROVIDER_API_KEY=...
#    MODEL_PROVIDER_MODEL=...

# 3. 初始化数据库（索引 + 种子账号 + 服务端工具目录，幂等可重复执行）
make mongo-up
make redis-up
make init-db

# 4. 一键启动全部服务（api / kernel / web + mongo / redis）
make dev
```

> 说明：数据库初始化也可以跳过（API 首次启动时会自动完成同样的初始化，全部幂等）；`make init-db` 的价值是**启动前显式完成并可查看初始化结果**（打印种子账号写入数、集合文档数）。本地运行数据（Mongo 数据文件、`data/`、`.localdata/`）不进仓库，git 跟踪列表只含源码与文档。

启动后访问 **http://127.0.0.1:4173**（用户端）。各服务端口：主服务 API `127.0.0.1:8000`、内核宿主 `127.0.0.1:8101`、用户端 `127.0.0.1:4173`。

停止服务：`make down`；查看日志：`make logs`。

### 种子账号

首次启动且用户集合为空时，自动创建三个演示账号（用户名固定，**密码由环境变量注入、不落代码**）：

| 用户名 | 角色 | 密码变量 |
|---|---|---|
| `admin` | 管理员 | `SEED_ADMIN_PASSWORD` |
| `ops` | 运维（受限管理） | `SEED_OPS_PASSWORD` |
| `member` | 普通成员 | `SEED_MEMBER_PASSWORD` |

首次部署前在 `.env` 中配置这三个密码；生产部署务必修改或删除这些账号。

### 配置说明

- 全部环境变量模板见 [.env.example](.env.example)，每个变量均带注释说明用途与默认值。
- JWT、双网关签名等密钥由 `deploy/bootstrap/generate-secrets.sh` 自动生成（`make bootstrap` 调用），请勿手工编造、勿入库。

## 项目结构

```
ZY-Agents/
├── services/api/           # FastAPI 主服务（业务面：认证、编排、知识库、技能、双网关）
├── services/kernel-host/   # Node.js 22 DSH 内核宿主（执行面：回合循环、工具桥、审批、事件日志）
├── apps/web/               # Vue3 用户端
├── apps/admin/             # Vue3 管理端
├── deploy/                 # 部署脚本与密钥生成
├── docs/                   # 设计文档（六份）
└── Makefile                # 本地开发入口（bootstrap / dev / check 等）
```

## 文档索引

| 文档 | 内容 |
|---|---|
| [00-architecture-spec.md](docs/00-architecture-spec.md) | 架构规格：约束与结论、部署形态、服务分层、双网关、升级机制 |
| [01-engineering-design.md](docs/01-engineering-design.md) | 工程设计：目录树、分层依赖规则、模块接口、装配与编排 |
| [02-data-model-and-api.md](docs/02-data-model-and-api.md) | 数据模型与 API：MongoDB 集合字段、公开 HTTP 路由表 |
| [03-kernel-contract.md](docs/03-kernel-contract.md) | 内核契约：双网关端点与签名方案、宿主协议、版本锁 |
| [04-localagent-protocol.md](docs/04-localagent-protocol.md) | 本地 Agent 协议：配对流程、帧级字段、连接状态机 |
| [05-spec-compliance.md](docs/05-spec-compliance.md) | 合规登记：约束落点与检测手段 |

## 社区讨论

欢迎扫码加入微信群「dsh 企业应用开发技术」，一起交流企业级 Agent 平台架构、DSH 内核与本地工具执行的落地实践：

<p align="center">
  <img src="docs/images/wechat-group-qrcode.jpg" alt="微信群：dsh 企业应用开发技术" width="360">
</p>

## License

[MIT](LICENSE) © ZY-Agents Contributors
