"""身份域幂等种子数据（``identity/seed.py``）。

## 为什么需要

单租户私有化交付是"**部署即用**"的：镜像起来后运维要能立刻登录管理端，
而不是先手工往 Mongo 里插 org_unit / user / role / binding 才能用。
``branding`` 已有 ``seed_if_empty``（``00`` §12.6），身份域缺一个对称的机制。

## ★ 幂等纪律（比"能登录"更重要）

**只在集合为空时写入**。第二次跑、以及每次重启都**不写**——否则会把实施人员
在后台改过的角色 / 绑定悄悄覆盖掉。那比"登录不了"严重得多：登录不了会立刻暴露，
配置被覆盖会静默生效几个月都没人发现（与 ``branding.seed_if_empty`` 同一口径）。

逐集合独立判断：``org_units`` 非空但 ``users`` 为空时，只补 ``users``，
不动已有的组织单元——这样"补种"是幂等且可增量的。

## ⚠️ 种子账号是**开发/演示用**的

- 密码字段 ``password_hash`` 存的是**明文占位**（见 ``_PLACEHOLDER_PASSWORD_*``）。
  阶段 1 的 login 密码校验本来就是占位（``api/endpoints/auth.py`` 只判非空），
  本模块**如实**与之对齐，**不假装**做了 argon2id 哈希，也不引入哈希依赖。
- **生产部署前必须**：改掉种子密码、或删除种子账号。
  凭据同时登记在 ``.env.example`` 与部署文档里，避免"拿到仓库不知道用什么登录"。
"""

from __future__ import annotations

from typing import Any

from app.core import mongo
from app.core.clock import utcnow
from app.core.logging import get_logger

log = get_logger("identity.seed")

#: 集合名（与 ``core/mongo.ensure_indexes`` 一致）
ORG_UNITS = "org_units"
USERS = "users"
ROLES = "roles"
ROLE_BINDINGS = "role_bindings"

#: 根组织单元编码（唯一索引 ``{code:1}``）
ROOT_ORG_CODE = "default"

# ---------------------------------------------------------------------------
# 种子凭据 —— 口令经 SEED_*_PASSWORD 环境变量注入，不落代码
# ---------------------------------------------------------------------------
# 阶段 1 login 的密码校验是**占位**（只判非空），这里不做哈希。
# **不写"已用 argon2 哈希"这种不实注释**——没有做哈希就是没做。
# 口令可由 Settings 覆盖（``SEED_ADMIN_PASSWORD`` / ``SEED_OPS_PASSWORD`` /
# ``SEED_MEMBER_PASSWORD`` 环境变量，本地开发在 .env 配置），
# 便于部署时直接改成自己的凭据而不必改代码。
#: 种子账号默认口令（模块级常量；实际取值经 ``_seed_accounts()`` 走 Settings 覆盖）。
#: ⚠️ 默认值为空串——未注入 SEED_*_PASSWORD 时启动即 fail fast（``validate_critical``）。
SEED_ADMIN_USERNAME = "admin"
SEED_ADMIN_PASSWORD = ""
SEED_MEMBER_USERNAME = "member"
SEED_MEMBER_PASSWORD = ""
SEED_OPS_USERNAME = "ops"
SEED_OPS_PASSWORD = ""

#: 种子账号 → 角色码（驱动 ``_seed_users`` 与 ``_seed_bindings``，加账号只改这一处）
SEED_ACCOUNT_ROLES: tuple[tuple[str, str, str], ...] = (
    # (settings 前缀, 默认用户名, 角色码)
    ("admin", SEED_ADMIN_USERNAME, "admin"),
    ("ops", SEED_OPS_USERNAME, "ops"),
    ("member", SEED_MEMBER_USERNAME, "member"),
)


def _seed_accounts() -> dict[str, str]:
    """取本次要写的账号名与口令（Settings 优先，回落到模块默认值）。

    运维（``ops``）账号与 admin / member 走**同一套**可配置机制
    （``SEED_OPS_USERNAME`` / ``SEED_OPS_PASSWORD``），不另开特例。
    """
    defaults = {
        "admin_username": SEED_ADMIN_USERNAME,
        "admin_password": SEED_ADMIN_PASSWORD,
        "ops_username": SEED_OPS_USERNAME,
        "ops_password": SEED_OPS_PASSWORD,
        "member_username": SEED_MEMBER_USERNAME,
        "member_password": SEED_MEMBER_PASSWORD,
    }
    try:
        from app.settings import get_settings

        s = get_settings()
    except Exception:  # pragma: no cover - 配置不可用时用默认值，保证 seed 仍能跑
        return defaults
    resolved = dict(defaults)
    for key in defaults:
        # seed_ops_username / seed_ops_password …
        attr = f"seed_{key}"
        value = getattr(s, attr, None)
        if value:
            resolved[key] = str(value)
    return resolved

#: 管理端全部权限码（与 ``api/endpoints/admin.py::_Permission.REQUIRED`` 对齐）
ADMIN_PERMISSIONS: list[str] = [
    "org.manage",
    "user.manage",
    "model.manage",
    "tool.manage",
    "skill.manage",
    "device.manage",
    "audit.read",
    "quota.read",
    "acl.manage",
    "brand.manage",
]
#: 普通成员权限码（只读，不含任何 manage）
MEMBER_PERMISSIONS: list[str] = ["knowledge.read"]
#: 运维角色权限码（★ 非 admin 的**受限**管理角色）。
#:
#: 存在的意义：``00`` §11 阶段 2 验收项"**两个不同角色的用户，看到的模型/知识/工具
#: 集合不同**"——修复权限码命名空间错位（P1-2）后，只有真实 seed 里存在一个
#: 非 admin 管理角色，这条验收项才能在部署后**开箱演示**，而不是仅存在于测试夹具。
#:
#: 刻意**只给** ``tool.manage`` + ``device.manage``：既能演示"按权限码放行"，
#: 又能演示"没有 user.manage 就 403"——这正是该验收项要证明的两面。
OPS_PERMISSIONS: list[str] = ["tool.manage", "device.manage"]


async def seed_identity() -> dict[str, Any]:
    """幂等写入身份域种子数据（org_unit / roles / role_bindings / users）。

    每个集合**独立**判断是否为空：为空的才写，非空的原样保留。
    因此可以安全地在每次启动时调用。

    返回本次实际写入的摘要 ``{"org_unit": bool, "roles": int, ...}``，便于日志与测试断言。
    """
    written: dict[str, Any] = {}

    root_id, org_written = await _seed_root_org_unit()
    written["org_unit"] = org_written

    written["roles"] = await _seed_roles(root_id)
    written["users"] = await _seed_users(root_id)
    written["role_bindings"] = await _seed_bindings(root_id)

    if any(written.values()):
        log.info(
            "identity seeded",
            extra={"zy": {"written": written, "root_org_unit_id": root_id}},
        )
    else:
        log.debug("identity seed skipped (collections non-empty)")
    return written


async def _seed_root_org_unit() -> tuple[Any, bool]:
    """确保存在一个根组织单元。已有则**不覆盖**（保护已改的 code/name）。

    返回 ``(root_org_unit_id, 本次是否真的写入)``——第二个值用于让调用方的
    "本次写入摘要"如实反映事实：已存在时必须为 False，否则第二次启动会
    谎报"写了组织单元"，掩盖幂等行为。

    ★ 返回 **str 形态**的 org_unit_id（不是原始 ObjectId）：``RequestContext.org_unit_id``
    是 ``str``（JWT claim 也是字符串），所有按 org 过滤的查询都用它做等值匹配。
    seed 写入的值必须与查询侧类型**一致**，否则 users 里的 org_unit_id 与
    role_bindings 的对不上，登录解析角色会查不到绑定（表现为"登录成功但 roles 为空"）。
    """
    coll = mongo.get_collection(ORG_UNITS)
    existing = await coll.find_one({"code": ROOT_ORG_CODE})
    if existing is not None:
        return str(existing.get("_id")), False
    if await coll.count_documents({}) > 0:
        # 集合非空但没有 default 编码的根：不擅自插，避免与既有组织结构冲突
        log.warning("org_units non-empty but no 'default' root; skipping org seed")
        return None, False
    doc = {
        "name": "默认组织",
        "code": ROOT_ORG_CODE,
        "parent_id": None,
        "status": "active",
        "created_at": utcnow(),
    }
    result = await coll.insert_one(doc)
    return str(result.inserted_id), True


async def _seed_roles(root_org_id: Any) -> int:
    """写入 admin / ops / member 三个角色定义；已存在则跳过（不覆盖已改 permissions）。"""
    if not root_org_id:
        return 0
    coll = mongo.get_collection(ROLES)
    wanted = [
        {"code": "admin", "name": "管理员", "permissions": ADMIN_PERMISSIONS},
        {"code": "ops", "name": "运维", "permissions": OPS_PERMISSIONS},
        {"code": "member", "name": "普通成员", "permissions": MEMBER_PERMISSIONS},
    ]
    count = 0
    for role in wanted:
        existing = await coll.find_one({"org_unit_id": root_org_id, "code": role["code"]})
        if existing is not None:
            continue  # 幂等：已存在则不覆盖（实施人员可能改过 permissions）
        await coll.insert_one(
            {
                "org_unit_id": root_org_id,
                "code": role["code"],
                "name": role["name"],
                "permissions": list(role["permissions"]),
                "created_at": utcnow(),
            }
        )
        count += 1
    return count


async def _seed_users(root_org_id: Any) -> int:
    """写入两个种子账号；已存在则跳过（**不覆盖**已改密码/状态）。

    ⚠️ **不要给种子账号改名**（登记于 ``docs/00-architecture-spec.md`` §13.1）：
    幂等判定按 ``{org_unit_id, username}`` 查找。若把种子账号改名为 ``root``，
    下次启动时这里按 ``username="admin"`` 查不到 → **会重新建一条原始 admin 账号**
    （等于把"改名"这个操作静默回退，且多出一个权限更高的账号）。

    改名的正确做法：改完后**手工同步**对应的配置项
    （``.env`` 的 ``SEED_ADMIN_USERNAME`` / ``SEED_OPS_USERNAME`` /
    ``SEED_MEMBER_USERNAME``），让 seed 与实际账号名一致。
    生产环境更推荐直接删除种子账号（见 ``deploy/SEED-ACCOUNTS.md``）。
    """
    if not root_org_id:
        return 0
    coll = mongo.get_collection(USERS)
    acc = _seed_accounts()
    # 账号清单由 SEED_ACCOUNT_ROLES 驱动（加账号只改那一处）
    wanted = [
        {
            "username": acc[f"{prefix}_username"],
            "display_name": display_name,
            "email": None,
            # ⚠️ 占位：阶段 1 login 只判非空，此处与之一致存明文，**未做哈希**
            "password_hash": acc[f"{prefix}_password"],
        }
        for prefix, _, display_name in (
            ("admin", "admin", "系统管理员"),
            ("ops", "ops", "运维"),
            ("member", "member", "普通成员"),
        )
    ]
    count = 0
    for user in wanted:
        existing = await coll.find_one(
            {"org_unit_id": root_org_id, "username": user["username"]}
        )
        if existing is not None:
            continue  # 幂等：绝不覆盖已存在账号（可能已改密码或停用）
        await coll.insert_one(
            {
                "org_unit_id": root_org_id,
                "username": user["username"],
                "display_name": user["display_name"],
                "email": user["email"],
                "password_hash": user["password_hash"],
                "status": "active",
                "preferences": {},
                "created_at": utcnow(),
            }
        )
        count += 1
    return count


async def _seed_bindings(root_org_id: Any) -> int:
    """把种子账号绑到对应角色；已存在的绑定不重复写。"""
    if not root_org_id:
        return 0
    users = mongo.get_collection(USERS)
    bindings = mongo.get_collection(ROLE_BINDINGS)
    acc = _seed_accounts()
    wanted = [(acc[f"{prefix}_username"], role_code) for prefix, _, role_code in SEED_ACCOUNT_ROLES]
    count = 0
    for username, role_code in wanted:
        user = await users.find_one({"org_unit_id": root_org_id, "username": username})
        if user is None:
            continue  # 用户没被 seed（可能集合非空被跳过），不要造孤儿绑定
        subject_id = user.get("_id")
        existing = await bindings.find_one(
            {
                "org_unit_id": root_org_id,
                "subject_id": subject_id,
                "role_code": role_code,
                "resource_id": None,
            }
        )
        if existing is not None:
            continue  # 幂等
        await bindings.insert_one(
            {
                "org_unit_id": root_org_id,
                "subject_type": "user",
                "subject_id": subject_id,
                "role_code": role_code,
                "scope": "org_unit",
                "resource_type": None,
                "resource_id": None,
                "created_at": utcnow(),
            }
        )
        count += 1
    return count
