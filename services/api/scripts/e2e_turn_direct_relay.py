"""#18 turn 直连 relay 端到端验证（HTTP 级，真实提供商）。

验证全链路（不经 DSH 宿主）：
  JWT 鉴权 → POST /api/v1/conversations/{id}/turns
  → turns._start_turn_direct（用户消息落库 → 品牌注入 + 历史组装）
  → driver.start_turn_direct_relay（context_shrink → model_relay 转发
    → 真实提供商(dev.ai.example.com glm-53-flash) → V3 事件经回调持久化）
  → assistant 回复落库 → SSE 流读侧投影 → 消息列表核对。

用法（仓库根）：
  bash -c 'set -a; source .env; set +a; services/api/.venv/Scripts/python.exe services/api/scripts/e2e_turn_direct_relay.py'
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8000"


def load_env(path: str) -> None:
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.split("#", 1)[0].strip()
            os.environ.setdefault(k, v)


load_env(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from app.core import mongo  # noqa: E402
from app.core.clock import utcnow  # noqa: E402
from app.identity.auth import generate_access_token  # noqa: E402
from app.settings import get_settings  # noqa: E402


async def main() -> None:
    s = get_settings()
    mongo.connect(s.mongo_uri, s.mongo_db)

    # 1) 准备用户（存在即复用）
    users = mongo.get_collection("users")
    doc = await users.find_one({"username": "e2e-relay"})
    if doc is None:
        r = await users.insert_one(
            {
                "username": "e2e-relay",
                "org_unit_id": "ou-e2e",
                "display_name": "E2E Relay",
                "created_at": utcnow(),
            }
        )
        uid, org = str(r.inserted_id), "ou-e2e"
    else:
        uid, org = str(doc["_id"]), str(doc.get("org_unit_id", "ou-e2e"))
    print(f"[user] {uid} @ {org}")

    token = generate_access_token(settings=s, user_id=uid, org_unit_id=org, roles=frozenset())
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=180) as client:
        # 2) 创建会话
        r = await client.post(
            f"{BASE}/api/v1/conversations",
            json={"title": "e2e-direct-relay"},
            headers=headers,
        )
        r.raise_for_status()
        cid = r.json()["id"]
        print(f"[conv] created {cid}")

        # 3) 发起 turn（直连 relay：真实提供商流式回复）
        req_id = f"req-e2e-{int(time.time())}"
        r = await client.post(
            f"{BASE}/api/v1/conversations/{cid}/turns",
            json={
                "content": [{"type": "text", "data": {"text": "用一句话介绍你自己"}}],
                "request_id": req_id,
            },
            headers=headers,
        )
        print(f"[turn] status={r.status_code}")
        print(f"[turn] body={json.dumps(r.json(), ensure_ascii=False)[:300]}")
        if r.status_code != 200:
            sys.exit(1)

        # 4) SSE 流（持久化事件读侧投影）
        events: list[dict] = []
        async with client.stream(
            "GET", f"{BASE}/api/v1/conversations/{cid}/stream", headers=headers
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
                if len(events) >= 8:
                    break
        print(f"[sse] {len(events)} events: {[e['type'] for e in events]}")
        for e in events[:8]:
            print(f"   cursor={e['cursor']:>3} {e['type']} {json.dumps(e['payload'], ensure_ascii=False)[:110]}")

        # 5) 消息列表（user + assistant 落库核对）
        r = await client.get(f"{BASE}/api/v1/conversations/{cid}/messages", headers=headers)
        msgs = r.json()["items"]
        print(f"[messages] {len(msgs)} 条:")
        for m in msgs:
            text = "".join(
                b.get("data", {}).get("text", "")
                for b in m["content"]
                if isinstance(b, dict) and b.get("type") == "text"
            )
            print(f"   seq={m['seq']} role={m['role']} meta={m['meta'].get('source', '')} text={text[:80]}")

        # 6) 判定
        types = [e["type"] for e in events]
        ok_run = "run.started" in types and "run.completed" in types
        ok_item = "item.delta" in types and "item.completed" in types
        roles = {m["role"] for m in msgs}
        ok_msgs = "user" in roles and "assistant" in roles
        print("[result]", "run-events:", ok_run, "| item-events:", ok_item, "| messages:", ok_msgs)


if __name__ == "__main__":
    asyncio.run(main())
