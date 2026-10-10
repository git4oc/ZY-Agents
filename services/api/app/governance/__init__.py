"""governance —— 审批、审计、配额（``00`` §8）。

- ``approval.py``: 工具级人在环。工具定义里 ``approval_required``，执行前阻塞等待。
- ``audit.py``: 审计落库与查询。
- ``quota.py``: 按组织单元/用户限制 token 与调用次数。
"""
