"""Durable file-move queue. Browser lifetime never owns a filesystem operation."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Job, JobState
from app.services.directory_categories import MoveVideoRequest, library_write_lock, move_video

log = logging.getLogger(__name__)
QUEUE = "arq:file-moves"


async def run_move(ctx: dict, job_id: int):
    # Once claimed, let file publication and index reconciliation finish on graceful
    # shutdown. Canceling an asyncio.to_thread await does not stop its filesystem work.
    task = asyncio.create_task(_run(job_id))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        return await task


async def _run(job_id: int):
    while True:
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if job is None or job.state != JobState.queued:
                return
        try:
            with library_write_lock():
                return await _perform(job_id)
        except HTTPException as exc:
            if exc.detail != "目录正在扫描或整理，请稍后重试":
                raise
        await asyncio.sleep(1)


async def _perform(job_id: int):
    async with SessionLocal() as session:
        changed = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.state == JobState.queued,
            )
            .values(
                state=JobState.running,
                started_at=datetime.now(UTC),
                attempts=Job.attempts + 1,
                message="正在检查文件与目标目录",
            )
        )
        await session.commit()
        if not changed.rowcount:
            return
        job = await session.get(Job, job_id)
        payload, video_id = job.payload, job.video_id

    latest = (0.01, "正在检查文件与目标目录")

    def progress(fraction, message):
        nonlocal latest
        latest = (fraction, message)

    async def publish():
        previous = None
        while True:
            await asyncio.sleep(0.4)
            if latest == previous:
                continue
            try:
                async with SessionLocal() as s:
                    await s.execute(
                        update(Job)
                        .where(
                            Job.id == job_id,
                            Job.state == JobState.running,
                            Job.progress < 0.98,
                        )
                        .values(progress=latest[0], message=latest[1])
                    )
                    await s.commit()
                previous = latest
            except Exception:
                log.warning("移动进度暂时无法保存 job=%s", job_id, exc_info=True)

    reporter = asyncio.create_task(publish())
    try:
        async with SessionLocal() as s:
            return await move_video(
                s,
                video_id,
                MoveVideoRequest.model_validate(payload["options"]),
                job_id=job_id,
                expected=payload,
                progress=progress,
                locked=True,
            )
    except Exception as exc:
        error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        log.exception("移动任务失败 job=%s", job_id)
        async with SessionLocal() as s:
            await s.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.state == JobState.running,
                )
                .values(
                    state=JobState.error,
                    error=error[:2000],
                    message="移动未完成，请查看详情",
                    finished_at=datetime.now(UTC),
                )
            )
            await s.commit()
    finally:
        reporter.cancel()
        await asyncio.gather(reporter, return_exceptions=True)


async def startup(ctx):
    """Requeue persisted requests and expose interrupted work for review, never hide it."""
    # A process-wide lock prevents two move workers and unsafe recovery of a live job.
    ctx["move_worker_lock"] = library_write_lock(".move-worker.lock")
    ctx["move_worker_lock"].__enter__()
    async with SessionLocal() as s:
        interrupted = list(
            (
                await s.scalars(
                    select(Job).where(
                        Job.kind == "move_video",
                        Job.state == JobState.running,
                    )
                )
            ).all()
        )
        for job in interrupted:
            if job.result and job.result.get("local_path"):
                # Index/result were committed together. Keep any remaining original
                # after an abnormal shutdown; never guess which file can be removed.
                warning = "移动和索引已保存；上次服务中断，请核对来源目录是否仍有原文件"
                job.result = {**job.result, "warning": warning}
                job.state, job.progress, job.message = JobState.done, 1.0, warning
            else:
                job.state = JobState.error
                job.error = (
                    "移动服务意外中断，请核对来源与目标文件后重新提交；系统未自动删除任何恢复文件"
                )
                job.message = "移动中断，需要核对文件"
            job.finished_at = datetime.now(UTC)
        await s.commit()
        queued = list(
            (
                await s.scalars(
                    select(Job.id)
                    .where(
                        Job.kind == "move_video",
                        Job.state == JobState.queued,
                    )
                    .order_by(Job.id)
                )
            ).all()
        )
    for job_id in queued:
        await ctx["redis"].enqueue_job(
            "run_move", job_id, _job_id=f"move-{job_id}", _queue_name=QUEUE
        )


async def shutdown(ctx):
    lock = ctx.pop("move_worker_lock", None)
    if lock:
        lock.__exit__(None, None, None)
