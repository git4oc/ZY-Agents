"""登录态校验 → RequestContext（``01`` §4.1）。

阶段 1 主服务身份模块（``identity/auth.py``）。

设计要点
--------
- ``resolve_context`` 从 Bearer JWT / Session 解析身份；任何失败抛 ``Unauthenticated``，
  **绝不返回匿名上下文**（``00`` §7.1：不提供"不带上下文"的查询）。
- ``generate_access_token``：签发 access token（``JWT_ISSUER=zy-agents``，见 ``01`` §6）。
- 依赖 jaraco / PyJWT；签名密钥来自 ``settings``。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import jwt  # PyJWT

from app.core.errors import Unauthenticated
from app.core.logging import get_logger
from app.identity.context import RequestContext
from app.settings import Settings

log = get_logger("identity.auth")

# PyJWT 需要的标准算法
_ALGORITHM = "HS256"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_access_token(
    *,
    settings: Settings,
    user_id: str,
    org_unit_id: str,
    roles: frozenset[str],
    permissions: frozenset[str] | None = None,
) -> str:
    """为已认证用户签发 access token。

    参数
    ----
    settings:
        应用配置（含 ``jwt_signing_secret`` / ``jwt_issuer`` / ``ACCESS_TOKEN_TTL_SECONDS``）。
    user_id / org_unit_id / roles:
        身份三要素（全部由服务端传入，绝不来自请求体）。
    permissions:
        ★ 由 ``roles`` 经 ``roles.permissions`` 展开得到的**权限码**集合
        （``02`` §2.1）。与 ``roles`` **分属两个 claim**——角色码是身份语义、
        权限码是授权语义，混在一起会让 ``/auth/me`` 的 ``roles`` 与
        ``agentPolicy.roleIds`` 语义被污染（前端直接消费它们）。

    返回 JWT 字符串。
    """
    now = _now()
    payload = {
        "sub": user_id,
        "org": org_unit_id,
        "roles": sorted(roles),
        "perms": sorted(permissions or frozenset()),
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_signing_secret, algorithm=_ALGORITHM)


async def resolve_context(
    authorization: str | None,
    *,
    settings: Settings,
) -> RequestContext:
    """从 ``Authorization`` header 解析出 RequestContext。

    参数
    ----
    authorization:
        ``Bearer <token>`` 原始 header（可为 None）。
    settings:
        应用配置。

    返回解析出的 RequestContext。

    异常
    ----
    Unauthenticated:
        header 缺失 / 格式错误 / token 无效 / 过期 / 签名不符——任何失败都抛此异常。
    """
    if not authorization:
        raise Unauthenticated("缺少 Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthenticated("Authorization 头格式错误，应为 'Bearer <token>'")

    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_secret,
            algorithms=[_ALGORITHM],
            issuer=settings.jwt_issuer,
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthenticated("登录态已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthenticated("登录态无效") from exc

    user_id = payload.get("sub")
    org_unit_id = payload.get("org")
    if not user_id or not org_unit_id:
        raise Unauthenticated("登录态缺少必要身份字段")

    roles = frozenset(str(r) for r in payload.get("roles", []) or [])
    # ★ **决策记录（勿当漏做而"顺手补上"）**：``perms`` claim 是 P1-2 权限命名空间
    #   修复时新增的。**旧 token（修复前签发的）没有这个 claim** → 解析为空集 →
    #   ``acl.has_permission`` 判定不放行（**fail-closed**）。
    #
    #   影响面：非 admin 用户需**重新登录**一次；admin 不受影响（走角色码直通）。
    #
    #   **当前刻意不引入 token_version 强制失效机制**，理由：
    #     1. 私有化交付通常无人持有修复前签发的长期会话 token；
    #     2. 真出现登录混乱时，加长 ``ACCESS_TOKEN_TTL_SECONDS`` 即可规避；
    #     3. 引入 token 版本号需要额外的存储与校验点，属**过度设计**。
    #   **若将来出现多实例 / 长期会话场景**（或跨版本升级后大面积 403），
    #   届时再引入 ``token_version`` 强制失效。
    permissions = frozenset(str(p) for p in payload.get("perms", []) or [])
    return RequestContext(
        user_id=str(user_id),
        org_unit_id=str(org_unit_id),
        roles=roles,
        permissions=permissions,
    )


def hash_secret(value: str) -> str:
    """对敏感值（如 device_credential）做 sha256 摘要，用于落库（不存明文）。

    说明：生产环境建议 argon2id；此处 sha256 用于非高安全敏感度场景的摘要存储。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
