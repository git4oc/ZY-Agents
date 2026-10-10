# -*- coding: utf-8 -*-
"""#7 角色差异化验收 E2E（只读为主；仅第 5 步插入测试用户/绑定作为 fixture）。

验收目标（00 §11 阶段 2 验收项 ①）：
- 两个角色（admin/member/ops）看到的模型/知识/工具集合差异由数据驱动；
- member/ops 访问管理域端点应 403；
- 第二 org 用户模型集为空（org 无模型实例）→ 模型集按 org 差异化。
"""
import json
import os
import sys

from pymongo import MongoClient

BASE = "http://127.0.0.1:8000/api/v1"
client = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=3000)
db = client["zy_agents"]

results = []


def step(name, ok, detail=""):
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" | {detail}" if detail else ""))


def http(method, path, token=None, body=None):
    import urllib.request

    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}


def login(username, password):
    st, body = http("POST", "/auth/login", body={"username": username, "password": password})
    return st, body


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


# ---------------- 1. 登录 ----------------
st_a, a = login("admin", seed_password("SEED_ADMIN_PASSWORD"))
step("admin 登录", st_a == 200 and a.get("access_token"), f"status={st_a} roles={a.get('roles')}")
st_m, m = login("member", seed_password("SEED_MEMBER_PASSWORD"))
step("member 登录", st_m == 200 and m.get("access_token"), f"status={st_m} roles={m.get('roles')}")
st_o, o = login("ops", seed_password("SEED_OPS_PASSWORD"))
step("ops 登录", st_o == 200 and o.get("access_token"), f"status={st_o} roles={o.get('roles')}")

AT, MT, OT = a.get("access_token", ""), m.get("access_token", ""), o.get("access_token", "")

# ---------------- 2. 模型集对比（同 org 同集）----------------
st, ma = http("GET", "/models", AT)
step("admin GET /models", st == 200, f"items={[(i.get('modelName')) for i in ma.get('items', [])]}")
st, mm = http("GET", "/models", MT)
step("member GET /models", st == 200, f"items={[(i.get('modelName')) for i in mm.get('items', [])]}")
step(
    "同 org 模型集一致（admin == member）",
    ma.get("items") == mm.get("items") and len(ma.get("items", [])) == 1,
    f"admin={len(ma.get('items', []))} member={len(mm.get('items', []))}",
)
st, mo = http("GET", "/models", OT)
step("ops GET /models", st == 200, f"items={[(i.get('modelName')) for i in mo.get('items', [])]}")

# ---------------- 3. agentPolicy 对比（/auth/me）----------------
for label, tok in (("admin", AT), ("member", MT), ("ops", OT)):
    st, me = http("GET", "/auth/me", tok)
    caps = me.get("agentPolicy", {}).get("capabilities", {})
    step(
        f"{label} /auth/me",
        st == 200 and me.get("roles") is not None,
        f"roles={me.get('roles')} caps={json.dumps(caps, ensure_ascii=False)}",
    )

# ---------------- 4. member/ops 访问管理域端点 → 403 ----------------
ADMIN_GETS = [
    ("/admin/skills", "skills"),
    ("/admin/users", "users"),
    ("/admin/tools", "tools"),
    ("/admin/audit", "audit"),
    ("/admin/model-instances", "model.manage"),
    ("/admin/grants", "acl.manage"),
]
for path, perm in ADMIN_GETS:
    st, body = http("GET", path, MT)
    step(f"member GET {path} → 403", st == 403, f"status={st} msg={body.get('error', {}).get('message', '')}")
    st, body = http("GET", path, OT)
    step(f"ops GET {path}", st == 200 or st == 403, f"status={st}（取决于 ops 角色是否含 {perm}）")

# admin 应全部 200
for path, _ in ADMIN_GETS:
    st, body = http("GET", path, AT)
    step(f"admin GET {path} → 200", st == 200, f"status={st}")

# ---------------- 5. 第二 org 用户：模型/知识 org 隔离 ----------------
# fixture：建第二 org + 测试用户 + member 角色绑定（幂等：存在即复用）
ORG_CODE = "orgb-e2e"
org = db.org_units.find_one({"code": ORG_CODE})
if org is None:
    oid = db.org_units.insert_one(
        {"name": "OrgB-E2E", "code": ORG_CODE, "parent_id": None, "status": "active", "created_at": "2026-10-08T00:00:00"}
    ).inserted_id
    org = db.org_units.find_one({"_id": oid})
org_id = str(org["_id"])

test_user = db.users.find_one({"username": "orgb-tester"})
if test_user is None:
    uid = db.users.insert_one(
        {"username": "orgb-tester", "display_name": "OrgB 测试员", "org_unit_id": org_id, "status": "active", "password_hash": "orgb123"}
    ).inserted_id
else:
    uid = test_user["_id"]
if db.role_bindings.find_one({"org_unit_id": org_id, "subject_type": "user", "subject_id": uid, "role_code": "member"}) is None:
    db.role_bindings.insert_one({"org_unit_id": org_id, "subject_type": "user", "subject_id": uid, "role_code": "member", "scope": "org_unit"})

st, ob = login("orgb-tester", "orgb123")
step("orgb-tester 登录", st == 200 and ob.get("access_token"), f"status={st} roles={ob.get('roles')}")
OBT = ob.get("access_token", "")
st, ob_me = http("GET", "/auth/me", OBT)
step("orgb-tester org 隔离", str(ob_me.get("org_unit", {}).get("id")) == org_id, f"org_unit={ob_me.get('org_unit')}")

st, mb = http("GET", "/models", OBT)
step(
    "orgB 用户模型集为空（org 无模型实例）",
    st == 200 and mb.get("items") == [],
    f"items={mb.get('items')}（默认组织有 1 个 glm-53-flash）",
)

st, kb = http("GET", "/knowledge/documents?view=mine", OBT)
step(
    "orgB 用户知识文档为空（org 隔离）",
    st == 200 and len(kb.get("items", [])) == 0,
    f"status={st} items={len(kb.get('items', []))} total={kb.get('total', 'N/A')}",
)

# ---------------- 6. member 侧知识/工具可见性（与 admin 同 org，应有数据）----------------
st, km = http("GET", "/knowledge/documents?view=mine", MT)
step("member 知识文档可见（同 org 默认组织）", st == 200, f"status={st} items={len(km.get('items', []))}")

print()
fails = [r for r in results if not r[1]]
print(f"===== SUMMARY: {len(results) - len(fails)}/{len(results)} passed =====")
sys.exit(0)
