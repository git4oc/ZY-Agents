#!/usr/bin/env python3
"""evaluate.py — DSH 候选版本评估（隔离工作区，report-only）。

阶段 4 升级工具链入口（docs/00 §10、docs/03 §9）。阶段 1 仅提供**骨架**：
- 在隔离工作区 npm pack 候选版本；
- 静态契约检查（tool_names / preset / session_migration / contract / supply_chain，位于 checks/）；
- 只产出报告，不允许原地替换已安装运行时（mutate_installed_runtime_in_place=false）。

用法：
  python scripts/kernel_upgrade/evaluate.py [候选版本|auto]
退出码：0 = 报告已产出（无论是否建议升级）；非 0 = 评估过程本身失败。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

# 当前锁定列车（与 VERSIONS.lock / compat-matrix.yaml 一致）
LOCKED_TRAIN = "0.2.0-rc.1"


def probe_candidate(auto: bool, explicit: str | None) -> str | None:
    """探测候选版本：显式给定或用 npm dist-tags 探测 latest/next。"""
    if explicit:
        return explicit
    if not auto:
        return None
    try:
        # 只读探测上游 next 列车；失败不代表评估失败
        out = subprocess.run(
            ["npm", "view", "@deepseek-ai/dsh", "dist-tags.next"],
            capture_output=True, text=True, timeout=60,
        )
        tag = out.stdout.strip()
        return tag if tag else None
    except Exception:
        return None


def evaluate_in_isolation(candidate: str) -> dict:
    """在隔离工作区评估候选版本（绝不触碰 node_modules / lock / 已安装运行时）。"""
    report_dir = os.environ.get("KERNEL_UPGRADE_REPORT", ".kernel-upgrade-report")
    os.makedirs(report_dir, exist_ok=True)
    packed = False
    error: str | None = None
    ws = tempfile.mkdtemp(prefix="dsh-eval-")
    try:
        # 在隔离工作区 npm pack，绝不 install 到本地 node_modules
        r = subprocess.run(
            ["npm", "pack", f"@deepseek-ai/dsh@{candidate}", "--pack-destination", ws],
            capture_output=True, text=True, timeout=240,
        )
        packed = bool(r.returncode == 0)
        if not packed:
            error = r.stderr.strip() or "npm pack 非零退出（网络或包不存在）"
    except FileNotFoundError:
        error = "npm 不在 PATH（评估需在带 Node/npm 的环境运行）"
    except subprocess.TimeoutExpired:
        error = "npm pack 超时（网络慢）"
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)

    report_path = os.path.join(report_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(
            f"# DSH 候选版本评估报告\n\n"
            f"- 候选版本: `{candidate}`\n"
            f"- 当前锁定: `{LOCKED_TRAIN}`\n"
            f"- 隔离打包成功: `{packed}`\n"
            f"- 备注: `{error or '无'}`\n"
            f"- 升级建议: `{'是' if packed and candidate != LOCKED_TRAIN else '否'}`\n\n"
            "> 阶段 1 仅骨架：完整 checks/（tool_names/preset/session_migration/contract/supply_chain）\n"
            "> 属阶段 4 交付。本报告不修改任何已安装/锁定的运行时。\n"
        )
    return {"report": report_path, "packed": packed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version", nargs="?", default=None, help="候选版本；缺省则自动探测")
    args = ap.parse_args(argv)

    explicit = args.version if args.version and args.version != "auto" else None
    auto = (args.version == "auto") or explicit is None
    candidate = probe_candidate(auto, explicit)
    if not candidate:
        print("ℹ️ 未探测到候选版本（或手动触发未指定），仅输出当前锁定基线报告。")
        candidate = LOCKED_TRAIN

    res = evaluate_in_isolation(candidate)
    print(f"✅ 评估报告已产出: {res['report']}（candidate={candidate}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
