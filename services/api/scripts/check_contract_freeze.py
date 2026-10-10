#!/usr/bin/env python3
"""check_contract_freeze.py — 契约冻结校验（docs/01 §3.2）。

对 ``app/kernel/contract/`` 下每个文件计算 **AST 级签名摘要**（类名、字段名、字段类型、
方法名、枚举成员；以及 versions.py 的常量名/值），与 `kernel/contract/.frozen.json` 比对：

  - **新增**字段或方法  → 警告（向后兼容的扩展）
  - **删除或改类型**    → 失败（破坏性变更，必须升 schema_version）
  - **versions.py 常量值变化** → 失败（除非同时更新 .frozen.json 并在 PR 说明）

用法：
  python scripts/check_contract_freeze.py            # 校验当前冻结基线
  python scripts/check_contract_freeze.py --generate # 重新生成 .frozen.json（首次建立/受控重冻结）
  python scripts/check_contract_freeze.py --baseline PATH  # 指定基线文件
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict

# ---- 路径 ----
SCRIPTS_DIR = Path(__file__).resolve().parent
CONTRACT_DIR = SCRIPTS_DIR.parent / "app" / "kernel" / "contract"
DEFAULT_BASELINE = CONTRACT_DIR / ".frozen.json"
BASELINE_NAME = ".frozen.json"


def _unparse(node: ast.AST | None) -> str:
    """把注解 node 转成稳定字符串（忽略行号/注释）。"""
    if node is None:
        return ""
    return ast.unparse(node)


def class_signature(cls: ast.ClassDef) -> Dict[str, Any]:
    """提取单个类的签名：字段名→注解类型字符串、方法名列表、枚举成员名→值。"""
    fields: Dict[str, str] = {}
    methods: list[str] = []
    members: Dict[str, str] = {}

    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields[stmt.target.id] = _unparse(stmt.annotation)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(stmt.name)
        elif (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Constant)
        ):
            val = stmt.value.value
            if isinstance(val, (str, int, float, bool)) or val is None:
                members[stmt.targets[0].id] = repr(val)

    methods.sort()
    return {"fields": fields, "methods": methods, "members": members}


def is_constant_name(name: str) -> bool:
    return name.isupper() and "_" in name or name.isupper()


def module_signature(path: Path) -> Dict[str, Any]:
    """计算单文件 AST 签名。返回 {constants, classes}。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants: Dict[str, str] = {}
    classes: Dict[str, Dict[str, Any]] = {}

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = class_signature(node)
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and is_constant_name(node.targets[0].id)
            and isinstance(node.value, ast.Constant)
        ):
            val = node.value.value
            if isinstance(val, str):
                constants[node.targets[0].id] = val

    return {"constants": dict(sorted(constants.items())), "classes": classes}


def collect_signatures() -> Dict[str, Any]:
    """收集 contract 目录全量签名。key=文件名（相对 contract dir）。"""
    out: Dict[str, Any] = {}
    for path in sorted(CONTRACT_DIR.glob("*.py")):
        out[path.name] = module_signature(path)
    return out


def compare(current: Dict[str, Any], baseline: Dict[str, Any]) -> list[str]:
    """返回 [ (severity, message) ]；severity ∈ {WARN, FAIL}。"""
    results: list[tuple[str, str]] = []

    for fname in sorted(set(current) | set(baseline)):
        cur = current.get(fname)
        base = baseline.get(fname)
        if cur is None:
            results.append(("FAIL", f"{fname}: 契约被删除（整文件缺失）"))
            continue
        if base is None:
            results.append(("WARN", f"{fname}: 新增契约文件（向后兼容扩展）"))
            continue

        cur_c = cur.get("constants", {})
        base_c = base.get("constants", {})
        for k in sorted(set(cur_c) | set(base_c)):
            if k not in cur_c:
                results.append(("FAIL", f"{fname}: 常量 {k} 被删除"))
            elif k not in base_c:
                results.append(("WARN", f"{fname}: 新增常量 {k}（向后兼容扩展）"))
            elif cur_c[k] != base_c[k]:
                results.append(
                    ("FAIL", f"{fname}: 常量值变化 {k}: {base_c[k]!r} → {cur_c[k]!r}"
                             "（破坏性变更，须更新 .frozen.json 并在 PR 说明）")
                )

        cur_cls = cur.get("classes", {})
        base_cls = base.get("classes", {})
        for cls_name in sorted(set(cur_cls) | set(base_cls)):
            cc = cur_cls.get(cls_name)
            bc = base_cls.get(cls_name)
            if cc is None:
                results.append(("FAIL", f"{fname}: 类 {cls_name} 被删除"))
                continue
            if bc is None:
                results.append(("WARN", f"{fname}: 新增类 {cls_name}（向后兼容扩展）"))
                continue

            # enum 成员
            cm, bm = cc.get("members", {}), bc.get("members", {})
            for k in sorted(set(cm) | set(bm)):
                if k not in cm:
                    results.append(("FAIL", f"{fname}.{cls_name}: 枚举成员 {k} 被删除"))
                elif k not in bm:
                    results.append(("WARN", f"{fname}.{cls_name}: 新增枚举成员 {k}（向后兼容扩展）"))
                elif cm[k] != bm[k]:
                    results.append(("FAIL", f"{fname}.{cls_name}: 枚举成员值变化 {k}: {bm[k]} → {cm[k]}"))

            # 字段（名 → 类型）
            cf, bf = cc.get("fields", {}), bc.get("fields", {})
            for k in sorted(set(cf) | set(bf)):
                if k not in cf:
                    results.append(("FAIL", f"{fname}.{cls_name}: 字段 {k} 被删除"))
                elif k not in bf:
                    results.append(("WARN", f"{fname}.{cls_name}: 新增字段 {k}（向后兼容扩展）"))
                elif cf[k] != bf[k]:
                    results.append(("FAIL", f"{fname}.{cls_name}: 字段类型变化 {k}: {bf[k]} → {cf[k]}"))

            # 方法
            cmeth, bmeth = set(cc.get("methods", [])), set(bc.get("methods", []))
            for k in sorted(cmeth - bmeth):
                results.append(("WARN", f"{fname}.{cls_name}: 新增方法 {k}（向后兼容扩展）"))
            for k in sorted(bmeth - cmeth):
                results.append(("FAIL", f"{fname}.{cls_name}: 方法 {k} 被删除"))

    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--generate", action="store_true", help="重新生成 .frozen.json 基线")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE), help="基线文件路径")
    args = ap.parse_args(argv)

    if not CONTRACT_DIR.is_dir():
        print(f"⚠️ 未找到 {CONTRACT_DIR}，跳契约冻结校验（阶段 1 尚未生成契约代码）", file=sys.stderr)
        return 0

    baseline_path = Path(args.baseline)
    current = collect_signatures()

    if args.generate:
        baseline_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"✅ 已生成契约冻结基线: {baseline_path}（{len(current)} 个文件）")
        return 0

    if not baseline_path.is_file():
        print(
            f"❌ 未找到冻结基线 {baseline_path}。首次建立请先执行:\n"
            f"   python scripts/check_contract_freeze.py --generate",
            file=sys.stderr,
        )
        return 1

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ 基线无法解析: {e}", file=sys.stderr)
        return 1

    results = compare(current, baseline)
    has_fail = any(sev == "FAIL" for sev, _ in results)
    has_warn = any(sev == "WARN" for sev, _ in results)

    for sev, msg in results:
        prefix = "❌" if sev == "FAIL" else "⚠️"
        print(f"{prefix} {msg}")

    if has_fail:
        print(f"\n❌ 契约存在破坏性变更（{sum(1 for s, _ in results if s == 'FAIL')} 处）。"
              "删除/改类型必须升 schema_version 并更新 .frozen.json。", file=sys.stderr)
        return 1
    if has_warn:
        print(f"\n⚠️ 契约存在向后兼容扩展（{sum(1 for s, _ in results if s == 'WARN')} 处），"
              "允许但请确认是否为预期新增。")
    print("✅ check_contract_freeze: 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
