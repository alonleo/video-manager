"""任务入队封装。

设计取舍：入队失败不应让 API 直接 500 —— Redis 可能没启动，而用户
只是想先扫个库。因此 enqueue_job 在队列不可用时返回 None，并在数据库
里留下一条 error 记录，用户可以重新发起。
"""

from __future__ import annotations

import asyncio
import logging

from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings

log = logging.getLogger(__name__)

_pool = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_job(kind: str, **kwargs) -> int | None:
    """把任务写入 jobs 表并入队 arq。返回 jobs.id；队列不可用返回 None。"""
    from app.db import SessionLocal
    from app.models import Job, JobState

    # 先落库，保证任务有据可查（即使入队失败也能看到）
    async with SessionLocal() as session:
        job = Job(
            kind=kind,
            video_id=kwargs.get("video_id"),
            state=JobState.queued,
            payload={k: v for k, v in kwargs.items() if k != "video_id"},
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_db_id = job.id

    try:
        pool = await asyncio.wait_for(get_pool(), timeout=5)
        arq_job = await asyncio.wait_for(
            pool.enqueue_job("run_job", job_db_id, kind, kwargs), timeout=5
        )
        if arq_job is None:
            raise RuntimeError("任务未能入队")
        if arq_job is not None:
            async with SessionLocal() as session:
                j = await session.get(Job, job_db_id)
                if j is not None:
                    j.arq_job_id = arq_job.job_id
                    await session.commit()
    except Exception as e:
        log.warning("任务入队失败（Redis 不可用？）job=%s kind=%s: %s", job_db_id, kind, e)
        async with SessionLocal() as session:
            job = await session.get(Job, job_db_id)
            if job:
                job.state = JobState.error
                job.error = "任务队列不可用，请启动 Redis 和 worker 后重新发起"
                await session.commit()
        return None

    return job_db_id
