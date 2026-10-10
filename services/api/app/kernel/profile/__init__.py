"""kernel/profile —— Runtime Profile 编译（``00`` §3，``03`` §3）。

- ``models.py``: RuntimeProfileSnapshot——不可变 Profile 字段模型。
- ``compiler.py``: 确定性编译，内容寻址。
- ``catalog.py`` / ``tools.py`` / ``skills.py``: admin 行 → 编译输入。
- ``store.py``: Profile 缓存（按 content_hash）。
- ``host_payload.py``: ★ 唯一注入密钥处。
"""
