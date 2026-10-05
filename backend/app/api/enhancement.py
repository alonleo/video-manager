"""Enhancement jobs, comparison previews, downloads and library import."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.photos import require_video
from app.db import get_session
from app.models import Job, JobState, Storage, StorageType, Video, VideoSource
from app.schemas import JobOut
from app.services.conversion_media import check_conflicting_jobs, fingerprint
from app.services.directory_categories import library_write_lock
from app.services.enhancement import (
    MODELS,
    EnhanceRequest,
    ai_available,
    hardware_available,
    model_size,
    output_path,
    plan_enhancement,
)
from app.services.operations import ConvertRequest, local_source
from app.services.probe import _run_ffprobe, extract_thumbnails, parse_probe_json
from app.workers.queue import get_pool

router = APIRouter(tags=["video-enhancement"])


@router.get("/api/enhancement/capabilities")
async def capabilities():
    return {
        "hardware": await hardware_available(),
        "models": [
            {
                "id": key,
                "name": label,
                "available": ai_available(key),
                "size_bytes": model_size(key),
            }
            for key, (name, _scale, label) in MODELS.items()
        ],
        "max_fps": 120,
        "max_resolution": 2160,
    }


@router.post("/api/videos/{video_id}/enhance", response_model=JobOut, status_code=202)
async def enhance(
    video_id: int, body: EnhanceRequest, session: AsyncSession = Depends(get_session)
):
    await require_video(session, video_id)
    if body.method == "ai" and not ai_available(body.ai_model):
        raise HTTPException(409, "AI 超分模型尚未就绪，请选择普通放大")
    with library_write_lock():
        source, path = await local_source(session, video_id, body.source_id)
        await check_conflicting_jobs(session, source, ConvertRequest())
        try:
            plan_enhancement(body, await _run_ffprobe(str(path)))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        options = body.model_copy(update={"source_id": source.id}).model_dump()
        active = list(
            (
                await session.scalars(
                    select(Job).where(
                        Job.kind.in_(["enhance_video", "enhance_preview"]),
                        Job.video_id == video_id,
                        Job.state.in_([JobState.queued, JobState.running]),
                    )
                )
            ).all()
        )
        for job in active:
            if job.payload.get("options") == options:
                return JobOut.model_validate(job)
        if len(active) >= 3:
            raise HTTPException(409, "此视频已有 3 个增强任务，请等待完成或取消后再提交")
        job = Job(
            kind="enhance_preview" if body.preview else "enhance_video",
            video_id=video_id,
            payload={"options": options, "fingerprint": fingerprint(path)},
            state=JobState.queued,
            message="等待生成预览" if body.preview else "等待增强视频",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
    job_id = job.id
    try:
        pool = await asyncio.wait_for(get_pool(), 5)
        queued = await asyncio.wait_for(
            pool.enqueue_job(
                "run_enhancement",
                job.id,
                _job_id=f"enhance-{job.id}",
                _queue_name="arq:enhancement",
            ),
            5,
        )
        if queued is None:
            raise RuntimeError("任务未入队")
        job.arq_job_id = queued.job_id
        await session.commit()
        await session.refresh(job)
    except Exception as exc:
        await session.rollback()
        await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.state == JobState.queued)
            .values(
                state=JobState.error, error="任务队列不可用，请重试", finished_at=datetime.now(UTC)
            )
        )
        await session.commit()
        raise HTTPException(503, "任务队列不可用，请重试") from exc
    return JobOut.model_validate(job)


async def finished_job(session, job_id):
    job = await session.get(Job, job_id)
    if not job or job.kind not in {"enhance_video", "enhance_preview"}:
        raise HTTPException(404, "增强任务不存在")
    if job.state != JobState.done or not job.result:
        raise HTTPException(409, "增强任务尚未完成")
    return job


@router.get("/api/jobs/{job_id}/enhancement-file")
async def enhanced_file(
    job_id: int,
    original: bool = False,
    download: bool = False,
    session: AsyncSession = Depends(get_session),
):
    job = await finished_job(session, job_id)
    if original and not job.result.get("preview"):
        raise HTTPException(404, "此任务没有原片对比预览")
    path = output_path(job_id, "original.mp4" if original else "result.mp4")
    if not path.is_file():
        raise HTTPException(410, "增强文件已被移动或清理")
    name = "original-preview.mp4" if original else job.result["filename"]
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = (
            f"attachment; filename=enhanced-{job_id}.mp4; filename*=UTF-8''{quote(name)}"
        )
    return FileResponse(path, media_type="video/mp4", headers=headers)


@router.post("/api/jobs/{job_id}/enhancement-import")
async def import_enhanced(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await finished_job(session, job_id)
    if job.result.get("preview"):
        raise HTTPException(422, "预览片段不能作为完整视频加入影像库")
    imported_id = job.result.get("imported_video_id")
    if imported_id and await session.get(Video, imported_id):
        return {"video_id": imported_id}
    path = output_path(job_id).resolve()
    if not path.is_file():
        raise HTTPException(410, "增强文件已被移动或清理")
    raw = await _run_ffprobe(str(path))
    info = parse_probe_json(raw)
    # Only explicitly imported files are scanned; preview and partial files stay outside.
    root = (settings.downloads_dir / "enhanced-library").resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / Path(job.result["filename"]).name
    with library_write_lock():
        existing = await session.scalar(
            select(VideoSource).where(VideoSource.local_path == str(destination))
        )
        if existing:
            return {"video_id": existing.video_id}
        storage = await session.scalar(select(Storage).where(Storage.name == "画质增强结果"))
        if storage and (
            storage.type != StorageType.local
            or Path(storage.config.get("root", "")).resolve() != root
        ):
            raise HTTPException(409, "同名存储源指向其他目录，请先修改其名称")
        if storage is None:
            storage = Storage(
                name="画质增强结果", type=StorageType.local, config={"root": str(root)}
            )
            session.add(storage)
            await session.flush()
        elif not storage.enabled:
            storage.enabled = True
        linked = False
        try:
            if destination.exists():
                if not destination.samefile(path):
                    raise HTTPException(409, "同名增强文件已存在，请先检查结果目录")
            else:
                os.link(path, destination)
                linked = True
            video = Video(
                title=Path(job.result["filename"]).stem,
                source_kind="local",
                status="ready",
                meta=raw,
                **{
                    key: getattr(info, key)
                    for key in [
                        "duration_ms",
                        "width",
                        "height",
                        "fps",
                        "video_codec",
                        "video_profile",
                        "pix_fmt",
                        "audio_codec",
                        "audio_channels",
                        "container",
                        "bitrate_bps",
                        "size_bytes",
                    ]
                },
            )
            session.add(video)
            await session.flush()
            session.add(
                VideoSource(
                    video_id=video.id,
                    storage_id=storage.id,
                    source_type=StorageType.local,
                    uri=f"local:{destination}",
                    local_path=str(destination),
                    is_primary=True,
                    available=True,
                    size_bytes=path.stat().st_size,
                    duration_ms=info.duration_ms,
                )
            )
            job.result = {**job.result, "imported_video_id": video.id}
            await session.commit()
            video_id = video.id
        except BaseException:
            await session.rollback()
            if linked:
                destination.unlink(missing_ok=True)
            raise
    try:
        cover, thumb = await extract_thumbnails(str(destination), video_id, info.duration_ms)
        await session.execute(
            update(Video).where(Video.id == video_id).values(cover_path=cover, thumb_path=thumb)
        )
        await session.commit()
    except Exception:
        # Import succeeded; a thumbnail failure must not suggest that it failed.
        pass
    return {"video_id": video_id}
