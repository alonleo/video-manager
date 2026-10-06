"""Rename videos and enqueue container conversion with optional subtitles/replacement."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.photos import require_video
from app.db import get_session
from app.models import Job, JobState, Video
from app.schemas import JobOut
from app.services.conversion_media import (
    check_conflicting_jobs,
    fingerprint,
    selected_tracks,
    subtitle_catalog,
)
from app.services.operations import ConvertRequest, RenameRequest, local_source, rename_video
from app.services.probe import _run_ffprobe
from app.services.ts_repair import TsRepairRequest, inspect_ts_duration
from app.workers.conversion import export_path
from app.workers.queue import get_pool

router = APIRouter(tags=["video-actions"])


@router.get("/api/videos/{video_id}/ts-repair")
async def inspect_ts_repair(
    video_id: int, source_id: int | None = None, session: AsyncSession = Depends(get_session)
):
    await require_video(session, video_id)
    source, path = await local_source(session, video_id, source_id)
    try:
        inspection = inspect_ts_duration(path, await _run_ffprobe(str(path)))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"source_id": source.id, **inspection.__dict__}


@router.post("/api/videos/{video_id}/ts-repair", response_model=JobOut, status_code=202)
async def repair_ts(
    video_id: int, body: TsRepairRequest, session: AsyncSession = Depends(get_session)
):
    await require_video(session, video_id)
    from app.services.directory_categories import library_write_lock

    conversion_body = ConvertRequest(
        format="ts", source_id=body.source_id, replace_source=body.replace_source
    )
    with library_write_lock():
        source, path = await local_source(session, video_id, body.source_id)
        try:
            inspection = inspect_ts_duration(path, await _run_ffprobe(str(path)))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not inspection.needs_repair:
            raise HTTPException(409, "该 TS 文件的时长信息一致，无需修复")
        await check_conflicting_jobs(session, source, conversion_body)
        job = Job(
            kind="convert_format",
            video_id=video_id,
            state=JobState.queued,
            payload={
                **conversion_body.model_dump(),
                "source_id": source.id,
                "_source_fingerprint": fingerprint(path),
                "_subtitle_versions": {},
                "_ts_repair": True,
                "_duration_before": inspection.__dict__,
            },
            message=(
                "等待无损修复 TS 时长并替换来源"
                if body.replace_source
                else "等待无损修复 TS 时长"
            ),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
    try:
        pool = await asyncio.wait_for(get_pool(), timeout=5)
        queued = await asyncio.wait_for(
            pool.enqueue_job("run_conversion", job_id, _job_id=f"convert-{job_id}"), timeout=5
        )
        if queued is None:
            raise RuntimeError("任务未能入队")
        job.arq_job_id = queued.job_id
        await session.commit()
        await session.refresh(job)
    except Exception as exc:
        from sqlalchemy import update

        await session.rollback()
        await session.execute(
            update(Job).where(Job.id == job_id, Job.state == JobState.queued).values(
                state=JobState.error,
                error="任务队列暂时不可用，请检查 Redis 后重试",
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        raise HTTPException(503, "任务队列暂时不可用，请检查 Redis 后重试") from exc
    return JobOut.model_validate(job)


@router.get("/api/videos/{video_id}/conversion-options")
async def conversion_options(
    video_id: int, source_id: int | None = None, session: AsyncSession = Depends(get_session)
):
    await require_video(session, video_id)
    source, path = await local_source(session, video_id, source_id)
    raw = await _run_ffprobe(str(path))
    return {"source_id": source.id, "subtitles": subtitle_catalog(raw)}


@router.post("/api/videos/{video_id}/rename")
async def rename(
    video_id: int,
    body: RenameRequest,
    session: AsyncSession = Depends(get_session),
):
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")
    from app.services.directory_categories import library_write_lock

    with library_write_lock():
        return await rename_video(session, video, body)


@router.post("/api/videos/{video_id}/convert", response_model=JobOut, status_code=202)
async def convert(
    video_id: int,
    body: ConvertRequest,
    session: AsyncSession = Depends(get_session),
):
    await require_video(session, video_id)
    from app.services.directory_categories import library_write_lock

    with library_write_lock():
        source, path = await local_source(session, video_id, body.source_id)
        await check_conflicting_jobs(session, source, body)
        tracks = await selected_tracks(session, video_id, body)
        if body.replace_source:
            destination = path.with_suffix("." + body.format)
            if destination != path and (destination.exists() or destination.is_symlink()):
                raise HTTPException(409, "目标格式的同名文件已存在，不能替换")
        job = Job(
            kind="convert_format",
            video_id=video_id,
            state=JobState.queued,
            payload={
                **body.model_dump(),
                "source_id": source.id,
                "_source_fingerprint": fingerprint(path),
                "_subtitle_versions": {str(t.id): t.version for t in tracks},
            },
            message=f"等待转换为 {body.format.upper()}",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
    try:
        pool = await asyncio.wait_for(get_pool(), timeout=5)
        queued = await asyncio.wait_for(
            pool.enqueue_job("run_conversion", job_id, _job_id=f"convert-{job_id}"),
            timeout=5,
        )
        if queued is None:
            raise RuntimeError("任务未能入队")
        job.arq_job_id = queued.job_id
        await session.commit()
        await session.refresh(job)
    except Exception as exc:
        # Preserve the record, but never leave a non-enqueued job stuck as 'queued'.
        from sqlalchemy import update

        await session.rollback()
        await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.state == JobState.queued)
            .values(
                state=JobState.error,
                error="任务队列暂时不可用，请检查 Redis 后重试",
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        raise HTTPException(503, "任务队列暂时不可用，请检查 Redis 后重试") from exc
    return JobOut.model_validate(job)


@router.get("/api/jobs/{job_id}/file")
async def download(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if job is None or job.kind != "convert_format":
        raise HTTPException(404, "转换任务不存在")
    if job.state != JobState.done or not job.result:
        raise HTTPException(409, "转换尚未完成，没有可下载的文件")
    try:
        if job.result.get("replaced"):
            _source, path = await local_source(session, job.video_id, job.result["source_id"])
            if fingerprint(path) != job.result["output_fingerprint"]:
                raise HTTPException(410, "该转换结果随后已被修改或再次替换，请查看当前视频")
        else:
            path = export_path(job.id, job.result["format"])
    except (ValueError, KeyError) as exc:
        raise HTTPException(404, "转换文件记录无效") from exc
    if not path.is_file():
        raise HTTPException(410, "转换文件已被移动或清理，请重新转换")
    mime = {
        "mp4": "video/mp4",
        "mov": "video/quicktime",
        "mkv": "video/x-matroska",
        "ts": "video/mp2t",
        "m2ts": "video/mp2t",
        "avi": "video/x-msvideo",
    }[job.result["format"]]
    return FileResponse(
        path,
        media_type=mime,
        filename=job.result["filename"],
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="video-{job.id}.{job.result["format"]}"; '
                f"filename*=UTF-8''{quote(job.result['filename'])}"
            ),
        },
    )
