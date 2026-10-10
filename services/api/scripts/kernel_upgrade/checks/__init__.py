"""DSH 升级检查器集合（阶段 4）。

规划中的检查器（docs/00 §10.1 / docs/03 §9）：
- tool_names.py         上游新增原生工具名是否与自有工具冲突
- preset.py             preset 组合是否仍可装配
- session_migration.py  旧版本建会话 → 候选版本恢复 → 逐字段比对
- contract.py           冻结契约是否被动过
- supply_chain.py       integrity / shasum 校验

阶段 1 保持空实现（仅结构），由 evaluate.py 于阶段 4 装配。
"""
