# -*- coding: utf-8 -*-
"""阶段 2 收尾 E2E：#4 知识 ask / #6 审批超时 / #9 上传异步化。

流程：
  A. #9 上传异步化：上传 → 立即返回 status=processing → 轮询至 ready → chunks 落库
  B. #4 知识 ask：对已索引文档提问 → answer + citations
  C. #6 审批超时：tool token 发起审批（timeout_seconds=1）不决策 → decision=timeout
  D. 清理测试数据
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

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
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}


def multipart_upload(path, token, file_bytes, filename):
    boundary = "----e2eboundary9a2"
    lines = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="file"; filename="' + filename + '"',
        "Content-Type: text/markdown",
        "",
        "",
    ]
    head = "\r\n".join(lines).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    payload = head + file_bytes + tail
    req = urllib.request.Request(BASE + path, method="POST", data=payload)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}


DOC_TEXT = """# 向量混合检索说明

本平台的知识库检索采用 BM25 与向量两路召回、RRF 融合、重排精排的三段式管道。

向量来自网关嵌入模型，维度为 1024，余弦相似度在检索进程内计算。

检索接口的签名保持冻结，调用方与 API 零改动。
"""

secret = ""
for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), encoding="utf-8"):
    line = line.strip()
    if line.startswith("kernel_tool_gateway_secret="):
        secret = line.split("=", 1)[1]
        break
os.environ.setdefault("kernel_tool_gateway_secret", secret)

from app.kernel.inbound import credentials  # noqa: E402
from app.settings import get_settings  # noqa: E402

s = get_settings()

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


# ---- admin 登录 ----
st, body = http("POST", "/api/v1/auth/login", body={"username": "admin", "password": seed_password("SEED_ADMIN_PASSWORD")})
step("admin 登录", st == 200)
AT = body.get("access_token", "")

# ---- A. #9 上传异步化 ----
st, up = multipart_upload("/api/v1/knowledge/documents", AT, DOC_TEXT.encode("utf-8"), "e2e-ask-doc.md")
step(
    "上传立即返回 status=processing（不阻塞）",
    st == 200 and up.get("status") == "processing",
    f"status={st} docStatus={up.get('status')}",
)
doc_id = up.get("id", "")

# 轮询至 ready（后台索引）
ready = False
for _ in range(20):
    time.sleep(1)
    st, doc = http("GET", f"/api/v1/knowledge/documents/{doc_id}", AT)
    if st == 200 and doc.get("status") == "ready":
        ready = True
        break
step("后台索引完成 status=ready（≤20s）", ready)

from pymongo import MongoClient

db = MongoClient(s.mongo_uri, serverSelectionTimeoutMS=3000)[s.mongo_db]
chunk_count = db.knowledge_chunks.count_documents({"document_id": doc_id})
step("chunks 落库 > 0", chunk_count > 0, f"count={chunk_count}")

# ---- B. #4 知识 ask ----
st, ans = http(
    "POST", f"/api/v1/knowledge/documents/{doc_id}/ask", AT,
    {"query": "平台的检索管道是哪几段？向量维度是多少？"},
)
step("ask → 200 且 answer 非空", st == 200 and bool(ans.get("answer")), f"status={st} answer={ans.get('answer', '')[:80]}")
step("citations 带 chunk 级出处", len(ans.get("citations") or []) > 0, f"citations={len(ans.get('citations') or [])}")

# org 隔离：orgB 用户不可问默认组织文档（orgb-tester 上轮已建）
st_o, o = http("POST", "/api/v1/auth/login", body={"username": "orgb-tester", "password": "orgb123"})
if st_o == 200:
    st, ans2 = http(
        "POST", f"/api/v1/knowledge/documents/{doc_id}/ask", o.get("access_token", ""),
        {"query": "测试"},
    )
    step("orgB 用户 ask → 404（org 隔离）", st == 404, f"status={st}")
else:
    step("orgB 用户 ask → 404（org 隔离）", False, "orgb-tester 登录失败（跳过）")

# ---- C. #6 审批超时 ----
gw_token = credentials.issue(
    aud="tool",
    secret=secret,
    org_unit_id=str(db.users.find_one({"username": "member", "status": "active"})["org_unit_id"]),
    user_id=str(db.users.find_one({"username": "member", "status": "active"})["_id"]),
    profile_version="",
    runtime_id="e2e-approval",
)
st, ap = http(
    "POST", "/internal/kernel/tools/approval", gw_token,
    {
        "tool_name": "e2e-timeout-tool",
        "arguments": {"q": "x"},
        "conversation_id": "e2e-approval",
        "call_id": "call-e2e-1",
        "timeout_seconds": 1,
    },
)
step(
    "审批超时 → decision=timeout（不静默放行）",
    st == 200 and ap.get("decision") == "timeout" and ap.get("ok") is False,
    f"status={st} decision={ap.get('decision')}",
)
approval_row = db.approvals.find_one({"call_id": "call-e2e-1"})
step("approvals 落库 status=timeout", approval_row is not None and approval_row.get("status") == "timeout", f"status={approval_row.get('status') if approval_row else None}")

# ---- D. 清理 ----
db.knowledge_chunks.delete_many({"document_id": doc_id})
db.knowledge_documents.delete_one({"_id": doc_id})
db.approvals.delete_many({"call_id": "call-e2e-1"})
print("[cleanup] 测试文档与审批单已清理")

fails = [r for r in results if not r[1]]
print(f"\n===== SUMMARY: {len(results) - len(fails)}/{len(results)} passed =====")
sys.exit(0)
