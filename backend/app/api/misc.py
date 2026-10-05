"""标签、健康检查与封面服务。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import engine, get_session
from app.models import Tag, Video, VideoTag
from app.schemas import HealthOut, TagSummary

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["misc"])

# 视频编号可能复用，封面也可能重新生成；每次校验，禁止共享缓存旧图。
_CACHE_HEADERS = {"Cache-Control": "private, no-cache"}


# ---------------------------------------------------------------- 标签


@router.get("/tags", response_model=list[TagSummary])
async def list_tags(session: AsyncSession = Depends(get_session), media_type: Literal["video", "photo"] = "video"):
    """列出所有标签，附带使用计数（前端筛选栏用）。"""
    rows = (
        await session.execute(
            select(Tag, func.count(VideoTag.video_id))
            .outerjoin(VideoTag, VideoTag.tag_id == Tag.id)
            .where(Tag.media_type == media_type)
            .group_by(Tag.id)
            .order_by(Tag.name)
        )
    ).all()
    return [TagSummary(id=t.id, name=t.name, color=t.color, media_type=t.media_type, video_count=count) for t, count in rows]


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: int,
    expected_count: int | None = Query(default=None, ge=0),
    media_type: Literal["video", "photo"] = "video",
    session: AsyncSession = Depends(get_session),
):
    # Keep usage verification and deletion in one SQLite write transaction.
    await session.execute(text("BEGIN IMMEDIATE"))
    tag = await session.get(Tag, tag_id)
    if tag is None or tag.media_type != media_type:
        raise HTTPException(404, "当前媒体库中不存在该标签")
    count = await session.scalar(
        select(func.count()).select_from(VideoTag).where(VideoTag.tag_id == tag_id)
    )
    if expected_count is not None and count != expected_count:
        raise HTTPException(409, "标签使用数量已变化，请重新查看并确认删除")
    # Delete only associations and the tag, atomically; never load/delete videos.
    await session.execute(delete(VideoTag).where(VideoTag.tag_id == tag_id))
    await session.execute(delete(Tag).where(Tag.id == tag_id))
    await session.commit()


# ---------------------------------------------------------------- 封面


@router.get("/videos/{video_id}/cover")
async def get_cover(video_id: int, session: AsyncSession = Depends(get_session)):
    video = await session.get(Video, video_id)
    if video is None or not video.cover_path:
        raise HTTPException(404, "无封面")
    path = settings.data_dir / video.cover_path
    if not path.exists():
        raise HTTPException(404, "封面文件缺失")
    return FileResponse(path, media_type="image/jpeg", headers=_CACHE_HEADERS)


@router.get("/videos/{video_id}/thumb")
async def get_thumb(video_id: int, session: AsyncSession = Depends(get_session)):
    video = await session.get(Video, video_id)
    if video is None or not video.thumb_path:
        raise HTTPException(404, "无缩略图")
    path = settings.data_dir / video.thumb_path
    if not path.exists():
        raise HTTPException(404, "缩略图文件缺失")
    return FileResponse(path, media_type="image/jpeg", headers=_CACHE_HEADERS)


# ---------------------------------------------------------------- 健康检查


async def _check_db() -> bool:
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text

            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        log.warning("数据库自检失败: %s", e)
        return False


async def _check_redis() -> bool:
    try:
        from app.workers.queue import get_pool

        pool = await get_pool()
        await pool.ping()
        return True
    except Exception as e:
        log.warning("Redis 自检失败: %s", e)
        return False


async def _check_ffmpeg() -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.ffmpeg_bin,
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        first = out.decode("utf-8", "replace").splitlines()[0] if out else ""
        return proc.returncode == 0, first[:120]
    except Exception as e:
        return False, str(e)[:120]


async def _check_vaapi() -> tuple[bool, str]:
    """实测 VAAPI 能否编码，而不是只看设备节点是否存在。"""
    dev = Path(settings.vaapi_device)
    if not dev.exists():
        return False, f"设备不存在: {dev}"
    cmd = [
        settings.ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-init_hw_device",
        f"vaapi=va:{dev}",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=1:duration=1",
        "-vf",
        "format=nv12,hwupload",
        "-c:v",
        "h264_vaapi",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            return True, f"VAAPI 硬编可用 ({dev})"
        return False, err.decode("utf-8", "replace").strip()[:150]
    except Exception as e:
        return False, str(e)[:150]


async def _check_openlist() -> tuple[bool | None, str]:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{settings.openlist_base_url}/api/public/settings")
            if r.status_code == 200:
                return True, "OpenList 可达"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"不可达: {str(e)[:100]}"


@router.get("/health", response_model=HealthOut)
async def health():
    db_ok, redis_ok = await asyncio.gather(_check_db(), _check_redis())
    ff_ok, ff_msg = await _check_ffmpeg()
    va_ok, va_msg = await _check_vaapi()
    ol_ok, ol_msg = await _check_openlist()

    # 整体状态：数据库和 ffmpeg 是硬依赖，其余降级不影响核心功能
    status = "ok" if (db_ok and ff_ok) else "degraded"

    return HealthOut(
        status=status,
        database=db_ok,
        redis=redis_ok,
        openlist=ol_ok,
        vaapi=va_ok,
        ffmpeg=ff_ok,
        detail={
            "ffmpeg": ff_msg,
            "vaapi": va_msg,
            "openlist": ol_msg,
            "database_url": settings.database_url.split("///")[-1],
        },
    )
