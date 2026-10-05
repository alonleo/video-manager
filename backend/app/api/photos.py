"""Photo preview and original-file download endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Video
from app.services.operations import local_source
from app.services.photos import BROWSER_MIMES, inspect_photo, photo_signature

router = APIRouter(prefix="/api/photos", tags=["photos"])


@router.get("/{photo_id}/image")
async def photo_image(
    photo_id: int, download: bool = False, session: AsyncSession = Depends(get_session)
):
    photo = await session.get(Video, photo_id)
    if photo is None or photo.media_type != "photo":
        raise HTTPException(404, "照片不存在")
    _, source_path = await local_source(session, photo_id)
    headers = {"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"}
    if download:
        return FileResponse(
            source_path,
            filename=source_path.name,
            media_type="application/octet-stream",
            headers=headers,
        )
    info = (photo.meta or {}).get("photo", {})
    cached = settings.data_dir / info.get("preview_path", "")
    if info.get("signature") != photo_signature(source_path) or not cached.is_file():
        try:
            info = await asyncio.to_thread(inspect_photo, source_path)
        except Exception as exc:
            raise HTTPException(422, "照片预览失败，请检查文件是否损坏或超过安全像素上限") from exc
        photo.meta = {"photo": info}
        photo.width, photo.height = info["width"], info["height"]
        photo.size_bytes = source_path.stat().st_size
        photo.container = info["format"].lower()
        photo.cover_path, photo.thumb_path = info["cover_path"], info["thumb_path"]
        photo.status = "indexed"
        await session.commit()
        cached = settings.data_dir / info["preview_path"]
    mime = BROWSER_MIMES.get(info.get("format"))
    # Common formats retain original detail/animation. HEIC/TIFF/BMP use a JPEG preview.
    return FileResponse(
        source_path if mime else cached, media_type=mime or "image/jpeg", headers=headers
    )
