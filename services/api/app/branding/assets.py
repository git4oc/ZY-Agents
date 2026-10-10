"""品牌资产上传（``branding/assets.py``，``01`` §4.8 / ``00`` §12.7 / ``02`` §2.9）。

- 类型按**内容嗅探**（不信任扩展名，``00`` §12.7）。
- 文件与像素尺寸上限（``BRANDING_ASSET_MAX_BYTES`` / ``BRANDING_ASSET_MAX_PIXELS``）。
- 存储文件名由**服务端生成**（UUID），**禁止请求参数参与路径拼接**。
- 文件落 ``branding-assets`` 卷；Mongo 只存 ``AssetRef``（``02`` §2.9）。
"""

from __future__ import annotations

import struct
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from fastapi import UploadFile

from app.core.errors import BrandingValidationError
from app.core.logging import get_logger
from app.identity.context import RequestContext
from app.branding.repository import BrandingRepository
from app.branding.settings import BrandingSettings, invalidate_cache, _default_settings, _settings_from_doc

log = get_logger("branding.assets")

Slot = Literal["logo_wide", "logo_square", "favicon"]

#: slot → 允许的图片类型（按内容嗅探）
_ALLOWED_MIME: dict[str, set[str]] = {
    "logo_wide": {"image/png", "image/jpeg", "image/webp", "image/svg+xml"},
    "logo_square": {"image/png", "image/jpeg", "image/webp", "image/svg+xml"},
    "favicon": {"image/png", "image/jpeg", "image/webp", "image/x-icon"},
}

#: 支持按内容嗅探的魔数 → mime
_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
    (b"<svg", "image/svg+xml"),
]


def _sniff_mime(data: bytes) -> str:
    """按内容嗅探 MIME（不信任扩展名）。未知返回空串。"""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    return ""


def _image_dimensions(mime: str, data: bytes) -> tuple[int, int] | None:
    """读取图片像素尺寸（PNG/JPEG/WebP），用于防解压炸弹。"""
    try:
        if mime == "image/png" and len(data) >= 24:
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        if mime == "image/jpeg" and len(data) >= 4 and data.startswith(b"\xff\xd8"):
            i = 2
            while i + 9 < len(data):
                marker = data[i]
                if marker != 0xFF:
                    i += 1
                    continue
                seg = data[i + 1]
                if seg in (0xC0, 0xC1, 0xC2, 0xC3):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return int(w), int(h)
                length = struct.unpack(">H", data[i + 2 : i + 4])[0]
                i += 2 + length
        if mime == "image/webp" and len(data) >= 30:
            # RIFF....WEBPVP8（lossy）或 VP8L / VP8X
            if data[12:16] == b"VP8X":
                w = 1 + struct.unpack("<I", data[24:28])[0] & 0xFFFFFF
                h = 1 + struct.unpack("<I", data[27:31])[0] & 0xFFFFFF
            else:
                w, h = struct.unpack("<HH", data[26:30])
            return int(w), int(h)
    except Exception:  # noqa: BLE001 - 尺寸解析失败不致命
        return None
    return None


async def put_asset(
    ctx: RequestContext,
    *,
    slot: Slot,
    upload: UploadFile,
    max_bytes: int = 1048576,
    max_pixels: int = 4096,
    storage_root: Path = Path("/data/branding-assets"),
) -> dict[str, Any]:
    """上传一个品牌资产并返回 AssetRef。

    参数
    ----
    slot:
        资产位（logo_wide / logo_square / favicon）。
    upload:
        上传文件。
    max_bytes / max_pixels:
        字节与单边像素上限（来自配置）。

    返回 AssetRef ``{storage_key, content_type, bytes, width, height}``。
    """
    # 读取字节（限制读入上限，防超大上传打满内存）
    data = await _read_limited(upload, max_bytes)
    if len(data) == 0:
        raise BrandingValidationError("上传文件为空")

    mime = _sniff_mime(data)
    if mime not in _ALLOWED_MIME.get(slot, set()):
        raise BrandingValidationError(
            f"slot={slot} 不接受该文件类型（嗅探到 {mime or 'unknown'}）"
        )

    # 尺寸校验（svg 无固定像素，跳过）
    dims = _image_dimensions(mime, data) if mime != "image/svg+xml" else None
    if dims is not None:
        w, h = dims
        if w > max_pixels or h > max_pixels:
            raise BrandingValidationError(
                f"图片尺寸 {w}x{h} 超过单边上限 {max_pixels}px"
            )

    # 服务端命名（UUID），禁止请求参数参与路径拼接
    storage_key = f"{slot}/{uuid.uuid4().hex}{_ext_for(mime)}"
    path = storage_root / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    asset_ref = {
        "storage_key": storage_key,
        "content_type": mime,
        "bytes": len(data),
        "width": (dims[0] if dims else None),
        "height": (dims[1] if dims else None),
    }

    # 更新单文档中的资产字段
    repo = BrandingRepository()
    from app.branding.settings import _settings_from_doc

    doc = await repo.get()
    settings = _settings_from_doc(doc) if doc else _default_settings()
    payload = settings.model_dump()
    payload[slot] = asset_ref
    payload["updated_at"] = _utcnow()
    payload["updated_by"] = ctx.user_id
    await repo.upsert(payload)
    invalidate_cache()
    return asset_ref


async def delete_asset(ctx: RequestContext, *, slot: str) -> None:
    """移除一个品牌资产。"""
    if slot not in ("logo_wide", "logo_square", "favicon"):
        raise BrandingValidationError(f"未知资产位: {slot}")
    repo = BrandingRepository()
    doc = await repo.get()
    settings = _settings_from_doc(doc) if doc else _default_settings()
    payload = settings.model_dump()
    payload[slot] = None
    payload["updated_at"] = _utcnow()
    payload["updated_by"] = ctx.user_id
    await repo.upsert(payload)
    invalidate_cache()


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BrandingValidationError(f"文件超过大小上限 {max_bytes} 字节")
    return data


def _ext_for(mime: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
    }.get(mime, ".bin")


def _utcnow():
    from app.core.clock import utcnow

    return utcnow()
