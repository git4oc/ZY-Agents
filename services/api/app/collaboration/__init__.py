"""collaboration —— 会话、参与者、消息、事件投影、分享（``00`` §7）。

分层规则（``01`` §3）：``collaboration/ → kernel/`` 只允许 import ``contract/``，
禁止 import ``dsh/``；``kernel/ → collaboration/`` 禁止（回调走 inbound/ 独立端点）。

事件脱敏**只有**在这里发生（``projection.py``，``00`` §7.3）。
"""
