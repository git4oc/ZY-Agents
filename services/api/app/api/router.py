"""路由汇总注册（``api/router.py``，``01`` §2 目录树）。

把公开路由 / 管理路由 / 内部路由聚合到主应用。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.endpoints import (
    admin,
    approvals,
    artifacts,
    auth,
    branding,
    conversations,
    devices,
    knowledge,
    knowledge_admin,
    knowledge_user,
    models,
    resource_feedback,
    skill_sharing,
    turns,
)
from app.api.endpoints.internal import kernel as internal_kernel

#: 公开 + 管理 API 汇总路由
api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(conversations.session_share_router)
api_router.include_router(turns.router)
api_router.include_router(devices.router)
api_router.include_router(models.router)
api_router.include_router(knowledge_admin.router)
api_router.include_router(admin.router)
api_router.include_router(branding.public_router)
api_router.include_router(branding.admin_router)
api_router.include_router(approvals.router)
api_router.include_router(artifacts.router)

#: 知识库路由（同一份端点注册两次，两个前缀）。
#:
#: **路径前缀说明**：``02`` §4.7 规定知识库端点为 ``/api/v1/knowledge/*``；而前端
#: ``apps/web/src/api/knowledgeSources.ts`` 实际请求的是 ``/api/knowledge/sources/documents/...``
#: （经 vite proxy 的 ``/api`` → ``/api/v1`` 归一化后就是这个形态）。
#: 为同时满足规范路径与前端已在调的路径，这里把**同一份 router**（自身前缀只有
#: ``/knowledge``）分别挂在 ``/api/v1`` 与 ``/api`` 两个前缀下——端点实现只有一份，
#: 不存在两套逻辑漂移的风险。
api_router.include_router(knowledge.router, prefix="/api/v1")
api_router.include_router(knowledge.router, prefix="/api")
api_router.include_router(knowledge_user.router, prefix="/api/v1")
api_router.include_router(knowledge_user.router, prefix="/api")

#: 资源反馈路由（评价/点赞/通知；同一份端点注册两个前缀，同 knowledge 模式）
api_router.include_router(resource_feedback.router, prefix="/api/v1")
api_router.include_router(resource_feedback.router, prefix="/api")
api_router.include_router(resource_feedback.notifications_router, prefix="/api/v1")
api_router.include_router(resource_feedback.notifications_router, prefix="/api")

#: 技能分享路由（分享链接/直分享/收件箱/成员目录；双前缀同上）
api_router.include_router(skill_sharing.router, prefix="/api/v1")
api_router.include_router(skill_sharing.router, prefix="/api")

#: 内部内核回调路由（HMAC 鉴权，独立前缀；nginx deny /internal/）
internal_router = APIRouter()
internal_router.include_router(internal_kernel.router)
