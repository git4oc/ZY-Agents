#!/usr/bin/env python3
"""check_settings_sync.py — .env.example 键 ↔ Settings 字段双向一致（docs/01 §6 纪律）。

要求：
  - ``.env.example`` 里出现的每个键，都必须能在 ``app/settings.py`` 的 ``Settings``
    中找到对应字段（小写归一比较，pydantic-settings 默认 case_sensitive=False 且转大写 env 键）；
  - ``Settings`` 里每个字段都必须有 ``.env.example`` 条目，并写默认值。

任一方向缺失即 exit 1。

用法：
  python scripts/check_settings_sync.py [--env .env.example] [--settings app/settings.py]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
API_DIR = SCRIPTS_DIR.parent
REPO_ROOT = API_DIR.parent.parent  # .../ZY-Agents（.env.example 在仓库根）
DEFAULT_ENV = REPO_ROOT / ".env.example"
DEFAULT_SETTINGS = API_DIR / "app" / "settings.py"

# 键归一：env 键转小写即 Settings 字段名（pydantic-settings 约定）。
# 例外：复数/缩写等特殊映射在此登记（目标保持极短）。
ENV_KEY_OVERRIDES: dict[str, str] = {}


def normalize_env_key(key: str) -> str:
    k = key.lower()
    return ENV_KEY_OVERRIDES.get(k, k)


def parse_env_keys(path: Path) -> set[str]:
    """从 .env.example 提取所有形如 KEY=VALUE 的键。"""
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            keys.add(m.group(1))
    return keys


def parse_settings_fields(path: Path) -> set[str]:
    """从 Settings class 提取字段名（类体内的 AnnAssign 目标）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fields: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.add(stmt.target.id)
    return fields


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default=str(DEFAULT_ENV))
    ap.add_argument("--settings", default=str(DEFAULT_SETTINGS))
    args = ap.parse_args(argv)

    env_path = Path(args.env)
    settings_path = Path(args.settings)

    if not env_path.is_file():
        print(f"⚠️ 未找到 {env_path}，跳过 settings-sync 检查", file=sys.stderr)
        return 0
    if not settings_path.is_file():
        print(f"⚠️ 未找到 {settings_path}，跳过 settings-sync 检查（阶段 1 尚未生成 Settings）", file=sys.stderr)
        return 0

    env_keys = {normalize_env_key(k): k for k in parse_env_keys(env_path)}
    settings_fields = parse_settings_fields(settings_path)

    env_lower = set(env_keys)
    missing_in_settings = sorted(env_lower - settings_fields)
    missing_in_env = sorted(settings_fields - env_lower)

    errors: list[str] = []
    for k in missing_in_settings:
        errors.append(f".env.example 键 {env_keys[k]}（{k}）没有对应的 Settings 字段")
    for k in missing_in_env:
        errors.append(f"Settings 字段 {k} 没有 .env.example 条目")

    if errors:
        print("❌ Settings 与 .env.example 不一致：", file=sys.stderr)
        for e in errors:
            print(f"   - {e}", file=sys.stderr)
        print("\n请按 docs/01 §6 纪律对齐：新增键须在 Settings 加字段并写默认值；反之亦然。", file=sys.stderr)
        return 1

    print(f"✅ check_settings_sync: 通过（{len(env_lower)} 个 env 键 ↔ {len(settings_fields)} 个 Settings 字段一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
