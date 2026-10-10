"""数据库初始化脚本（开源用户入口）。

用法（仓库根目录）：
    make init-db

或手动（在 services/api 目录）：
    ./.venv/Scripts/python.exe scripts/init_db.py

做什么（与 API 启动时的自动初始化一致，全部幂等，可重复执行）：
    1. 连接 MongoDB（MONGO_URI / MONGO_DB 来自 .env，经 make bootstrap 生成）
    2. ensure_indexes：创建全部集合索引
    3. seed_identity：种子账号（admin / ops / member，密码经 SEED_*_PASSWORD 环境变量注入）
    4. capability bootstrap：服务端工具目录（http_fetch / knowledge_search）
    5. branding seed_if_empty：默认品牌

前置：MongoDB / Redis 已启动（make mongo-up && make redis-up 或系统安装）。
说明：不初始化任何业务数据——本地运行数据（.localdata/、data/）不进仓库。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 脚本位于 services/api/scripts/，app 包在 services/api/app
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def run() -> int:
    from app.branding.settings import seed_if_empty
    from app.capability import registry
    from app.core import mongo
    from app.core.mongo import ensure_indexes
    from app.identity.seed import seed_identity
    from app.settings import get_settings

    settings = get_settings()
    db = mongo.connect(settings.mongo_uri, settings.mongo_db)
    print(f"[1/5] mongo connected: {settings.mongo_uri} / {settings.mongo_db}")

    ensure_indexes()
    print(f"[2/5] indexes ensured ({len(mongo.INDEX_SPECS)} collections)")

    written = await seed_identity()
    print(f"[3/5] identity seeded: {written} accounts (admin / ops / member)")

    registry.bootstrap()
    print("[4/5] capability bootstrap done (http_fetch / knowledge_search)")

    seeded = await seed_if_empty(settings.branding_seed_file)
    print(f"[5/5] branding seeded: {'yes' if seeded else 'already present (skipped)'}")

    print("--- collection counts ---")
    for name in ("org_units", "users", "roles", "role_bindings", "tools"):
        count = await db[name].count_documents({})
        print(f"    {name}: {count}")

    await mongo.close()
    print("done. 数据库初始化完成，可启动 API（make dev 或 make api）。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        sys.exit(130)
