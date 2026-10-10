"""ZY-Agents API 测试包。

运行方式（项目根 ``services/api`` 下）::

    python -m unittest discover -s tests -p "test_*.py"

**无网络依赖**：本套件覆盖的纯函数逻辑（契约严格性 / HMAC 域分离 / 品牌白名单 /
Profile 内容寻址确定性）不依赖 Mongo/Motor/Redis/DSH，可在无凭据环境直接运行
（见 ``tests/_zytest_stubs.py`` 的 motor/pymongo 桩说明）。
"""
