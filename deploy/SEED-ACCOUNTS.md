# 种子账号与首次登录（开发 / 演示用）

> 对应实现：`services/api/app/identity/seed.py`（幂等 seed）
> 装配位置：`services/api/app/main.py` lifespan 步骤 `e2`

## 为什么需要这个文件

单租户私有化交付是"部署即用"的：镜像起来后运维要能**立刻登录管理端**，
而不是先手工往 Mongo 里插 org_unit / user / role / binding 才能用。

品牌配置已有 `branding.seed_if_empty`（`00` §12.6），身份域此前缺一个对称机制，
导致首次部署后 `/auth/me` 的 `roles` 恒为空 → 管理端所有写操作 403。

## 种子账号

口令不在本文件中——密码由 `SEED_ADMIN_PASSWORD` / `SEED_OPS_PASSWORD` /
`SEED_MEMBER_PASSWORD` 环境变量注入（`deploy/bootstrap` 或 `.env`），
不落在代码与文档中。用户名与角色如下：

| 角色 | 用户名 | 登录后 `/auth/me` 的 `roles` | 权限 |
|---|---|---|---|
| 管理员 | `admin` | `["admin"]` | 管理端全读写（超级用户直通） |
| 运维 | `ops` | `["ops"]` | **仅** `tool.manage` + `device.manage` |
| 普通成员 | `member` | `["member"]` | 可登录；管理端写操作 403 |

### `ops` 角色存在的意义

它是**非 admin 的受限管理角色**，用于开箱演示阶段 2 验收项
「**两个不同角色的用户，看到的模型/知识/工具集合不同**」（`00` §11）。

用 `ops` 登录后可以直观看到"**按权限码放行**"而不是"按角色名一刀切"：

| 操作 | 结果 | 原因 |
|---|---|---|
| `GET /api/v1/admin/tools` | **200** | 有 `tool.manage` |
| `GET /api/v1/admin/devices` | **200** | 有 `device.manage` |
| `POST/PATCH /api/v1/admin/tools/*` | **200** | 有 `tool.manage` |
| `GET /api/v1/admin/users` | **403** | 无 `user.manage` |
| `GET /api/v1/admin/audit` | **403** | 无 `audit.read` |
| `GET /api/v1/admin/model-providers` | **403** | 无 `model.manage` |
| 改品牌 `PATCH /api/v1/admin/branding` | **403** | 无 `brand.manage` |

`/auth/me` 会同时下发 `roles`（角色码）与 `permissions`（权限码，已由
`roles.permissions` 展开）两个字段——`ops` 的 `permissions` 恰为
`["device.manage", "tool.manage"]`。

登录接口：

```
POST /api/v1/auth/login
{ "username": "admin", "password": "<SEED_ADMIN_PASSWORD 环境变量注入的口令>" }
→ { "access_token": "...", "token_type": "bearer", "expires_in": 3600, "roles": ["admin"] }
```

后续请求带 `Authorization: Bearer <access_token>`。

对应的角色定义（`roles` 集合）：

- `admin`：`org.manage` / `user.manage` / `model.manage` / `tool.manage` /
  `skill.manage` / `device.manage` / `audit.read` / `quota.read` / `acl.manage` /
  `brand.manage`（与 `api/endpoints/admin.py::_Permission.REQUIRED` 对齐）
- `member`：`knowledge.read`（只读，不含任何 `*.manage`）

## 🔴 生产部署前必做

**二选一：改掉这两个口令，或直接删除这两个账号。**

原因（必须说清，不粉饰）：阶段 1 的密码校验是**占位实现**——
`api/endpoints/auth.py::login` 只判断口令**非空**，不做哈希比对；
因此 `users.password_hash` 里存的就是**明文**。这两条口令等同于公开凭据。

本轮**刻意没有**把登录改成 argon2id 哈希校验：那是超出本轮范围的功能改动。
`identity/seed.py` 与 login 两侧如实对应，注释中均已标注"占位、未做哈希"，
不存在"看起来做了哈希其实没有"的误导。

后续阶段补齐密码哈希时，`identity/seed.py` 的 `_seed_users` 需同步改为写入哈希值。

## 幂等纪律（比"能登录"更重要）

`seed_identity()` **只在集合为空时写入**，逐集合独立判断：

- `org_units` / `roles` / `role_bindings` / `users` 各自判断是否已有数据；
- 非空的**原样保留**，绝不覆盖实施人员在后台改过的角色、权限或账号状态；
- 因此可以安全地在**每次启动**时调用。

为什么这条比"能登录"更重要：登录不了会**立刻暴露**；
而 seed 覆盖掉后台改动会**静默生效**，几个月都没人发现。
这与 `branding.seed_if_empty` 是同一口径（`00` §12.6）。

已存在同名账号 / 角色 / 绑定时跳过写入，因此**重复部署或重启不会重置**任何配置。
