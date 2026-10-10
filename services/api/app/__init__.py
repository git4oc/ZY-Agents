"""app —— ZY-Agents 主服务根包。

分层规则（``01`` §3）：
- ``app.* → kernel/dsh/`` 禁止（唯一例外 kernel/dsh/gateway.py 自身）。
- ``app.* → kernel/contract/`` 允许。
- ``kernel/ → collaboration/`` 禁止（回调走 inbound/ 独立端点）。
- ``capability/ → collaboration/`` 禁止。
- ``localagent/ → kernel/`` 禁止（阶段 3）。
"""
