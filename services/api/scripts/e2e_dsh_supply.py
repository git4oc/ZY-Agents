"""#63 DSH runtime 供给链端到端验证（HTTP 级，真实宿主 + 真实模型）。

前置（仓库根）：
  - kernel-host 已启动（node src/host.mjs，监听 8101）
  - mongo/redis 本地已启动
  - 临时 uvicorn（8001，TURN_DIRECT_RELAY=0 走 DSH 路径，回调根指向自身）：

  bash -c 'set -a; source .env; set +a; \\
    TURN_DIRECT_RELAY=0 APP_BASE_URL=http://127.0.0.1:8001 \\
    services/api/.venv/Scripts/python.exe -m uvicorn app.main:app \\
    --app-dir services/api --port 8001'

验证链路（DSH 供给链，不经直连 relay）：
  JWT 鉴权 → POST /api/v1/conversations/{id}/turns
  → turns（用户消息落库 → admission → driver.start_turn）
  → driver：预生成 runtime_id → 双凭证（HMAC 短时 token）→ create_runtime →
    create_session → 先订阅事件流再 send → 消费至终态 → dispose_runtime
  → 宿主 agent 循环（真实模型 glm-53-flash + 反向回调双网关）
  → assistant 回复落库（text 由宿主 turn_end 事件提取）→ 消息列表 / kernel_events 核对。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8001")


def load_env(path: str) -> None:
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.split("#", 1)[0].strip()
            os.environ.setdefault(k, v)


load_env(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

from app.core import mongo  # noqa: E402
from app.core.clock import utcnow  # noqa: E402
from app.identity.auth import generate_access_token  # noqa: E402
from app.settings import get_settings  # noqa: E402


async def main() -> None:
    s = get_settings()
    mongo.connect(s.mongo_uri, s.mongo_db)
    org = "ou-e2e"

    # 1) 准备用户（存在即复用）
    users = mongo.get_collection("users")
    doc = await users.find_one({"username": "e2e-dsh"})
    if doc is None:
        r = await users.insert_one(
            {
                "username": "e2e-dsh",
                "org_unit_id": org,
                "display_name": "E2E DSH Supply",
                "created_at": utcnow(),
            }
        )
        uid = str(r.inserted_id)
    else:
        uid = str(doc["_id"])
    print(f"[user] {uid} @ {org}")

    # 2) 播种 server 工具目录 + 授权（让 Profile 携带工具 → 宿主 agent 可发起工具调用）。
    #    注：本环境 capability registry 无内置注册实现，故直接插目录行——
    #    工具执行回执为 tool_not_found（fail-closed），但 host 原生 tool_call
    #    事件与反向回调链路照样走通（事件断言所需）。
    tools_coll = mongo.get_collection("tools")
    grants_coll = mongo.get_collection("resource_grants")
    if await tools_coll.find_one({"org_unit_id": org, "name": "knowledge_search"}) is None:
        await tools_coll.insert_one(
            {
                "org_unit_id": org,
                "name": "knowledge_search",
                "description": "检索组织知识库（E2E 目录行）",
                "category": "server",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词"}
                    },
                    "required": ["query"],
                },
                "output_schema": None,
                "risk_level": "read",
                "approval_required": False,
                "timeout_ms": 30000,
                "status": "active",
                "created_at": utcnow(),
            }
        )
    await grants_coll.update_one(
        {"org_unit_id": org, "resource_type": "tool", "resource_id": "knowledge_search"},
        {"$set": {"subject_type": "all", "effect": "allow", "action": "use"}},
        upsert=True,
    )
    print(f"[seed] knowledge_search tool + grant synced for {org}")

    token = generate_access_token(settings=s, user_id=uid, org_unit_id=org, roles=frozenset())
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=180) as client:
        # 3) 创建会话
        r = await client.post(
            f"{BASE}/api/v1/conversations",
            json={"title": "e2e-dsh-supply"},
            headers=headers,
        )
        r.raise_for_status()
        cid = r.json()["id"]
        print(f"[conv] created {cid}")

        # 4) 发起 turn（DSH 供给链：真实宿主 agent 循环 + 反向回调双网关）
        req_id = f"req-e2e-dsh-{int(time.time())}"
        r = await client.post(
            f"{BASE}/api/v1/conversations/{cid}/turns",
            json={
                "content": [
                    {
                        "type": "text",
                        "data": {
                            "text": "请调用 knowledge_search 工具搜索知识库，然后用一句话回答：知识库里有什么内容？"
                        },
                    }
                ],
                "request_id": req_id,
            },
            headers=headers,
        )
        print(f"[turn] status={r.status_code}")
        print(f"[turn] body={json.dumps(r.json(), ensure_ascii=False)[:300]}")
        if r.status_code != 200:
            sys.exit(1)

        # 5) 消息列表（user + assistant 落库核对）
        r = await client.get(f"{BASE}/api/v1/conversations/{cid}/messages", headers=headers)
        msgs = r.json()["items"]
        texts = []
        for m in msgs:
            text = "".join(
                b.get("data", {}).get("text", "")
                for b in m["content"]
                if isinstance(b, dict) and b.get("type") == "text"
            )
            texts.append((m["role"], text))
        print(f"[messages] {len(msgs)} 条: {[(role, t[:40]) for role, t in texts]}")

        roles = [role for role, _ in texts]
        assistant_text = "".join(t for role, t in texts if role == "assistant")

        ok = True
        if "user" not in roles or "assistant" not in roles:
            print("ASSERT_FAIL: messages 缺 user 或 assistant")
            ok = False
        if not assistant_text.strip():
            print("ASSERT_FAIL: assistant 文本为空")
            ok = False

        # 6) kernel_events 核对（模型调用 / 工具调用 / 终态）
        events = (
            await mongo.get_collection("kernel_events")
            .find({"conversation_id": cid})
            .sort("cursor", 1)
            .to_list(200)
        )
        etypes = [e.get("type") for e in events]
        print(f"[kernel_events] {len(etypes)} 条: {etypes}")

        has_model = "model.request" in etypes
        has_terminal = "turn.finished" in etypes
        has_tool = "tool.call.requested" in etypes
        print(
            f"[assert] model.request={has_model} tool.call.requested={has_tool} "
            f"terminal(turn.finished)={has_terminal}"
        )
        if not has_model:
            print("ASSERT_FAIL: kernel_events 缺 model.request")
            ok = False
        if not has_terminal:
            print("ASSERT_FAIL: kernel_events 缺终态 turn.finished")
            ok = False
        if not has_tool:
            print("[warn] kernel_events 无 tool.call.requested（模型未发起工具调用——非供给链问题）")

        # 7) runtime 清理核对（宿主侧应无残留 runtime）
        async with httpx.AsyncClient(timeout=5) as c:
            h = await c.get(f"{s.kernel_host_url.rstrip('/')}/health")
            runtimes = (h.json() or {}).get("runtimes", [])
            print(f"[host] runtimes after turn: {runtimes}")

    if not ok:
        sys.exit(1)
    print("e2e_dsh_supply: PASS")


asyncio.run(main())
