"""产物下载端点（``api/endpoints/artifacts.py``，``02`` §4.5）。

``GET /api/v1/conversations/{conversation_id}/artifacts/{artifact_id}``

**签名 URL 三步校验**（``00`` §7.4，全部委托给 ``collaboration/share.py::verify_download``，
本端点不重复实现任何校验逻辑）：

1. 签名有效且未过期；
2. 当前 user_id 对该 artifact 所属会话**仍有 ACL**（参与者未被 removed_at）；
3. 签名未被撤销（nonce 未被拉黑）。

**不要只靠 TTL**——TTL 只是兜底，撤销后立刻失效才是正确语义。

签名参数通过 **query string** 传入（``user_id`` / ``nonce`` / ``expires_at`` / ``signature``），
与 ``share.verify_download(params, *, secret, ctx)`` 的入参形状一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.errors import Forbidden, NotFound
from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/api/v1/conversations", tags=["artifacts"])


@router.get("/{conversation_id}/artifacts/{artifact_id}")
async def download_artifact(
    conversation_id: str,
    artifact_id: str,
    user_id: str = Query(..., description="签名绑定的用户 id"),
    nonce: str = Query(..., description="签名随机串（撤销即失效）"),
    expires_at: int = Query(..., description="签名过期时间戳（秒）"),
    signature: str = Query(..., description="HMAC-SHA256 签名"),
    ctx: RequestContext = Depends(get_ctx),
) -> Any:
    """下载会话产物（签名 URL 三步校验）。

    校验通过后从本地卷流式返回文件；失败分别映射为 403（签名/ACL/撤销）与 404（不存在）。
    """
    from app.collaboration.share import verify_download
    from app.settings import get_settings

    settings = get_settings()

    # 1~3) 三步校验全在 share.verify_download 内（签名 → 参与关系 ACL → nonce 未撤销）
    storage_path = await verify_download(
        {
            "artifact_id": artifact_id,
            "user_id": user_id,
            "nonce": nonce,
            "expires_at": expires_at,
            "signature": signature,
        },
        secret=settings.artifact_url_secret,
        ctx=ctx,
    )

    # 额外一道：产物必须确实属于路径里声明的会话（防跨会话引用）
    await _assert_belongs_to_conversation(ctx, artifact_id, conversation_id)

    if not storage_path:
        raise NotFound("产物文件缺失")

    # 目录穿越防护：解析后必须仍在 storage_root 内
    root = settings.storage_root
    path = (root / str(storage_path)).resolve()
    try:
        path.relative_to(root.resolve())
    except (ValueError, OSError):
        raise Forbidden("非法的存储路径")

    if not path.is_file():
        raise NotFound("产物文件缺失")

    return StreamingResponse(
        _file_iterator(path),
        media_type=_guess_media(path),
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


async def _assert_belongs_to_conversation(
    ctx: RequestContext, artifact_id: str, conversation_id: str
) -> None:
    """确认产物归属该会话（签名合法也不许跨会话拿文件）。"""
    from app.core import mongo

    doc = await mongo.get_collection("artifacts").find_one(
        {"_id": _oid(artifact_id), "org_unit_id": ctx.org_unit_id},
        {"conversation_id": 1},
    )
    if doc is None:
        raise NotFound("产物不存在")
    if str(doc.get("conversation_id") or "") != str(conversation_id):
        raise Forbidden("产物不属于该会话")


def _file_iterator(path: Any):
    """分块读取文件（避免大产物一次性进内存）。"""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(64 * 1024)
            if not chunk:
                break
            yield chunk


def _guess_media(path: Any) -> str:
    """按扩展名猜 MIME（``mimetypes`` 判定失败时回落通用二进制）。"""
    import mimetypes

    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _oid(value: str) -> Any:
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return value
