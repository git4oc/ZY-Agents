"""知识库用户端点（``api/endpoints/knowledge_user.py``，阶段 2 任务 #2）。

前端 ``apps/web/src/api/personalKnowledge.ts``（我的知识库页面）所需的用户侧端点。
**登录用户即可用**（无 admin 守卫）；org 隔离 + owner 可见（本人上传 + 管理端上传的
org 级文档）。摄取管道复用 ``knowledge_admin.py`` 的解析与 ``knowledge/indexer.py``。

- ``GET    /knowledge/directories`` / ``POST`` / ``PUT/{id}`` / ``DELETE/{id}``：目录 CRUD
- ``GET    /knowledge/documents``：我的文档列表（view=mine/shared；shared 为 grant 驱动的分享视图）
- ``POST   /knowledge/documents``：multipart 上传（owner_scoped）
- ``GET/PATCH/DELETE /knowledge/documents/{id}``：详情 / 元数据 / 删除
- ``POST   /knowledge/documents/{id}/relearn`` / ``/replace``：重建索引 / 替换原件
- ``GET    /knowledge/counts``：徽标计数（我分享的活跃授权数 + 收到的分享总数/未读数）

纪律（与 ``knowledge_admin.py`` 同口径）：``storage_path`` 相对 ``storage_root``；
失败 status=failed + error 不静默；嵌入向量不下发（``00`` §3.2 不变量）。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from app.core import mongo
from app.core.clock import utcnow
from app.core.errors import Forbidden, NotFound
from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/knowledge", tags=["knowledge-user"])

_DOCS = "knowledge_documents"
_CHUNKS = "knowledge_chunks"
_DIRS = "knowledge_directories"
_SHARES = "knowledge_shares"

#: 后台索引任务引用（防 asyncio.create_task 被垃圾回收）
_BACKGROUND_TASKS: set = set()


# ---------------------------------------------------------------------------
# 目录 CRUD
# ---------------------------------------------------------------------------


@router.get("/directories")
async def list_directories(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """目录树（org 隔离）。"""
    dirs = mongo.get_collection(_DIRS)
    rows = await dirs.find({"org_unit_id": ctx.org_unit_id}).to_list(length=None)
    items = _build_tree(rows)
    root_count = sum(1 for r in rows if not r.get("parent_id"))
    return {"items": items, "rootCount": root_count}


@router.post("/directories")
async def create_directory(
    body: DirectoryBody, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    dirs = mongo.get_collection(_DIRS)
    doc_id = uuid.uuid4().hex
    await dirs.insert_one(
        {
            "_id": doc_id,
            "org_unit_id": ctx.org_unit_id,
            "name": body.name.strip(),
            "parent_id": body.parentId or "",
            "created_by": ctx.user_id,
            "created_at": utcnow(),
        }
    )
    return {"id": doc_id}


@router.put("/directories/{directory_id}")
async def update_directory(
    directory_id: str, body: DirectoryBody, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    dirs = mongo.get_collection(_DIRS)
    result = await dirs.update_one(
        {"_id": directory_id, "org_unit_id": ctx.org_unit_id},
        {"$set": {"name": body.name.strip(), "parent_id": body.parentId or ""}},
    )
    if result.matched_count == 0:
        raise NotFound("目录不存在")
    return {"ok": True}


@router.delete("/directories/{directory_id}")
async def delete_directory(
    directory_id: str, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    dirs = mongo.get_collection(_DIRS)
    query = {"org_unit_id": ctx.org_unit_id, "_id": directory_id}
    if await dirs.find_one(query) is None:
        raise NotFound("目录不存在")
    if await dirs.find_one({"org_unit_id": ctx.org_unit_id, "parent_id": directory_id}):
        raise Forbidden("目录包含子目录，无法删除")
    if await mongo.get_collection(_DOCS).find_one(
        {"org_unit_id": ctx.org_unit_id, "directory_id": directory_id}
    ):
        raise Forbidden("目录内有文档，无法删除")
    await dirs.delete_one(query)
    return {"ok": True}


class DirectoryBody(BaseModel):
    name: str
    parentId: str = ""


def _build_tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """目录数组 → 树（children 嵌套）。"""
    nodes: dict[str, dict[str, Any]] = {}
    for r in rows:
        nodes[str(r["_id"])] = {
            "id": str(r["_id"]),
            "name": str(r.get("name") or ""),
            "parentId": str(r.get("parent_id") or ""),
            "count": 0,
            "children": [],
        }
    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent = nodes.get(node["parentId"])
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots


# ---------------------------------------------------------------------------
# 文档列表 / 详情
# ---------------------------------------------------------------------------


@router.get("/documents")
async def list_documents(
    view: str = "mine",
    directoryId: str = "all",
    keyword: str = "",
    page: int = 1,
    pageSize: int = 12,
    ctx: RequestContext = Depends(get_ctx),
) -> dict[str, Any]:
    """我的文档列表（view=mine 本人视角 / view=shared 他人分享给我的）。"""
    if view == "shared":
        return await _shared_documents(directoryId, keyword, page, pageSize, ctx)
    if view != "mine":
        return {"items": [], "total": 0, "page": page, "pageSize": pageSize}

    query: dict[str, Any] = {
        "org_unit_id": ctx.org_unit_id,
        "$or": [{"owner_user_id": ctx.user_id}, {"owner_user_id": {"$exists": False}}],
    }
    if directoryId == "":
        query["directory_id"] = {"$in": [None, ""]}
    elif directoryId != "all":
        query["directory_id"] = directoryId
    if keyword.strip():
        query["title"] = {"$regex": re.escape(keyword.strip()), "$options": "i"}

    docs = mongo.get_collection(_DOCS)
    total = await docs.count_documents(query)
    page = max(1, page)
    page_size = min(max(1, pageSize), 100)
    cursor = (
        docs.find(query, {"raw_text": 0})
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    rows = [r async for r in cursor]
    # 聚合分享状态（share.shared / recipientCount 徽标）
    doc_ids = [str(r["_id"]) for r in rows]
    share_counts: dict[str, int] = {}
    if doc_ids:
        async for g in mongo.get_collection(_SHARES).find(
            {"org_unit_id": ctx.org_unit_id, "document_id": {"$in": doc_ids}, "status": "active"},
            {"document_id": 1},
        ):
            share_counts[g["document_id"]] = share_counts.get(g["document_id"], 0) + 1
    items = [_doc_to_personal(r, ctx, share_count=share_counts.get(str(r["_id"]), 0)) for r in rows]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


async def _shared_documents(
    directoryId: str, keyword: str, page: int, pageSize: int, ctx: RequestContext
) -> dict[str, Any]:
    """他人分享给我的文档（knowledge_shares grant 驱动）。"""
    grant_rows = await mongo.get_collection(_SHARES).find(
        {"org_unit_id": ctx.org_unit_id, "recipient_user_id": ctx.user_id, "status": "active"}
    ).to_list(length=None)
    grants_by_doc: dict[str, dict[str, Any]] = {str(g.get("document_id")): g for g in grant_rows}
    query: dict[str, Any] = {
        "org_unit_id": ctx.org_unit_id,
        "_id": {"$in": list(grants_by_doc.keys())},
    }
    if keyword.strip():
        query["title"] = {"$regex": re.escape(keyword.strip()), "$options": "i"}

    docs = mongo.get_collection(_DOCS)
    total = await docs.count_documents(query)
    page = max(1, page)
    page_size = min(max(1, pageSize), 100)
    cursor = (
        docs.find(query, {"raw_text": 0})
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    users = mongo.get_collection("users")
    items: list[dict[str, Any]] = []
    async for r in cursor:
        grant = grants_by_doc.get(str(r["_id"]), {})
        granter = await users.find_one(
            {"_id": _uid(grant.get("granted_by"))}, {"display_name": 1, "username": 1}
        )
        items.append(
            {
                **_doc_to_personal(r, ctx),
                "accessStatus": "active",
                "isOwner": False,
                "canReshare": bool(grant.get("can_reshare")),
                "canShare": bool(grant.get("can_reshare")),
                "grantedByUserId": str(grant.get("granted_by") or ""),
                "grantedBy": {
                    "displayName": str((granter or {}).get("display_name") or ""),
                    "username": str((granter or {}).get("username") or ""),
                },
                "sharedAt": _iso(grant.get("updated_at")),
                "seen": bool(grant.get("seen", True)),
            }
        )
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    doc = await _load_visible(ctx, document_id)
    return _doc_to_personal(doc, ctx)


class AskBody(BaseModel):
    query: str


@router.post("/documents/{document_id}/ask")
async def ask_document(
    document_id: str, body: AskBody, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """按用户问题检索全组织知识库并生成回答。

    检索走 ``S-32`` 冻结签名（``retriever.search``，org 隔离 + ACL + 同文档去重
    内建），范围为**全组织知识库**（问题驱动，不锁定单一文档；URL 中的
    document_id 仅作入口并校验可见性）。生成走提供商非流式
    ``chat/completions``（无关键词命中时基于已索引 chunk 仍可回答）。
    回答附 ``citations``（chunk 级出处 + 文档标题）。
    """
    if not body.query.strip():
        raise Forbidden("问题不能为空")
    doc = await _load_visible(ctx, document_id)

    from starlette.concurrency import run_in_threadpool

    from app.knowledge.models import SearchFilters
    from app.knowledge.retriever import search as knowledge_search

    # search 是同步阻塞签名（S-32 冻结），用 run_in_threadpool 放线程池，不堵事件循环。
    # 检索范围为全组织知识库（问题驱动，不锁定单一文档）
    hits = await run_in_threadpool(
        knowledge_search,
        body.query.strip(),
        filters=SearchFilters(
            org_unit_id=ctx.org_unit_id,
            user_context=ctx,
        ),
        top_k=5,
    )

    import httpx

    from app.settings import get_settings

    settings = get_settings()
    context_blocks = "\n\n".join(
        f"[{i + 1}] {hit.text}" for i, hit in enumerate(hits)
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是企业知识库问答助手。仅依据下方资料回答用户问题；"
                "回答中用 [n] 标注所引用资料的编号；资料不足以回答时如实说明。"
                + (f"\n\n资料：\n{context_blocks}" if context_blocks else "")
            ),
        },
        {"role": "user", "content": body.query.strip()},
    ]
    answer = ""
    try:
        timeout = httpx.Timeout(settings.model_provider_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{settings.model_provider_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.model_provider_api_key}"},
                json={
                    "model": settings.model_provider_model,
                    "messages": messages,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            answer = str(
                ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            )
    except Exception as exc:  # noqa: BLE001 - 生成失败不吞：明确报错给调用方
        raise Forbidden(f"问答生成失败: {exc}") from exc

    # 引用跨文档：补文档标题（一次批量查询）
    cite_ids = {hit.document_id for hit in hits if hit.document_id}
    titles: dict[str, str] = {}
    if cite_ids:
        cite_query_ids: list[Any] = []
        for d in cite_ids:
            try:
                cite_query_ids.append(ObjectId(d))
            except Exception:
                cite_query_ids.append(d)
        rows = await mongo.get_collection(_DOCS).find(
            {"_id": {"$in": cite_query_ids}}, {"title": 1}
        ).to_list(length=len(cite_ids))
        titles = {str(r.get("_id")): str(r.get("title") or "") for r in rows}

    return {
        "answer": answer,
        "citations": [
            {
                "index": i + 1,
                "chunkId": hit.chunk_id,
                "documentId": hit.document_id,
                "documentTitle": titles.get(hit.document_id, ""),
                "headingPath": hit.heading_path,
                "score": hit.score,
                "text": hit.text,
            }
            for i, hit in enumerate(hits)
        ],
        "usedChunks": [hit.chunk_id for hit in hits],
    }


def _doc_to_personal(r: dict[str, Any], ctx: RequestContext, share_count: int = 0) -> dict[str, Any]:
    """Mongo 文档 → 前端 ``PersonalKnowledge`` 形状（字段名对齐 personalKnowledge.ts）。"""
    created = r.get("created_at")
    updated = r.get("updated_at") or r.get("indexed_at") or created
    shared = share_count > 0
    return {
        "id": str(r.get("_id")),
        "ownerUserId": str(r.get("owner_user_id") or ""),
        "directoryId": str(r.get("directory_id") or ""),
        "name": str(r.get("title") or r.get("name") or ""),
        "description": str(r.get("description") or ""),
        "tags": list(r.get("tags") or []),
        "status": str(r.get("status") or ""),
        "error": str(r.get("error") or ""),
        "activeDocumentId": str(r.get("_id")),
        "canReshare": False,
        "accessStatus": "owner",
        "grantedByUserId": "",
        "seen": True,
        "sharedAt": "",
        "createdAt": _iso(created),
        "updatedAt": _iso(updated),
        "isOwner": True,
        "canShare": True,
        "share": {"shared": shared, "recipientCount": share_count},
    }


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _owner_query(ctx: RequestContext, document_id: str) -> dict[str, Any]:
    """owner 可见查询：本人上传 或 管理端上传的 org 级文档（无 owner）。"""
    return {
        "org_unit_id": ctx.org_unit_id,
        "_id": document_id,
        "$or": [{"owner_user_id": ctx.user_id}, {"owner_user_id": {"$exists": False}}],
    }


def _uid(value: Any) -> Any:
    """users 集合 _id 有 ObjectId 与 str 两种形态（同 skill_sharing.py）。"""
    try:
        return ObjectId(value)
    except Exception:
        return value or ""


async def _load_visible(ctx: RequestContext, document_id: str) -> dict[str, Any]:
    """可见性：owner 或持有 active 授权（分享接收者）。"""
    doc = await mongo.get_collection(_DOCS).find_one(_owner_query(ctx, document_id))
    if doc is not None:
        return doc
    grant = await mongo.get_collection(_SHARES).find_one(
        {
            "org_unit_id": ctx.org_unit_id,
            "document_id": document_id,
            "recipient_user_id": ctx.user_id,
            "status": "active",
        }
    )
    if grant is None:
        raise NotFound("文档不存在")
    doc = await mongo.get_collection(_DOCS).find_one(
        {"_id": document_id, "org_unit_id": ctx.org_unit_id}
    )
    if doc is None:
        raise NotFound("文档不存在")
    return doc


async def _load_owned(ctx: RequestContext, document_id: str) -> dict[str, Any]:
    doc = await mongo.get_collection(_DOCS).find_one(_owner_query(ctx, document_id))
    if doc is None:
        raise NotFound("文档不存在")
    return doc


# ---------------------------------------------------------------------------
# 上传 / 更新 / 删除 / 重建 / 替换
# ---------------------------------------------------------------------------


@router.post("/documents")
async def upload_document(
    ctx: RequestContext = Depends(get_ctx),
    file: UploadFile = File(...),
    directoryId: str = Form(""),
    tags: str = Form(""),
) -> dict[str, Any]:
    """用户上传：复用管理端摄取管道（存原件 → 解析 → 索引），owner_scoped。"""
    from app.api.endpoints.knowledge_admin import _SUPPORTED, _extract_text
    from app.knowledge.indexer import index_document
    from app.settings import get_settings

    filename = file.filename or "document.txt"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _SUPPORTED:
        raise Forbidden(f"不支持的文件类型: {filename}（支持 pdf/docx/txt/md）")

    doc_id = uuid.uuid4().hex
    org = ctx.org_unit_id
    storage_path = f"knowledge/{org}/{doc_id}{ext}"
    mime_type, preview_mime_type = _SUPPORTED[ext]
    data = await file.read()

    root = get_settings().storage_root
    dir_path = root / "knowledge" / org
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{doc_id}{ext}").write_bytes(data)

    try:
        raw_text = _extract_text(data, filename)
    except Exception as exc:  # noqa: BLE001 - 解析失败明确报错
        raise Forbidden(f"文档解析失败: {exc}") from exc
    if not raw_text.strip():
        raise Forbidden("文档解析后无正文内容")

    docs = mongo.get_collection(_DOCS)
    await docs.insert_one(
        {
            "_id": doc_id,
            "org_unit_id": org,
            "owner_user_id": ctx.user_id,
            "directory_id": directoryId or "",
            "title": filename,
            "original_filename": filename,
            "filename": filename,
            "description": "",
            "tags": _parse_tags(tags),
            "mime_type": mime_type,
            "preview_mime_type": preview_mime_type,
            "storage_path": storage_path,
            "source_ref": storage_path,
            "source_type": "org",
            "status": "processing",
            "raw_text": raw_text,
            "acl": {"visibility": "org"},
            "created_at": utcnow(),
        }
    )

    # 4) 切片索引后台化（阶段 2 任务 #9：解析/索引不阻塞上传响应；
    #    status=processing 先返回，后台完成置 ready/failed。Celery 独立 worker 属部署期迁移）
    import asyncio

    async def _index_and_update() -> None:
        try:
            result = await index_document(doc_id)
            if result.status == "failed":
                raise ValueError(result.error)
        except Exception as exc:  # noqa: BLE001 - 索引失败回写状态，不静默
            await docs.update_one(
                {"_id": doc_id}, {"$set": {"status": "failed", "error": str(exc)}}
            )

    task = asyncio.create_task(_index_and_update())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return {"id": doc_id, "name": str(filename), "status": "processing", "chunkCount": 0}


class DocumentPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    directoryId: str | None = None


@router.patch("/documents/{document_id}")
async def update_document(
    document_id: str, body: DocumentPatch, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    await _load_owned(ctx, document_id)
    updates: dict[str, Any] = {"updated_at": utcnow()}
    if body.name is not None:
        updates["title"] = body.name.strip()
    if body.description is not None:
        updates["description"] = body.description
    if body.tags is not None:
        updates["tags"] = body.tags
    if body.directoryId is not None:
        updates["directory_id"] = body.directoryId
    await mongo.get_collection(_DOCS).update_one({"_id": document_id}, {"$set": updates})
    fresh = await mongo.get_collection(_DOCS).find_one({"_id": document_id}) or {}
    return _doc_to_personal(fresh, ctx)


@router.post("/documents/{document_id}/relearn")
async def relearn_document(
    document_id: str, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """重建索引（失败重试入口；status → processing → ready/failed）。"""
    await _load_owned(ctx, document_id)
    from app.knowledge.indexer import index_document

    docs = mongo.get_collection(_DOCS)
    await docs.update_one(
        {"_id": document_id}, {"$set": {"status": "processing", "error": ""}}
    )
    try:
        result = await index_document(document_id)
        if result.status == "failed":
            raise ValueError(result.error)
    except Exception as exc:  # noqa: BLE001 - 索引失败回写状态，不静默
        await docs.update_one(
            {"_id": document_id}, {"$set": {"status": "failed", "error": str(exc)}}
        )
        raise
    fresh = await docs.find_one({"_id": document_id}) or {}
    return _doc_to_personal(fresh, ctx)


@router.post("/documents/{document_id}/replace")
async def replace_document(
    document_id: str,
    ctx: RequestContext = Depends(get_ctx),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """替换原件：覆盖存储文件 + 重解析 + 重建索引。"""
    doc = await _load_owned(ctx, document_id)
    from app.api.endpoints.knowledge_admin import _SUPPORTED, _extract_text
    from app.knowledge.indexer import index_document
    from app.settings import get_settings

    filename = file.filename or doc.get("original_filename") or "document.txt"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _SUPPORTED:
        raise Forbidden(f"不支持的文件类型: {filename}（支持 pdf/docx/txt/md）")

    data = await file.read()
    mime_type, preview_mime_type = _SUPPORTED[ext]

    root = get_settings().storage_root
    old_path = root / str(doc.get("storage_path") or "")
    new_storage_path = f"knowledge/{ctx.org_unit_id}/{document_id}{ext}"
    new_path = root / new_storage_path
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_bytes(data)
    if old_path.resolve() != new_path.resolve() and old_path.is_file():
        old_path.unlink()

    try:
        raw_text = _extract_text(data, filename)
    except Exception as exc:  # noqa: BLE001
        raise Forbidden(f"文档解析失败: {exc}") from exc

    docs = mongo.get_collection(_DOCS)
    await docs.update_one(
        {"_id": document_id},
        {
            "$set": {
                "original_filename": filename,
                "filename": filename,
                "mime_type": mime_type,
                "preview_mime_type": preview_mime_type,
                "storage_path": new_storage_path,
                "source_ref": new_storage_path,
                "raw_text": raw_text,
                "status": "processing",
                "error": "",
                "updated_at": utcnow(),
            }
        },
    )
    try:
        result = await index_document(document_id)
        if result.status == "failed":
            raise ValueError(result.error)
    except Exception as exc:  # noqa: BLE001
        await docs.update_one(
            {"_id": document_id}, {"$set": {"status": "failed", "error": str(exc)}}
        )
        raise
    fresh = await docs.find_one({"_id": document_id}) or {}
    return _doc_to_personal(fresh, ctx)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """删除文档（本人上传的才可删）：原件 + chunks + 记录，重建 df。"""
    doc = await _load_owned(ctx, document_id)
    if doc.get("owner_user_id") != ctx.user_id:
        raise Forbidden("仅可删除本人上传的文档")

    from app.knowledge.indexer import rebuild_df
    from app.settings import get_settings

    storage_path = doc.get("storage_path")
    if storage_path:
        root = get_settings().storage_root
        path = (root / str(storage_path)).resolve()
        try:
            path.relative_to(root.resolve())
        except (ValueError, OSError) as exc:
            raise Forbidden("非法的存储路径") from exc
        if path.is_file():
            path.unlink()

    await mongo.get_collection(_CHUNKS).delete_many({"document_id": document_id})
    await mongo.get_collection(_SHARES).delete_many({"document_id": document_id})
    await mongo.get_collection(_DOCS).delete_one({"_id": document_id})
    await rebuild_df(ctx.org_unit_id)
    return {"ok": True}


@router.get("/counts")
async def knowledge_counts(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """徽标计数：我分享出去的活跃授权数 + 收到的分享总数/未读数。"""
    shares = mongo.get_collection(_SHARES)
    share_count = await shares.count_documents(
        {"org_unit_id": ctx.org_unit_id, "granted_by": ctx.user_id, "status": "active"}
    )
    received = await shares.count_documents(
        {"org_unit_id": ctx.org_unit_id, "recipient_user_id": ctx.user_id, "status": "active"}
    )
    unread = await shares.count_documents(
        {
            "org_unit_id": ctx.org_unit_id,
            "recipient_user_id": ctx.user_id,
            "status": "active",
            "seen": False,
        }
    )
    return {
        "unreadCount": unread,
        "shareCount": share_count,
        "receivedCount": received,
        "feedbackCount": 0,
    }


# ---------------------------------------------------------------------------
# 分享 / 授权（owner 直分享 + 授权管理）
# ---------------------------------------------------------------------------


class ShareRecipientBody(BaseModel):
    userId: str
    canReshare: bool = False


class ShareBody(BaseModel):
    recipients: list[ShareRecipientBody] = []


async def _load_shareable(ctx: RequestContext, document_id: str) -> dict[str, Any]:
    """可分享校验：owner 或持有 canReshare 授权。"""
    doc = await mongo.get_collection(_DOCS).find_one(_owner_query(ctx, document_id))
    if doc is not None:
        return doc
    grant = await mongo.get_collection(_SHARES).find_one(
        {
            "org_unit_id": ctx.org_unit_id,
            "document_id": document_id,
            "recipient_user_id": ctx.user_id,
            "status": "active",
            "can_reshare": True,
        }
    )
    if grant is None:
        raise NotFound("文档不存在")
    doc = await mongo.get_collection(_DOCS).find_one(
        {"_id": document_id, "org_unit_id": ctx.org_unit_id}
    )
    if doc is None:
        raise NotFound("文档不存在")
    return doc


@router.post("/documents/{document_id}/share")
async def share_document(
    document_id: str, body: ShareBody, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """直分享给指定成员（owner 或 canReshare 授权者；重复分享即更新授权）。"""
    await _load_shareable(ctx, document_id)
    shares = mongo.get_collection(_SHARES)
    users = mongo.get_collection("users")
    now = utcnow()
    recipients: list[dict[str, Any]] = []
    for item in body.recipients:
        if not item.userId or item.userId == ctx.user_id:
            continue
        user = await users.find_one(
            {"_id": _uid(item.userId), "org_unit_id": ctx.org_unit_id}
        )
        if user is None:
            continue
        recipient_id = str(user["_id"])
        await shares.update_one(
            {
                "org_unit_id": ctx.org_unit_id,
                "document_id": document_id,
                "recipient_user_id": recipient_id,
            },
            {
                "$set": {
                    "can_reshare": item.canReshare,
                    "status": "active",
                    "granted_by": ctx.user_id,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now, "seen": False},
            },
            upsert=True,
        )
        recipients.append(
            {
                "userId": recipient_id,
                "displayName": str(user.get("display_name") or ""),
                "username": str(user.get("username") or ""),
            }
        )
    return {"shared": True, "recipientCount": len(recipients), "recipients": recipients}


@router.get("/documents/{document_id}/grants")
async def list_grants(
    document_id: str, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """授权列表（owner 视角：谁拿到这篇文档、能否再分享）。"""
    await _load_owned(ctx, document_id)
    rows = await mongo.get_collection(_SHARES).find(
        {"org_unit_id": ctx.org_unit_id, "document_id": document_id, "status": "active"}
    ).to_list(length=None)
    users = mongo.get_collection("users")
    items: list[dict[str, Any]] = []
    for g in rows:
        user = await users.find_one(
            {"_id": _uid(g.get("recipient_user_id"))},
            {"display_name": 1, "username": 1, "email": 1},
        )
        granter = await users.find_one(
            {"_id": _uid(g.get("granted_by"))}, {"display_name": 1, "username": 1}
        )
        items.append(
            {
                "userId": str(g.get("recipient_user_id") or ""),
                "user": {
                    "displayName": str((user or {}).get("display_name") or ""),
                    "username": str((user or {}).get("username") or ""),
                },
                "grantedByUserId": str(g.get("granted_by") or ""),
                "grantedBy": {
                    "displayName": str((granter or {}).get("display_name") or ""),
                    "username": str((granter or {}).get("username") or ""),
                },
                "canReshare": bool(g.get("can_reshare")),
                "status": "active",
                "sharedAt": _iso(g.get("updated_at")),
            }
        )
    return {"items": items}


class GrantPatch(BaseModel):
    canReshare: bool = False


@router.patch("/documents/{document_id}/grants/{user_id}")
async def update_grant(
    document_id: str, user_id: str, body: GrantPatch, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """更新授权（canReshare 开关）。"""
    await _load_owned(ctx, document_id)
    result = await mongo.get_collection(_SHARES).update_one(
        {
            "org_unit_id": ctx.org_unit_id,
            "document_id": document_id,
            "recipient_user_id": user_id,
            "status": "active",
        },
        {"$set": {"can_reshare": body.canReshare, "updated_at": utcnow()}},
    )
    if result.matched_count == 0:
        raise NotFound("授权不存在")
    return {"ok": True}


@router.delete("/documents/{document_id}/grants/{user_id}")
async def revoke_grant(
    document_id: str, user_id: str, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """吊销授权（owner 或授权者本人；吊销后对方 shared 视图即不可见）。"""
    await _load_owned(ctx, document_id)
    result = await mongo.get_collection(_SHARES).update_one(
        {
            "org_unit_id": ctx.org_unit_id,
            "document_id": document_id,
            "recipient_user_id": user_id,
            "status": "active",
        },
        {"$set": {"status": "revoked", "updated_at": utcnow()}},
    )
    if result.matched_count == 0:
        raise NotFound("授权不存在")
    return {"ok": True}


def _parse_tags(raw: str) -> list[str]:
    """逗号分隔标签串 → 列表（去空去重）。"""
    return list(dict.fromkeys(t.strip() for t in (raw or "").split(",") if t.strip()))
