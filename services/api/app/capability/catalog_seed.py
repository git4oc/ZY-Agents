"""内置 server 工具 → tools 目录同步（``capability/catalog_seed.py``）。

## 为什么需要

registry（``capability/registry.py``）注册的是**执行层**实现；Profile 编译读的是
**目录层**（``tools`` 集合 + ``resource_grants`` 授权，见 ``kernel/profile/catalog.py``）。
两层之间没有桥——目录为空时 ``profile.tools`` 恒为空，内核智能体拿不到任何工具
（聊天里表现为"没有可用的知识库检索工具"）。本模块在启动时把 registry 的 server
工具幂等同步为各 org 的目录行 + 默认授权，使内置工具开箱可用。

## 幂等纪律（与 ``identity/seed.py`` 同口径）

**只补缺失，不覆盖已有**：``tools`` 行按 ``{org_unit_id, name}`` 判断（唯一索引
``tools_org_name_u``），存在即跳过——后台改过的定义不被悄悄覆盖；
``resource_grants`` 按 ``{org, resource_type=tool, resource_id}`` 判断，已有授权
即跳过（deny 优先的授权决策不被冲掉）。

授权契约：``load_tools_for_user`` 用 ``name in granted`` 匹配，故 tool 授权的
``resource_id`` = **工具名**（非 _id）。
"""

from __future__ import annotations

from app.capability import registry
from app.core import mongo
from app.core.clock import utcnow
from app.core.logging import get_logger
from app.identity.seed import ORG_UNITS, USERS
from app.kernel.profile.store import invalidate

log = get_logger("capability.catalog_seed")

_TOOLS = "tools"
_GRANTS = "resource_grants"


async def _all_org_ids() -> list[str]:
    """全部组织 id（org_units 优先；空则退化用 users 的 distinct org）。"""
    orgs = mongo.get_collection(ORG_UNITS)
    ids = [str(r.get("_id")) for r in await orgs.find({}, {"_id": 1}).to_list(length=None)]
    if ids:
        return ids
    users = mongo.get_collection(USERS)
    return [str(v) for v in await users.distinct("org_unit_id")]


async def sync_server_tool_catalog() -> int:
    """把 registry 全部 server 工具同步进各 org 的 tools 目录 + 默认授权。

    :return: 新插入的目录行数（幂等：已存在的不计）。
    """
    tools_impl = registry.all_tools()
    if not tools_impl:
        return 0

    org_ids = await _all_org_ids()
    if not org_ids:
        return 0

    tools_coll = mongo.get_collection(_TOOLS)
    grants_coll = mongo.get_collection(_GRANTS)

    inserted = 0
    grants_inserted = 0
    touched_orgs: set[str] = set()
    for org_id in org_ids:
        for tool in tools_impl:
            row = await tools_coll.find_one(
                {"org_unit_id": org_id, "name": tool.name}, {"_id": 1}
            )
            if row is None:
                await tools_coll.insert_one(
                    {
                        "org_unit_id": org_id,
                        "name": tool.name,
                        "description": tool.description,
                        "category": tool.category,
                        "input_schema": tool.input_schema,
                        "output_schema": tool.output_schema,
                        "risk_level": tool.risk_level,
                        "approval_required": tool.approval_required,
                        "timeout_ms": tool.timeout_ms,
                        "status": "active",
                        "created_at": utcnow(),
                    }
                )
                inserted += 1
                touched_orgs.add(org_id)

            # 默认授权（resource_id = 工具名，见 load_tools_for_user 契约）
            grant_exists = await grants_coll.find_one(
                {"org_unit_id": org_id, "resource_type": "tool", "resource_id": tool.name},
                {"_id": 1},
            )
            if grant_exists is None:
                await grants_coll.insert_one(
                    {
                        "org_unit_id": org_id,
                        "resource_type": "tool",
                        "resource_id": tool.name,
                        "subject_type": "all",
                        "subject_id": None,
                        "effect": "allow",
                        "created_at": utcnow(),
                    }
                )
                grants_inserted += 1
                touched_orgs.add(org_id)

    for org_id in touched_orgs:
        await invalidate(org_id)

    if inserted or grants_inserted:
        log.info(
            "server tool catalog synced",
            extra={"zy": {"inserted": inserted, "grants": grants_inserted, "orgs": len(touched_orgs)}},
        )
    return inserted
