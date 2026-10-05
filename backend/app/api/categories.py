"""Directory classification and durable background file moves."""

import asyncio
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Job, JobState
from app.schemas import JobOut
from app.services.directory_categories import (
    CreateCategoryRequest,
    MoveVideoRequest,
    _fingerprint,
    create_category,
    library_write_lock,
    list_categories,
    prepare_move,
)
from app.workers.queue import get_pool

router = APIRouter(tags=["directory-categories"])


@router.get("/api/categories")
async def categories(session: AsyncSession = Depends(get_session), media_type: Literal["video", "photo"] = "video"):
    return await list_categories(session, media_type=media_type)


@router.post("/api/categories", status_code=201)
async def create(body: CreateCategoryRequest, session: AsyncSession = Depends(get_session)):
    return await create_category(session, body)


@router.post("/api/videos/{video_id}/move", response_model=JobOut, status_code=202)
async def move(video_id: int, body: MoveVideoRequest, session: AsyncSession = Depends(get_session)):
    # A short independent lock allows more videos to queue while a large copy runs.
    with library_write_lock(".move-submission.lock"):
        active = (
            await session.scalars(
                select(Job).where(
                    Job.kind == "move_video",
                    Job.video_id == video_id,
                    Job.state.in_([JobState.queued, JobState.running]),
                )
            )
        ).all()
        for job in active:
            if job.payload.get("options") == body.model_dump():
                return JobOut.model_validate(job)
        source, original, destination, storage, _, _ = await prepare_move(session, video_id, body)
        job = Job(
            kind="move_video",
            video_id=video_id,
            state=JobState.queued,
            message="等待后台移动，可离开当前页面",
            payload={
                "options": body.model_dump(),
                "_original": str(original),
                "_destination": str(destination),
                "_fingerprint": list(_fingerprint(original)),
                "filename": original.name,
                "target_name": body.path or storage.name,
            },
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    try:
        pool = await asyncio.wait_for(get_pool(), 5)
        queued = await asyncio.wait_for(
            pool.enqueue_job(
                "run_move",
                job_id,
                _job_id=f"move-{job_id}",
                _queue_name="arq:file-moves",
            ),
            5,
        )
        if queued is None:
            raise RuntimeError("任务未能入队")
        await session.execute(update(Job).where(Job.id == job_id).values(arq_job_id=queued.job_id))
        await session.commit()
        await session.refresh(job)
    except Exception as exc:
        await session.rollback()
        await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.state == JobState.queued,
            )
            .values(
                state=JobState.error,
                error="移动任务队列不可用，请重试",
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        raise HTTPException(503, "移动任务队列不可用，请重试") from exc
    return JobOut.model_validate(job)
