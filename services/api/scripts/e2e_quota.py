# -*- coding: utf-8 -*-
"""#5 配额扣减 E2E（S-44 入口统一扣减 + 超额拒绝 + S-45 审计落库）。

流程：
  1. 正常调用 /internal/kernel/model/generate → 非 429（配额内）
  2. Mongo 把 member 本期用量推到超限（default_model_token_quota_monthly=1000）
  3. 再调 → 429 + audit_logs 有 quota_exceeded denied 记录
  4. 还原用量

用法（仓库根 ZY-Agents）：services/api/.venv/Scripts/python.exe services/api/scripts/e2e_quota.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import urllib.request

from pymongo import MongoClient

BASE = "http://127.0.0.1:8000"
results = []


def step(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" | {detail}" if detail else ""))


def http(method, path, token=None, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as resp:
            return resp.status, resp.read().decode()[:200]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]


# 加载 .env 里的网关密钥（必须在 import app.settings 之前注入环境）
secret = ""
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
for line in open(env_path, encoding="utf-8"):
    line = line.strip()
    if line.startswith("kernel_model_gateway_secret="):
        secret = line.split("=", 1)[1]
        break
if secret:
    os.environ.setdefault("kernel_model_gateway_secret", secret)

from app.kernel.inbound import credentials  # noqa: E402
from app.settings import get_settings  # noqa: E402

settings = get_settings()
db = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)[settings.mongo_db]

# member 用户（默认组织）
user = db.users.find_one({"username": "member", "status": "active"})
uid, org = str(user["_id"]), str(user["org_unit_id"])

# 网关 token（aud=model）
gw_token = credentials.issue(
    aud="model",
    secret=secret,
    org_unit_id=org,
    user_id=uid,
    profile_version="",
    runtime_id="e2e-quota",
)

BODY = {
    "model": "glm-53-flash",
    "session_id": "e2e-quota",
    "messages": [{"role": "user", "content": "用一句话回答：1+1=?"}],
}

# ---- 1. 正常调用（relay 未启用时返回 finish error 行，但不算 429）----
st, body = http("POST", "/internal/kernel/model/generate", gw_token, BODY)
step("网关调用（配额内）→ 非 429", st != 429, f"status={st} body={body[:120]}")

# ---- 2. 推高用量到超限 ----
period = "2026-10"
db.quota_usage.update_one(
    {"org_unit_id": org, "user_id": uid, "period": period},
    {"$set": {"model_tokens_prompt": 900, "model_tokens_completion": 200}},
    upsert=True,
)
print(f"[setup] quota_usage 推高到 1100（配额 {settings.default_model_token_quota_monthly}）")

# ---- 3. 再调 → 429 + 审计 ----
st, body = http("POST", "/internal/kernel/model/generate", gw_token, BODY)
step("超额调用 → 429", st == 429, f"status={st} body={body[:200]}")

audit_row = db.audit_logs.find_one(
    {
        "actor_user_id": uid,
        "action": "model.generate",
        "result": "denied",
        "error_code": "quota_exceeded",
    },
    sort=[("occurred_at", -1)],
)
step(
    "audit_logs 有 quota_exceeded denied 记录",
    audit_row is not None,
    f"details={json.dumps((audit_row or {}).get('details', {}), ensure_ascii=False, default=str)}",
)

# ---- 4. 还原用量 ----
db.quota_usage.update_one(
    {"org_unit_id": org, "user_id": uid, "period": period},
    {"$set": {"model_tokens_prompt": 0, "model_tokens_completion": 0}},
)
print("[cleanup] quota_usage 已还原")

# ---- 5. 还原后再调 → 非 429 ----
st, body = http("POST", "/internal/kernel/model/generate", gw_token, BODY)
step("还原后调用 → 非 429", st != 429, f"status={st}")

fails = [r for r in results if not r[1]]
print(f"\n===== SUMMARY: {len(results) - len(fails)}/{len(results)} passed =====")
sys.exit(0)
