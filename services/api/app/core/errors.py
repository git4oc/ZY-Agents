"""领域异常与 HTTP 映射。

阶段 1 主服务的基础设施模块之一（``core/errors.py``，见 ``01-engineering-design.md`` §2 目录树）。

设计要点
--------
- 定义统一的 ``DomainError`` 基类与各领域异常子类。
- 每个领域异常携带：机器可读 ``code``（请求体 ``error.code``）、HTTP ``status``、
  面向用户的 ``message``、可选的 ``details``。
- 统一错误体（``02`` §4.1）：:

      { "error": { "code": "...", "message": "...", "details": {}, "trace_id": "..." } }

- ``ErrorMappingMiddleware``（见 ``api/middleware.py``）把此处异常映射为上述 HTTP 响应。
- 约定 ``code`` 一律使用小写 ``snake_case``，与领域异常类名一一对应。
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """所有领域异常的基类。

    参数
    ----
    code:
        机器可读错误码（写入响应体 ``error.code``），小写 snake_case。
    message:
        面向用户/日志的人类可读信息。
    status:
        HTTP 状态码（默认 400）。
    details:
        附加结构化信息（如 ``holder_user_id``），可空。
    """

    code: str = "domain_error"
    status: int = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.details: dict[str, Any] = details or {}
        self.trace_id: str = ""

    def as_body(self) -> dict[str, Any]:
        """构造统一错误体的 ``error`` 对象（不含外层包络）。"""
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "trace_id": self.trace_id,
        }


# ---------------------------------------------------------------------------
# 认证 / 授权
# ---------------------------------------------------------------------------


class Unauthenticated(DomainError):
    """未认证或 token 无效。绝不返回匿名 RequestContext（见 ``identity/auth.py``）。"""

    code = "unauthenticated"
    status = 401

    def __init__(self, message: str = "认证失败", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details=details)


class Forbidden(DomainError):
    """当前用户无权执行该操作。"""

    code = "forbidden"
    status = 403

    def __init__(
        self,
        message: str = "无权限执行此操作",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class InvalidToken(DomainError):
    """token 无效（HMAC 网关 token 或 JWT）。"""

    code = "invalid_token"
    status = 401

    def __init__(self, message: str = "无效的令牌", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details=details)


# ---------------------------------------------------------------------------
# 资源
# ---------------------------------------------------------------------------


class NotFound(DomainError):
    """资源不存在。协同场景下会话查询"非参与者视为 Forbidden 而非 404"由调用方决定。"""

    code = "not_found"
    status = 404

    def __init__(
        self,
        message: str = "资源不存在",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class Conflict(DomainError):
    """资源冲突（唯一约束等）。"""

    code = "conflict"
    status = 409

    def __init__(
        self,
        message: str = "资源冲突",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class ConflictError(DomainError):
    """唯一键冲突（``pymongo.errors.DuplicateKeyError`` → HTTP 409）。

    ## 为什么需要它，而不是让 DuplicateKeyError 冒到中间件

    唯一索引冲突是**可预期的**用户错误（重复提交同一个 code / name / key），
    却会一路冒泡成 ``internal_error`` **500**——对使用者毫无信息量，
    还把真正的配置问题（"这个 code 已被占用"）掩盖成"服务器坏了"。

    精确转换的价值：409 + 可读 message 让前端能提示"换个 code"，
    而不是让用户拿着 500 去问运维。

    ## 边界：只转换**可预期**的写入冲突

    仅捕获 ``DuplicateKeyError``（唯一索引）与 ``InvalidOperationError``
    这类**语义明确**的驱动异常。**未知异常仍应 500**——把未知异常吞成 4xx
    才是危险的（故障会被伪装成用户错误，直到某天排查不出来）。
    未知异常的处置权在 ``ErrorMappingMiddleware``。
    """

    code = "duplicate_key"
    status = 409

    def __init__(
        self,
        message: str = "已存在同名的记录",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


# ---------------------------------------------------------------------------
# 会话 / turn
# ---------------------------------------------------------------------------


class ConversationBusyError(DomainError):
    """会话已被他人驱动，turn 抢注失败（见 ``kernel/turn/claims.py``、``00`` §7.5）。"""

    code = "conversation_busy"
    status = 409

    def __init__(
        self,
        holder_user_id: str,
        *,
        message: str = "会话正在运行中",
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {"holder_user_id": holder_user_id}
        if details:
            merged.update(details)
        super().__init__(message, details=merged)


class ConversationNotFound(DomainError):
    """会话不存在。"""

    code = "conversation_not_found"
    status = 404

    def __init__(self, message: str = "会话不存在") -> None:
        super().__init__(message)


class ParticipantRequired(DomainError):
    """非参与者访问会话。返回 403 而非 404 以避免存在性探测（``01`` §4.2）。"""

    code = "participant_required"
    status = 403

    def __init__(self, message: str = "您不是该会话的参与者") -> None:
        super().__init__(message)


class ProfileVersionMismatch(DomainError):
    """Profile 版本不匹配，旧会话不可续（一致性闸门，见 ``00`` §3.2 不变量 2）。"""

    code = "profile_version_mismatch"
    status = 409

    def __init__(
        self,
        message: str = "配置已变更，该会话不可续",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class SkillDependencyError(DomainError):
    """Skill 编译依赖校验失败（``00`` §3.4，缺依赖直接编译失败）。"""

    code = "skill_dependency_error"
    status = 422

    def __init__(
        self,
        message: str = "Skill 依赖不满足",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status=422, details=details)


# ---------------------------------------------------------------------------
# 网关 / 认证 token
# ---------------------------------------------------------------------------


class GatewayTokenError(DomainError):
    """网关短时 token 校验失败（HMAC，见 ``kernel/inbound/credentials.py``）。"""

    code = "gateway_token_error"
    status = 401

    def __init__(self, message: str = "网关令牌无效", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details=details)


class ApprovalTimeout(DomainError):
    """审批等待超时，未获人工决策（勿静默放行，见 ``governance/approval.py``）。"""

    code = "approval_timeout"
    status = 504

    def __init__(self, message: str = "审批等待超时", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status=504, details=details)


class QuotaExceeded(DomainError):
    """配额超限（见 ``governance/quota.py``）。"""

    code = "quota_exceeded"
    status = 429

    def __init__(self, message: str = "配额已超限", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status=429, details=details)


class AmbiguousDeviceError(DomainError):
    """多设备且未绑定目标设备——报错要求选设备，不猜（见 ``00`` §5.4）。"""

    code = "ambiguous_device"
    status = 409

    def __init__(
        self,
        message: str = "检测到多台在线设备，请指定目标设备",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class LocalAgentOffline(DomainError):
    """本地 Agent 离线（结构化错误，含设备名与最后在线时间，见 ``00`` §5.7）。"""

    code = "local_agent_offline"
    status = 503

    def __init__(
        self,
        message: str = "本地 Agent 离线",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status=503, details=details)


# ---------------------------------------------------------------------------
# 内核
# ---------------------------------------------------------------------------


class KernelError(DomainError):
    """内核侧返回的错误（运行时错误、宿主不可用等）。"""

    code = "kernel_error"
    status = 502

    def __init__(
        self,
        message: str = "内核调用失败",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status=502, details=details)


class KernelUnavailable(DomainError):
    """内核宿主不可用。宿主不可用时**不阻塞启动**，只在调用时明确报错。"""

    code = "kernel_unavailable"
    status = 503

    def __init__(self, message: str = "内核宿主不可用", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status=503, details=details)


# ---------------------------------------------------------------------------
# 品牌
# ---------------------------------------------------------------------------


class BrandingValidationError(DomainError):
    """品牌输入校验失败（不可信输入，见 ``00`` §12.7）。"""

    code = "branding_validation_error"
    status = 422

    def __init__(self, message: str = "品牌配置校验失败", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status=422, details=details)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


class ToolExecutionError(DomainError):
    """服务端工具执行失败。"""

    code = "tool_execution_error"
    status = 502

    def __init__(
        self,
        message: str = "工具执行失败",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status=502, details=details)


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------


class StorageError(DomainError):
    """存储（本地卷 / Mongo / Redis）异常。"""

    code = "storage_error"
    status = 500

    def __init__(self, message: str = "存储异常", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status=500, details=details)
