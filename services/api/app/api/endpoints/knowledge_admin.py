"""知识库管理端点（``api/endpoints/knowledge_admin.py``，阶段 2 摄入管道）。

上传 → 解析（PDF/Word/txt/md → 文本）→ 切片索引（复用 ``knowledge/indexer.py``）
→ 状态回写。文档读取/预览/搜索契约在 ``knowledge.py``，本文件只做管理写。

- ``POST   /api/v1/admin/knowledge/documents``：multipart 上传，解析并索引。
- ``GET    /api/v1/admin/knowledge/documents``：文档列表（含解析状态）。
- ``DELETE /api/v1/admin/knowledge/documents/{document_id}``：删文档 + chunks + 原件。

纪律：
- ``storage_path`` 相对 ``storage_root``（与 ``knowledge.py`` 预览的目录穿越防护对齐）；
- 解析/索引失败 status=failed + error，不静默（``01`` §5.1 同口径）；
- api_key / 嵌入向量不经此端点下发（``00`` §3.2 不变量）。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core import mongo
from app.core.clock import utcnow
from app.core.errors import Forbidden, NotFound
from app.deps import get_ctx
from app.identity.context import RequestContext

router = APIRouter(prefix="/api/v1/admin/knowledge", tags=["knowledge-admin"])

_DOCS = "knowledge_documents"

#: 后台索引任务引用（防 asyncio.create_task 被垃圾回收）
_BACKGROUND_TASKS: set = set()
_CHUNKS = "knowledge_chunks"
_TERMS = "knowledge_terms"

#: 允许的扩展名 → (mime_type, preview_mime_type)
_SUPPORTED: dict[str, tuple[str, str]] = {
    ".pdf": ("application/pdf", "application/pdf"),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    ),
    ".txt": ("text/plain", "text/plain"),
    ".md": ("text/markdown", "text/markdown"),
}


def _guard(ctx: RequestContext) -> None:
    """知识库管理写权限：admin 直通，或显式 ``knowledge.manage``。"""
    if "admin" in ctx.roles or "knowledge.manage" in ctx.permissions:
        return
    raise Forbidden("需要 knowledge.manage 权限")


def _extract_text(data: bytes, filename: str) -> str:
    """按扩展名解析文本（PDF → PyMuPDF；Word → python-docx；txt/md → 直接读）。"""
    import io

    lower = filename.lower()
    if lower.endswith(".pdf"):
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            return "\n".join(page.get_text() for page in pdf)
    if lower.endswith(".docx"):
        import docx

        document = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs if p.text)
    if lower.endswith(".txt") or lower.endswith(".md"):
        return data.decode("utf-8", errors="replace")
    raise ValueError(f"不支持的文件类型: {filename}")


@router.post("/documents")
async def upload_document(
    ctx: RequestContext = Depends(get_ctx),
    file: UploadFile = File(...),
    title: str = Form(""),
) -> dict[str, Any]:
    """上传文档：存原件 → 解析文本 → 切片索引（复用 ``knowledge/indexer.py``）。"""
    _guard(ctx)

    filename = file.filename or "document.txt"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _SUPPORTED:
        raise Forbidden(f"不支持的文件类型: {filename}（支持 pdf/docx/txt/md）")

    from app.knowledge.indexer import index_document
    from app.settings import get_settings

    doc_id = uuid.uuid4().hex
    org = ctx.org_unit_id
    storage_path = f"knowledge/{org}/{doc_id}{ext}"
    mime_type, preview_mime_type = _SUPPORTED[ext]
    data = await file.read()

    # 1) 存原件（storage_path 相对 storage_root，与 knowledge.py 预览同约定）
    root = get_settings().storage_root
    dir_path = root / "knowledge" / org
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{doc_id}{ext}").write_bytes(data)

    # 2) 解析文本（失败不建记录，原件留作排查）
    try:
        raw_text = _extract_text(data, filename)
    except Exception as exc:  # noqa: BLE001 - 解析失败明确报错
        raise Forbidden(f"文档解析失败: {exc}") from exc
    if not raw_text.strip():
        raise Forbidden("文档解析后无正文内容")

    # 3) 建文档记录（status=processing → index_document 置 ready）
    docs = mongo.get_collection(_DOCS)
    await docs.insert_one(
        {
            "_id": doc_id,
            "org_unit_id": org,
            "title": title or filename,
            "original_filename": filename,
            "filename": filename,
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

    return {"id": doc_id, "title": title or filename, "status": "processing", "chunk_count": 0}


@router.get("/documents")
async def list_documents(ctx: RequestContext = Depends(get_ctx)) -> dict[str, Any]:
    """文档列表（含解析状态）。"""
    _guard(ctx)
    docs = mongo.get_collection(_DOCS)
    rows = await docs.find({"org_unit_id": ctx.org_unit_id}).to_list(length=None)
    return {
        "items": [
            {
                "id": str(r.get("_id")),
                "title": str(r.get("title") or r.get("name") or ""),
                "original_filename": str(r.get("original_filename") or r.get("filename") or ""),
                "file_ext": _ext_of(r),
                "status": str(r.get("status") or ""),
                "chunk_count": int(r.get("chunk_count") or 0),
                "error": str(r.get("error") or ""),
            }
            for r in rows
        ]
    }


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str, ctx: RequestContext = Depends(get_ctx)
) -> dict[str, Any]:
    """删除文档：原件 + chunks + 记录一并清理，重建该 org 的 df 统计。"""
    _guard(ctx)
    docs = mongo.get_collection(_DOCS)
    doc = await docs.find_one({"_id": document_id})
    if doc is None:
        raise NotFound("文档不存在")

    storage_path = doc.get("storage_path")
    if storage_path:
        from app.settings import get_settings

        root = get_settings().storage_root
        path = (root / str(storage_path)).resolve()
        try:
            path.relative_to(root.resolve())
        except (ValueError, OSError) as exc:
            raise Forbidden("非法的存储路径") from exc
        if path.is_file():
            path.unlink()

    await mongo.get_collection(_CHUNKS).delete_many({"document_id": document_id})
    await docs.delete_one({"_id": document_id})

    from app.knowledge.indexer import rebuild_df

    await rebuild_df(ctx.org_unit_id)
    return {"ok": True}


def _ext_of(doc: dict[str, Any]) -> str:
    """从原件名推导扩展名（与 knowledge.py::_file_ext 同口径，容错缺字段）。"""
    filename = str(doc.get("original_filename") or doc.get("filename") or "")
    return ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
