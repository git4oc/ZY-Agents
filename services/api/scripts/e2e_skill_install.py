# -*- coding: utf-8 -*-
"""Skill ZIP 安装 E2E（阶段 2：安装接真）。

流程：
  1. admin 登录 → 构造 SKILL.md ZIP → POST /api/v1/admin/skills/install → 200
  2. 重装同版本 → duplicate=true
  3. 装更高版本（不确认）→ 409 skill_upgrade_confirmation_required
  4. confirm_replace=true → updated=true
  5. 非法包（缺 SKILL.md）→ 400 missing_skill_md
  6. 清理测试数据
"""
import io
import json
import os
import sys
import urllib.request
import urllib.error
import zipfile

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
        with urllib.request.urlopen(req, data=data, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}


def multipart_upload(path, token, file_bytes, filename, fields):
    boundary = "----e2eboundary7d91"
    lines = []
    for k, v in fields.items():
        lines.append(f"--{boundary}")
        lines.append(f'Content-Disposition: form-data; name="{k}"')
        lines.append("")
        lines.append(str(v).lower())
    lines.append(f"--{boundary}")
    lines.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"')
    lines.append("Content-Type: application/zip")
    lines.append("")
    head = "\r\n".join(lines).encode() + b"\r\n"
    tail = f"\r\n--{boundary}--\r\n".encode()
    payload = head + file_bytes + tail
    req = urllib.request.Request(BASE + path, method="POST", data=payload)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}


def build_zip(skill_md: str, extra: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", skill_md)
        for name, content in (extra or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


SKILL_MD_V1 = """---
name: e2e-install-skill
description: E2E 安装验证技能
version: 1.0.0
---

# 用法

这是一个 E2E 测试用的技能说明正文。
"""

SKILL_MD_V2 = SKILL_MD_V1.replace("version: 1.0.0", "version: 2.0.0")

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
step("admin 登录", st == 200, f"status={st}")
AT = body.get("access_token", "")

# ---- 1. �次安装 → 200 ----
st, r1 = multipart_upload("/api/v1/admin/skills/install", AT, build_zip(SKILL_MD_V1), "e2e-install-skill.zip", {"confirm_replace": "false"})
step("首次安装 → 200", st == 200, f"status={st} name={r1.get('name')} version={r1.get('version')} fileCount={r1.get('fileCount')}")

# ---- 2. 重装同版本 → duplicate=true ----
st, r2 = multipart_upload("/api/v1/admin/skills/install", AT, build_zip(SKILL_MD_V1), "e2e-install-skill.zip", {"confirm_replace": "false"})
step("重装同版本 → duplicate=true", st == 200 and r2.get("duplicate") is True, f"status={st} duplicate={r2.get('duplicate')}")

# ---- 3. 更高版本（不确认）→ 409 upgrade confirmation ----
st, r3 = multipart_upload("/api/v1/admin/skills/install", AT, build_zip(SKILL_MD_V2), "e2e-install-skill.zip", {"confirm_replace": "false"})
detail3 = r3.get("detail") or {}
step(
    "更高版本不确认 → 409 skill_upgrade_confirmation_required",
    st == 409 and detail3.get("code") == "skill_upgrade_confirmation_required",
    f"status={st} code={detail3.get('code')} upgrade={json.dumps(detail3.get('upgrade', {}), ensure_ascii=False)}",
)

# ---- 4. confirm_replace=true → updated=true ----
st, r4 = multipart_upload("/api/v1/admin/skills/install", AT, build_zip(SKILL_MD_V2), "e2e-install-skill.zip", {"confirm_replace": "true"})
step(
    "confirm_replace → updated=true version=2.0.0",
    st == 200 and r4.get("updated") is True and r4.get("version") == "2.0.0",
    f"status={st} updated={r4.get('updated')} version={r4.get('version')}",
)

# ---- 5a. 非法包（有 SKILL.md 但缺 frontmatter）→ 400 missing_frontmatter ----
st, r5 = multipart_upload("/api/v1/admin/skills/install", AT, build_zip("无 frontmatter 的普通文件"), "bad.zip", {"confirm_replace": "false"})
detail5 = r5.get("detail") or {}
step(
    "SKILL.md 缺 frontmatter → 400 missing_frontmatter",
    st == 400 and detail5.get("code") == "missing_frontmatter",
    f"status={st} code={detail5.get('code')}",
)

# ---- 5b. ZIP 里没有 SKILL.md → 400 missing_skill_md ----
buf5b = io.BytesIO()
with zipfile.ZipFile(buf5b, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("readme.txt", "没有 SKILL.md 的包")
st, r5b = multipart_upload("/api/v1/admin/skills/install", AT, buf5b.getvalue(), "bad2.zip", {"confirm_replace": "false"})
detail5b = r5b.get("detail") or {}
step(
    "缺 SKILL.md → 400 missing_skill_md",
    st == 400 and detail5b.get("code") == "missing_skill_md",
    f"status={st} code={detail5b.get('code')}",
)

# ---- 6. 落库核对（skills + skill_versions）----
from pymongo import MongoClient

from app.settings import get_settings

s = get_settings()
db = MongoClient(s.mongo_uri, serverSelectionTimeoutMS=3000)[s.mongo_db]
installed = db.skills.find_one({"key": "e2e-install-skill"})
step(
    "skills 落库（current_version=2.0.0）",
    installed is not None and installed.get("current_version") == "2.0.0",
    f"version={installed.get('current_version') if installed else None}",
)
versions = list(db.skill_versions.find({"skill_id": installed["_id"]})) if installed else []
step("skill_versions 落 2 条（1.0.0 + 2.0.0）", len(versions) == 2, f"count={len(versions)}")

# ---- 7. 清理 ----
if installed:
    db.skills.delete_one({"_id": installed["_id"]})
    db.skill_versions.delete_many({"skill_id": installed["_id"]})
    print("[cleanup] 测试技能已清理")

fails = [r for r in results if not r[1]]
print(f"\n===== SUMMARY: {len(results) - len(fails)}/{len(results)} passed =====")
sys.exit(0)
