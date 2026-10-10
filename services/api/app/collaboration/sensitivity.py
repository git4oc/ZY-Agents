"""字段敏感度声明表（``collaboration/sensitivity.py``，``02`` §3）。

脱敏**只在** ``projection.py`` 发生，规则来自这张**声明表**，不在代码里散落 ``if``。

规则（``02`` §3）：
1. 表里未声明的字段一律按 ``secret`` 处理（**默认拒绝**，不是默认放行）。
2. viewer 的 SSE 推的是脱敏后的流。前端过滤不算过滤。
3. ``assistant.message`` 是 public，因为协同的核心价值就是让参与者读到答复。
"""

from __future__ import annotations

from typing import Any

#: 敏感度等级
Sensitivity = str  # "public" | "internal" | "secret"

PUBLIC = "public"
INTERNAL = "internal"
SECRET = "secret"


#: 每个事件类型 → 字段敏感度表
#: 语义：字段 → 敏感度。未列出的字段默认 SECRET。
SENSITIVITY_TABLE: dict[str, dict[str, str]] = {
    "tool.call.requested": {
        "tool_name": INTERNAL,  # ✅ 保留名称
        "arguments": SECRET,  # ❌
        "call_id": INTERNAL,  # ✅ 关联同一调用
        "category": INTERNAL,  # ✅ server/local/hybrid 是接线点，非机密
    },
    "tool.call.completed": {
        "result": SECRET,  # ❌
        # status 取值：ok | unverified | failed（由效果核验派生，见 tool_endpoint._status_for）
        "status": PUBLIC,  # ✅
        "latency_ms": PUBLIC,  # ✅
        "call_id": INTERNAL,  # ✅
        "tool_name": INTERNAL,  # ✅
        "result_summary": INTERNAL,  # ✅ 摘要（不含原始结果体）
        "verified": PUBLIC,  # ✅ 效果核验三态：ok | unverified | failed
    },
    # 本轮新增：工具失败 / 审批流转（与既有 tool.call.* 同族，复用同一脱敏口径）
    "tool.call.failed": {
        "code": PUBLIC,  # ✅ 错误码：前端要区分失败原因
        "status": PUBLIC,  # ✅
        "message": INTERNAL,  # ✅ 错误描述（不含内网 URL / 路径）
        "latency_ms": PUBLIC,  # ✅
        "call_id": INTERNAL,  # ✅
        "tool_name": INTERNAL,  # ✅
        "verified": PUBLIC,  # ✅ 效果核验判定（三态：ok / failed / unverified）
    },
    "tool.approval.requested": {
        "tool_name": INTERNAL,  # ✅ 参与者需知道在批什么工具
        "call_id": INTERNAL,  # ✅
        "approval_id": INTERNAL,  # ✅ 前端拿它去 POST 决策
        "status": PUBLIC,  # ✅ pending
        "arguments": SECRET,  # ❌ 参数只在 owner/editor 的审批列表可见
    },
    "tool.approval.decided": {
        "tool_name": INTERNAL,  # ✅
        "call_id": INTERNAL,  # ✅
        "approval_id": INTERNAL,  # ✅
        "approved": PUBLIC,  # ✅ 通过与否
        "reason": INTERNAL,  # ✅ 审批理由
        "status": PUBLIC,  # ✅ approved / rejected / timeout
    },
    "artifact.created": {
        "filename": INTERNAL,  # ✅
        "storage_path": SECRET,  # ❌
        "download_url": SECRET,  # ❌ 连 URL 都不给
    },
    "model.request": {
        "system": SECRET,  # ❌
        "messages": SECRET,  # ❌
        "model_name": INTERNAL,  # ✅
    },
    "assistant.message": {
        "content": PUBLIC,  # ✅
    },
    "localagent.auth_required": {
        "url": INTERNAL,  # ✅（参与者需知道去哪登录）
        "device_display_name": PUBLIC,  # ✅
    },
    "error": {
        "code": PUBLIC,  # ✅
        "details": INTERNAL,  # ⚠️ 按子字段判定
    },
}

#: 默认敏感度（表里未声明的字段按此处理）：secret（默认拒绝）
DEFAULT_SENSITIVITY = SECRET


def field_sensitivity(event_type: str, field: str) -> str:
    """返回某事件某字段的敏感度（未声明一律返回 SECRET）。"""
    return SENSITIVITY_TABLE.get(event_type, {}).get(field, DEFAULT_SENSITIVITY)


def project_payload(event_type: str, payload: dict[str, Any], *, viewer_role: str) -> dict[str, Any]:
    """按 viewer 角色对事件负载做脱敏投影。

    - owner / editor → 原样返回（角色判定在 projection.py 外层，这里仅做通用过滤）。
    - viewer → 只保留 public / internal 字段（secret 字段剔除）。

    参数
    ----
    event_type:
        归一化事件类型。
    payload:
        原始事件负载（全量，含工具参数与结果）。
    viewer_role:
        查看者角色（owner / editor / viewer）。

    返回脱敏后的负载。
    """
    if viewer_role in ("owner", "editor"):
        return payload

    table = SENSITIVITY_TABLE.get(event_type, {})
    result: dict[str, Any] = {}
    for field, value in payload.items():
        sens = table.get(field, DEFAULT_SENSITIVITY)
        if sens in (PUBLIC, INTERNAL):
            result[field] = value
        # secret 字段直接剔除（连 download_url 都不给）
    return result
