"""★ 唯一注入密钥的位置（``kernel/profile/host_payload.py``，``03`` §4 / ``00`` §3.1）。

``build_host_payload`` 是**唯一**把网关地址、网关 token、provider api_key 拼接进入
下发给内核宿主的地方。Profile 快照本身不含任何密钥（不变量 1，``00`` §3.2）。

返回载荷含：``gatewayUrl / accessToken / toolGatewayUrl / toolAccessToken``
以及供宿主装配用的 provider 凭证（api_key 只在内存拼装、绝不落库/落 Profile）。
"""

from __future__ import annotations

from typing import Any

from app.kernel.profile.models import RuntimeProfileSnapshot


def build_host_payload(
    profile: RuntimeProfileSnapshot,
    *,
    runtime_id: str,
    model_token: str,
    tool_token: str,
    gateway_base: str,
) -> dict[str, Any]:
    """构造下发宿主的运行时载荷（含密钥——仅此一次）。

    参数
    ----
    profile:
        编译好的 RuntimeProfileSnapshot（**不含密钥**）。
    runtime_id:
        宿主侧 runtime id。
    model_token:
        模型网关 HMAC 短时 token（``KERNEL_MODEL_GATEWAY_SECRET`` 派生，aud=model）。
    tool_token:
        工具网关 HMAC 短时 token（``KERNEL_TOOL_GATEWAY_SECRET`` 派生，aud=tool）。
    gateway_base:
        主服务对外基础地址（用于拼接网关 URL）。

    返回宿主装配所需的全量载荷。
    """
    # 网关 URL：默认路径拼接（见 ``01`` §6 的 KERNEL_*_GATEWAY_URL）
    base = gateway_base.rstrip("/")
    model_gateway_url = f"{base}/internal/kernel/model/generate"
    tool_gateway_url = f"{base}/internal/kernel/tools"

    provider_payload: dict[str, Any] = {}
    if profile.model is not None:
        # 注意：Profile 快照不含 api_key；此处若有 provider 密钥，也只在宿主进程内存中
        # 拼接下发。阶段 1 中 api_key 由模型网关在每次调用时从存储解密，宿主不持有。
        provider_payload = {
            "provider_type": profile.model.provider_type,
            "provider_name": profile.model.provider_name,
            "model_name": profile.model.model_name,
        }

    return {
        "schema_version": "zy.runtime-profile.v1",
        "runtime_id": runtime_id,
        "profile_version": profile.profile_version,
        "content_hash": profile.content_hash,
        "gatewayUrl": model_gateway_url,
        "accessToken": model_token,
        "toolGatewayUrl": tool_gateway_url,
        "toolAccessToken": tool_token,
        # ★ 密钥只出现在这里，绝不进入 Profile 内容寻址
        "model": provider_payload,
        "tools": [t.model_dump() for t in profile.tools],
        "skills": [s.model_dump() for s in profile.skills],
    }
