"""主服务 import 冒烟（验证全链路模块可加载）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def load_env(path):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


load_env(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from app.main import app  # noqa: E402

print("app.main import OK")
print("routes:", len(app.routes))
http = [r for r in app.routes if hasattr(r, "methods")]
print("http routes:", len(http))
