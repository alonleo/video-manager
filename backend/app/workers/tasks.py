"""arq 后台任务。

并发纪律（计划书 §9）：
- 扫描/转码：max_jobs=2
- 转录：独立队列 max_jobs=1，串行执行 —— CPU 密集且内存占用大（15GB 可用），
  并行会把播放和系统一起拖死
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import update

from app.db import SessionLocal
from app.models import Job, JobState

log = logging.getLogger(__name__)


async def _mark(session, job_db_id: int, **values) -> None:
    await session.execute(
        update(Job)
        .where(Job.id == job_db_id, Job.state.not_in([JobState.canceled, JobState.done]))
        .values(**values)
    )
    await session.commit()


class JobCanceled(Exception):
    pass


async def run_job(ctx: dict, job_db_id: int, kind: str, payload: dict) -> dict:
    """统一任务入口：负责状态流转，具体工作分派给 handler。"""
    async with SessionLocal() as session:
        initial = await session.get(Job, job_db_id)
        if initial is None or initial.state == JobState.canceled:
            return {"canceled": True}
        await _mark(
            session,
            job_db_id,
            state=JobState.running,
            started_at=datetime.now(UTC),
            attempts=(await session.get(Job, job_db_id)).attempts + 1,
        )

    async def progress(done: int, total: int, message: str = "") -> None:
        async with SessionLocal() as s:
            current = await s.get(Job, job_db_id)
            if current is None or current.state == JobState.canceled:
                raise JobCanceled()
            if done is None:
                return
            await _mark(
                s,
                job_db_id,
                progress=(done / total) if total else 0.0,
                message=message or f"{done}/{total}",
            )

    try:
        if kind == "download_url":
            from app.services.downloads import download

            result = await download(payload, job_db_id, progress)
        else:
            result = await _dispatch(kind, payload, progress)
    except (JobCanceled, asyncio.CancelledError):
        async with SessionLocal() as s:
            await _mark(s, job_db_id, state=JobState.canceled, finished_at=datetime.now(UTC))
        return {"canceled": True}
    except Exception as e:
        log.exception("任务失败 job=%s kind=%s", job_db_id, kind)
        async with SessionLocal() as s:
            await _mark(
                s,
                job_db_id,
                state=JobState.error,
                error=str(e)[:2000],
                finished_at=datetime.now(UTC),
            )
        raise

    async with SessionLocal() as s:
        await _mark(
            s,
            job_db_id,
            state=JobState.done,
            progress=1.0,
            result=result,
            finished_at=datetime.now(UTC),
        )
    return result


async def _dispatch(kind: str, payload: dict, progress) -> dict:
    if kind in {"subtitle_generate", "subtitle_translate", "subtitle_model_download"}:
        from app.services.subtitles import dispatch

        return await dispatch(kind, payload, progress)

    if kind == "scan_library":
        from app.services.library import scan_storage

        async with SessionLocal() as session:
            stats = await scan_storage(
                session,
                payload["storage_id"],
                sub_path=payload.get("sub_path", ""),
                do_probe=payload.get("do_probe", True),
                progress_cb=progress,
            )
            return stats.as_dict()

    raise ValueError(f"未知任务类型: {kind}")


async def scan_library(ctx: dict, storage_id: int, sub_path: str = "", do_probe: bool = True):
    """供直接调用（不经 jobs 表）的便捷入口。"""
    from app.services.library import scan_storage

    async with SessionLocal() as session:
        stats = await scan_storage(session, storage_id, sub_path=sub_path, do_probe=do_probe)
        return stats.as_dict()
