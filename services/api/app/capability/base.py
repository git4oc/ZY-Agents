"""工具接口与上下文（``capability/base.py``，``01`` §4.4）。

- ``ToolContext``：工具执行上下文（含 RequestContext、会话/调用定位、trace）。
- ``ServerTool``：服务端工具协议（name / description / input_schema / ... / execute）。
- ``ToolResult``：统一工具返回。

分层规则（``01`` §3）：``capability/ → collaboration/`` **禁止**——工具不感知会话，
只收 ``ToolContext``，因此这里**不 import** collaboration。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.identity.context import RequestContext

#: 工具类别（``00`` §4.4）
ToolCategory = Literal["server", "local", "hybrid"]
#: 风险等级
RiskLevel = Literal["read", "write", "destructive"]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """一次工具执行的上下文（工具不感知会话结构，只收此上下文）。"""

    ctx: RequestContext
    conversation_id: str | None
    session_id: str | None
    call_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    """统一工具返回。"""

    ok: bool
    value: Any = None
    error: str | None = None


class ServerTool(Protocol):
    """服务端工具协议。实现方须提供这些属性和方法。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    category: ToolCategory
    risk_level: RiskLevel
    approval_required: bool
    timeout_ms: int

    async def execute(self, args: dict[str, Any], tc: ToolContext) -> ToolResult: ...
