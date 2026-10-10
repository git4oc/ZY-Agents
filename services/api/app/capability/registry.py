"""工具注册表（``capability/registry.py``，``01`` §4.4）。

- ``register`` / ``get`` / ``all_tools``：进程内工具注册表。
- ``bootstrap``：应用启动时注册内置 server 工具（``01`` §5.1 步骤 5c）。
- 阶段 1 提供至少一个 server 工具（``http_fetch``，见 ``tools/http_fetch.py``）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from app.capability.base import ServerTool

log = get_logger("capability.registry")

#: 进程内工具注册表（name -> ServerTool）
_tools: dict[str, "ServerTool"] = {}

#: 内置工具实现键 → 工具类（惰性导入，避免 import 循环）
_BUILTIN_LOADERS: dict[str, object] = {
    "http_fetch": None,  # 由 bootstrap 导入注册
}


def register(tool: "ServerTool") -> None:
    """注册一个服务端工具（同名覆盖）。"""
    _tools[tool.name] = tool
    log.info("server tool registered", extra={"zy": {"name": tool.name}})


def get(name: str) -> "ServerTool | None":
    """按名取工具；未注册返回 None。"""
    return _tools.get(name)


def all_tools() -> tuple["ServerTool", ...]:
    """返回全部已注册工具（按注册名排序，保证确定性）。"""
    return tuple(_tools[name] for name in sorted(_tools))


def bootstrap() -> None:
    """注册全部内置 server 工具（幂等）。"""
    from app.capability.tools.http_fetch import HttpFetchTool
    from app.capability.tools.knowledge_search import KnowledgeSearchTool

    register(HttpFetchTool())
    register(KnowledgeSearchTool())
    log.info("capability bootstrap done", extra={"zy": {"count": len(_tools)}})
