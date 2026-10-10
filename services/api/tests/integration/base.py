"""集成回归套件（活服务器；服务未运行时自动 skip）。

分层：``tests/`` 顶层的单元测试跑纯函数（stub，3s）；本包跑**活服务器**
HTTP 全链路——覆盖阶段 2 全部真实缺陷面（见 ``tests/README.md`` 覆盖表）。

运行（从 ``services/api`` CWD）::

    ./.venv/Scripts/python.exe -m unittest discover -s tests -v

服务未运行时本包整体 skip，不阻塞单元测试。
"""

from __future__ import annotations

import json
import os
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8000"
_PROXY_BASE = "http://127.0.0.1:3000"


def http(method: str, path: str, token: str | None = None, body: dict | None = None, base: str = BASE) -> tuple[int, dict]:
    """极简 JSON HTTP 助手（返回 (status, body)；错误体也解析）。"""
    req = urllib.request.Request(base + path, method=method)
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


def server_alive() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/healthz", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def login(username: str, password: str) -> str:
    st, body = http("POST", "/api/v1/auth/login", body={"username": username, "password": password})
    if st != 200:
        raise unittest.SkipTest(f"登录失败（{st}），跳过集成测试")
    return str(body.get("access_token", ""))


def _seed_password(env_key: str) -> str:
    """种子口令：环境变量优先，回落 services/api/.env（测试从该 CWD 运行）。

    缺值返回空串——``login`` 侧会 SkipTest 并注明原因，不误报 FAIL。
    """
    val = os.environ.get(env_key, "")
    if not val:
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(env_key + "="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return val


def admin_token() -> str:
    return login("admin", _seed_password("SEED_ADMIN_PASSWORD"))


def member_token() -> str:
    return login("member", _seed_password("SEED_MEMBER_PASSWORD"))


def unique_suffix() -> str:
    return uuid.uuid4().hex[:8]


def mongo_db() -> Any:
    """pymongo 直连句柄（清理无删除端点的集合；测试从 services/api CWD + venv python 运行）。

    单元测试的 ``_zytest_stubs.py`` 会把 pymongo 桩注入 sys.modules 并污染整个
    测试进程——这里检测到桩（无 MongoClient 属性）就清掉重导真 pymongo。
    """
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/api 根 → app.* 可导入
    import pymongo

    if not hasattr(pymongo, "MongoClient"):
        for name in [n for n in sys.modules if n == "pymongo" or n.startswith("pymongo.")]:
            del sys.modules[name]
        importlib.invalidate_caches()
        pymongo = importlib.import_module("pymongo")

    from app.settings import get_settings

    s = get_settings()
    return pymongo.MongoClient(s.mongo_uri, serverSelectionTimeoutMS=3000)[s.mongo_db]


def gateway_secret(kind: str = "tool") -> str:
    """internal kernel HMAC 网关 secret（读 services/api/.env，与 e2e 脚本同口径）。

    模型/工具网关是**两个独立 secret**（域分离设计）：
    ``kind="tool"`` → ``kernel_tool_gateway_secret``；``kind="model"`` → ``kernel_model_gateway_secret``。
    """
    import os

    env_path = Path(__file__).resolve().parents[2] / ".env"
    prefix = f"kernel_{kind}_gateway_secret="
    secret = ""
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(prefix):
                secret = line.split("=", 1)[1]
                break
    if secret:
        os.environ.setdefault(f"kernel_{kind}_gateway_secret", secret)
    return secret


def gateway_claims_id(username: str = "admin") -> tuple[str, str]:
    """(org_unit_id, user_id)——网关 token 签发所需声明（从 users 集合取）。"""
    db = mongo_db()
    row = db.users.find_one({"username": username, "status": "active"})
    if row is None:
        raise unittest.SkipTest(f"种子用户 {username} 不存在")
    return str(row["org_unit_id"]), str(row["_id"])


def multipart_zip(path: str, token: str, zip_bytes: bytes, filename: str, confirm_replace: bool = False) -> tuple[int, dict]:
    """multipart ZIP 上传（走 proxy 形态的路径，浏览器同款）。"""
    boundary = "----itestboundary"
    parts = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/zip\r\n\r\n'.encode()
        + zip_bytes
        + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="confirm_replace"\r\n\r\n{str(confirm_replace).lower()}\r\n--{boundary}--\r\n'.encode()
    )
    req = urllib.request.Request(BASE + path, method="POST", data=parts)
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


def make_skill_zip(name: str, version: str = "1.0.0") -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", f"---\nname: {name}\ndescription: itest skill\nversion: {version}\n---\n\n# {name} 指令正文\n")
    return buf.getvalue()


class IntegrationTestCase(unittest.TestCase):
    """活服务器集成基类：服务不可达 → 整类 skip。"""

    @classmethod
    def setUpClass(cls) -> None:
        if not server_alive():
            raise unittest.SkipTest("后端未运行（http://127.0.0.1:8000/healthz 不可达）")

    def setUp(self) -> None:
        self._cleanup: list[tuple[str, str]] = []  # (method, path) 逆序回放

    def register_cleanup(self, method: str, path: str, token: str | None = None) -> None:
        self._cleanup.append((method, path, token))

    def tearDown(self) -> None:
        for entry in reversed(self._cleanup):
            method, path, token = (entry + (None,))[:3]
            try:
                http(method, path, token=token)
            except Exception:
                pass
