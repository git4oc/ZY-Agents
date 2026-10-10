"""kernel/dsh —— DSH 适配器（上游 API 只在这里出现）。

见 ``01`` §3 分层规则：唯一例外 ``kernel/dsh/gateway.py`` 被其它模块 import。
其他任何模块禁止 import 此包。升级 DSH 时爆炸半径只可能在 ``kernel/dsh/`` 里。
"""

from app.kernel.dsh.gateway import DshKernelGateway

__all__ = ["DshKernelGateway"]
