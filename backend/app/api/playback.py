"""Playback decisions, on-demand compatible preparation and seekable HTTP Range streaming."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.photos import require_video
from app.db import get_session
from app.models import Video, VideoSource, WatchHistory
from app.schemas import PlaybackInfo
from app.services import playback as playback_svc
from app.services import transport

router = APIRouter(prefix="/api/videos", tags=["playback"])


async def _pick_source(session: AsyncSession, video_id: int) -> VideoSource:
    sources = (
        (
            await session.execute(
                select(VideoSource)
                .where(VideoSource.video_id == video_id, VideoSource.available.is_(True))
                .order_by(VideoSource.is_primary.desc(), VideoSource.id)
            )
        )
        .scalars()
        .all()
    )
    if not sources:
        raise HTTPException(404, "该视频没有可用的来源（文件可能已被移动或删除）")
    return sources[0]


def _decision(video: Video, source: VideoSource, compatible: bool = False):
    container = video.container
    if source.local_path and transport.is_transport_stream(container, Path(source.local_path)):
        container = "mpegts"
    return playback_svc.decide(
        container=container,
        video_codec=video.video_codec,
        video_profile=video.video_profile,
        audio_codec=video.audio_codec,
        pix_fmt=video.pix_fmt,
        extension=Path(source.local_path).suffix if source.local_path else None,
        force_compatible=compatible,
    )


async def _spec(video: Video, source: VideoSource, mode: str) -> transport.MediaSpec:
    try:
        return await asyncio.to_thread(
            transport.MediaSpec.from_path,
            Path(source.local_path),
            mode,
            video.audio_codec,
            video.duration_ms,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(410, "源视频已不可读取，请检查文件或磁盘挂载") from exc


def _uses_cache(video: Video, source: VideoSource, compatible: bool = False) -> bool:
    return bool(
        source.source_type.value == "local"
        and source.local_path
        and _decision(video, source, compatible).needs_ffmpeg
    )


async def _build_info(
    video_id: int, request: Request, session: AsyncSession, *, retry=False, compatible=False, engine="browser"
):
    video = await require_video(session, video_id)
    source = await _pick_source(session, video_id)
    decision = _decision(video, source, compatible)
    history = await session.get(WatchHistory, video_id)
    start_ms = history.position_ms if history and not history.finished else 0
    if video.duration_ms and start_ms > video.duration_ms * 0.95:
        start_ms = 0
    base = str(request.base_url).rstrip("/")
    info = PlaybackInfo(
        video_id=video_id,
        mode=decision.mode.value,
        reason=decision.reason,
        duration_ms=video.duration_ms,
        start_position_ms=start_ms,
        container=video.container,
        video_codec=video.video_codec,
        audio_codec=video.audio_codec,
        compatibility_available=bool(source.source_type.value == "local" and source.local_path),
        compatibility_mode=compatible,
    )
    if engine == "wasm":
        info.mode = "wasm"
        info.reason = "独立解码 · 原文件播放"
        info.compatibility_available = False
        info.compatibility_mode = False
        if source.source_type.value == "local" and source.local_path:
            info.source_extension = Path(source.local_path).suffix.lower().lstrip(".")
            info.stream_url = f"/api/videos/{video_id}/stream?engine=wasm"
        else:
            info.reason = "此来源尚未接入在线播放，请使用本地文件或已挂载的目录"
        return info
    if _uses_cache(video, source, compatible):
        spec = await _spec(video, source, decision.mode.value)
        preparation = await transport.cache.ensure(spec, retry=retry)
        info.preparation_state = preparation.state
        info.preparation_progress = preparation.progress
        info.preparation_error = preparation.error
        if preparation.state == "ready":
            # Fingerprint prevents mixing ranges from different source revisions.
            info.stream_url = (
                f"{base}/api/videos/{video_id}/stream?version={spec.key}"
                f"&compatible={str(compatible).lower()}"
            )
        # A discarded cache or changed source can be resolved once by the player.
        info.needs_refresh = True
    elif source.source_type.value == "local" and decision.mode.value == "direct":
        info.stream_url = f"{base}/api/videos/{video_id}/stream"
    else:
        info.reason = "此来源尚未接入在线播放，请使用本地文件或已挂载的目录"

    return info


@router.get("/{video_id}/playback-info", response_model=PlaybackInfo)
async def playback_info(
    video_id: int,
    request: Request,
    compatible: bool = False,
    engine: Literal["browser", "wasm"] = "browser",
    session: AsyncSession = Depends(get_session),
):
    return await _build_info(video_id, request, session, compatible=compatible, engine=engine)


@router.post("/{video_id}/playback-retry", response_model=PlaybackInfo)
async def retry_playback(
    video_id: int,
    request: Request,
    compatible: bool = False,
    session: AsyncSession = Depends(get_session),
):
    return await _build_info(video_id, request, session, retry=True, compatible=compatible)


class CachedFileResponse(FileResponse):
    """Protect an open transfer from LRU eviction, including disconnect/error paths."""

    def __init__(self, path: Path, key: str):
        self.key = key
        self.owner = transport.cache
        self.owner.hold(key)
        super().__init__(
            path,
            media_type="video/mp4",
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.owner.release(self.key)


@router.api_route("/{video_id}/stream", methods=["GET", "HEAD"])
async def stream(
    video_id: int,
    request: Request,
    version: str | None = None,
    engine: Literal["browser", "wasm"] = "browser",
    compatible: bool = False,
    session: AsyncSession = Depends(get_session),
):
    video = await require_video(session, video_id)
    source = await _pick_source(session, video_id)
    if source.source_type.value != "local" or not source.local_path:
        raise HTTPException(501, "该来源的播放尚未实现")
    path = Path(source.local_path)
    if not path.is_file():
        raise HTTPException(410, "源文件已不可访问，外接盘可能未挂载")
    if engine != "wasm" and _uses_cache(video, source, compatible):
        spec = await _spec(video, source, _decision(video, source, compatible).mode.value)
        if version is not None and version != spec.key:
            raise HTTPException(409, "源文件已更新，请重新获取播放信息")
        preparation = await transport.cache.ensure(spec)
        if preparation.state == "error":
            raise HTTPException(422, preparation.error)
        if preparation.state != "ready" or not preparation.path:
            raise HTTPException(
                425, "正在准备兼容播放文件，请稍后重试", headers={"Retry-After": "2"}
            )
        return CachedFileResponse(preparation.path, spec.key)
    return FileResponse(
        path,
        media_type=(
            "video/mp4"
            if path.suffix.lower() in {".mp4", ".m4v"}
            else mimetypes.guess_type(path.name)[0]
        )
        or "application/octet-stream",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
    )
