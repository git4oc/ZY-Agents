"""内核错误模型（``03`` §2.3 ``KernelError`` 契约型）。

注意：这里 ``KernelError`` 是**契约层**的不变模型（供命令/应答校验用）；
领域层与之对应的运行时异常在 ``app.core.errors.KernelError``。两者职责不同：
本处用于"内核返回的、可序列化的错误载荷"，core 异常用于 Python 异常传播与 HTTP 映射。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import field_validator

from app.kernel.contract.commands import ContractModel
from app.kernel.contract.versions import AGENT_KERNEL_CONTRACT_VERSION

#: 错误码校验：^[a-z][a-z0-9_.-]*$
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")


class KernelError(ContractModel):
    """内核返回的错误载荷。"""

    schema_version: Literal["zy.agent-kernel.v1"] = AGENT_KERNEL_CONTRACT_VERSION
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = {}

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        if not _CODE_PATTERN.match(v):
            raise ValueError(f"错误码格式非法: {v!r}（须匹配 ^[a-z][a-z0-9_.-]*$）")
        return v
