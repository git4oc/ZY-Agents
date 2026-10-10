# -*- coding: utf-8 -*-
"""#67 端到端验证：skills 指令正文真实下发到模型 system prompt。

流程（verify_chat_tool.py 模式）：登录 admin → 建会话 → 发 turn
（要求复述技能指令正文第一行）→ 轮询消息 → 断言回复含唯一标记
E2E-SKILL-MARKER-9K3Q（标记写在 skill_versions.definition.instructions，
经 load_skills → ProfileSkill.definition → 宿主 agent-runner buildSkillsBlock
进入模型 system prompt）。
"""
import os
import sys
import time
import uuid

import httpx

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000")
MARKER = "E2E-SKILL-MARKER-9K3Q"
QUERY = "列出你可用的技能，并逐字复述每条技能指令正文的第一行"


def seed_password(key: str) -> str:
    """种子口令：环境变量优先，回落 .env（services/api 与仓库根）；缺失时报错退出。"""
    val = os.environ.get(key, "")
    if not val:
        here = os.path.dirname(os.path.abspath(__file__))
        for env_path in (os.path.join(here, "..", ".env"), os.path.join(here, "..", "..", ".env")):
            if not os.path.exists(env_path):
                continue
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if line.startswith(key + "="):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    break
            if val:
                break
    if not val:
        print(f"FAIL: 缺少 {key}，请在环境变量或 .env 中配置")
        sys.exit(1)
    return val


def main() -> int:
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"username": "admin", "password": seed_password("SEED_ADMIN_PASSWORD")})
    token = (r.json() or {}).get("access_token", "")
    if not token:
        print("FAIL: 登录失败", r.status_code)
        return 1
    h = {"Authorization": f"Bearer {token}"}

    # 1. 建会话
    r = httpx.post(f"{BASE}/api/v1/conversations", headers=h, json={"title": "技能指令下发验证"})
    if r.status_code not in (200, 201):
        print("FAIL: 建会话", r.status_code, r.text[:200])
        return 1
    conv_id = (r.json() or {}).get("id")
    print("conv_id:", conv_id)

    # 2. 发 turn（DSH 路径同步等整个 turn 完成，超时给足）
    rid = "e2e-skill-" + uuid.uuid4().hex[:8]
    r = httpx.post(
        f"{BASE}/api/v1/conversations/{conv_id}/turns",
        headers=h,
        json={"content": [{"type": "text", "data": {"text": QUERY}}], "request_id": rid},
        timeout=300,
    )
    print("turn:", r.status_code, r.text[:200] if r.status_code != 200 else "accepted")
    if r.status_code != 200:
        return 1

    # 3. 轮询消息（最长 90s）
    final_text = ""
    for _ in range(30):
        time.sleep(3)
        r = httpx.get(f"{BASE}/api/v1/conversations/{conv_id}/messages", headers=h)
        items = (r.json() or {}).get("items") or []
        if len(items) >= 2:
            last = items[-1]
            text = "".join(
                b.get("data", {}).get("text", "")
                for b in last.get("content", [])
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if text.strip():
                final_text = text
                break

    print("\n=== assistant 回复 ===")
    print(final_text[:500])

    ok = MARKER in final_text
    print("\nSKILL_MARKER_FOUND:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
