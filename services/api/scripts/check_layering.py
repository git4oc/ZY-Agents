#!/usr/bin/env python3
"""check_layering.py — 分层依赖守卫（docs/01 §3 / §3.1）。

用 `ast` 解析 import 图（而非正则），对 services/api/app/**/*.py 的每条 import
检查 (源模块, 目标模块) 是否命中禁止表；命中则打印来源文件:行号并 exit 1。

分层禁止表（docs/01 §3.1，也见 00 §2.1）：

    app.*          → kernel/dsh/      禁止（唯一例外 kernel/dsh/gateway.py 自身）
    kernel/        → collaboration/   禁止（回调走 inbound/ 的独立端点）
    capability/    → collaboration/   禁止（工具不感知会话）
    localagent/    → kernel/          禁止（本地 Agent 只经工具网关间接被用）

邀请例外：ALLOWED_EXCEPTIONS 必须保持极短（目标 ≤3 条）。新增例外需在 PR 描述说明。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# ---- 项目根（脚本位于 services/api/scripts/）----
SCRIPTS_DIR = Path(__file__).resolve().parent
API_DIR = SCRIPTS_DIR.parent
APP_DIR = API_DIR / "app"

# ==========================================================================
# 分层禁止表：(源模块前缀, 目标模块前缀) → 说明
# 前缀按「模块段」匹配（段间以 '.' 分隔，前缀为该模块或其后代）。
# ==========================================================================
FORBIDDEN: List[Tuple[str, str, str]] = [
    ("app", "kernel.dsh", "app.* 禁止 import kernel/dsh/（唯一例外 gateway.py）"),
    ("kernel", "collaboration", "kernel/ 禁止 import collaboration/（回调走 inbound/）"),
    ("capability", "collaboration", "capability/ 禁止 import collaboration/（工具不感知会话）"),
    ("localagent", "kernel", "localagent/ 禁止 import kernel/"),
]

# kernel/dsh/ 允许反向依赖任何 app.* 模块（适配器需要调用宿主服务）——正向禁止，
# 反向（dsh → app.*）允许，体现在规则匹配只按「源在前缀、目标在前缀」顺序。
# 例外：kernel/dsh/gateway.py 自身允许 import 自身 nest（不会发生，防御性列出）。
ALLOWED_EXCEPTIONS: List[str] = [
    # gateway.py 是「唯一接触 DSH」的实现，允许它访问 kernel/dsh 内部（self/子包）。
    # 该规则仅为防御，正常不会触发。
]

# 需要忽略的非 app 目录（例如收进 app/ 的 tests/__init__ 桩）
SKIP_PREFIXES: Tuple[str, ...] = ("app.tests",)


# --------------------------------------------------------------------------
def module_for_import(node: ast.Import | ast.ImportFrom) -> str | None:
    """把一条 import 语句目标解析成模块名；相对导入基于当前文件推导。"""
    if isinstance(node, ast.Import):
        return node.names[0].name
    # ast.ImportFrom
    ev = node.module or ""
    if node.level:
        raise ValueError("_resolve handles relative separately")
    return ev


def source_module(path: Path) -> str:
    """把文件的绝对路径转为 app.* 模块名。"""
    rel_to_app = path.relative_to(APP_DIR)
    parts = list(rel_to_app.parts)
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return "app." + ".".join(parts)


def segments(mod: str) -> List[str]:
    return mod.split(".") if mod else []


def prefix_match(prefix: str, target: str) -> bool:
    """prefix 是否为目标模块或其祖先（按段前缀匹配）。"""
    if not prefix:
        return True
    sp = segments(prefix)
    st = segments(target)
    return st[: len(sp)] == sp


def is_relative(node: ast.ImportFrom) -> bool:
    return node.level > 0


def resolve_relative(importer: str, node: ast.ImportFrom, names: List[str]) -> List[str]:
    """把相对导入按 importer 模块解析为绝对模块名。"""
    base_parts = segments(importer)
    # 去掉 __init__ 的『自身段』：相对导入基于包，而非当前文件模块
    # 简化：level=1 指向上级包，此处按常见语义折中处理
    level = node.level
    if len(base_parts) >= level:
        pkg = base_parts[:-level]
    else:
        pkg = []
    base = ".".join(pkg)
    module = node.module or ""
    resolved: List[str] = []
    for n in names:
        if module:
            resolved.append(f"{base}.{module}.{n}".strip("."))
        else:
            resolved.append(f"{base}.{n}".strip("."))
    return resolved


def find_imports(path: Path) -> list[tuple[int, str, list[str]]]:
    """返回 [(lineno, source_module_abs, [target_abs,...])] 的相对导入也解析。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError):
        return []
    importer = source_module(path)
    out: list[tuple[int, str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, importer, [alias.name]))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                targets = resolve_relative(importer, node, [a.name for a in node.names])
            else:
                if not node.module:
                    continue
                targets = [f"{node.module}.{a.name}" for a in node.names]
            out.append((node.lineno, importer, targets))
    return out


def main() -> int:
    if not APP_DIR.is_dir():
        print(f"⚠️ 未找到 {APP_DIR}，跳过分层检查（阶段 1 尚未生成主服务代码）", file=sys.stderr)
        return 0

    violations: List[str] = []
    files = sorted(APP_DIR.rglob("*.py"))

    for path in files:
        src = source_module(path)
        if any(src.startswith(p) for p in SKIP_PREFIXES):
            continue
        for lineno, importer, targets in find_imports(path):
            for target in targets:
                for sp, tp, why in FORBIDDEN:
                    if prefix_match(sp, importer) and prefix_match(tp, target):
                        if importer in ALLOWED_EXCEPTIONS:
                            continue
                        violations.append(
                            f"{path.relative_to(APP_DIR.parent.parent)}:{lineno} → "
                            f"禁止的 import: {importer} -> {target}  ({why})"
                        )

    if violations:
        print("❌ 分层依赖违规：", file=sys.stderr)
        for v in violations:
            print(f"   {v}", file=sys.stderr)
        print(f"   共 {len(violations)} 处。新增例外需在 PR 描述说明并同步 ALLOWED_EXCEPTIONS（目标 ≤3 条）。",
              file=sys.stderr)
        return 1

    print(f"✅ check_layering: 通过（扫描 {len(files)} 个文件，无分层违规）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
