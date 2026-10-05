"""任务查询 API。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.job_notifications import router as notifications_router
from app.db import get_session
from app.models import Job, JobState
from app.schemas import JobListResponse, JobOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
router.include_router(notifications_router)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    state: JobState | None = None,
    kind: str | None = None,
    video_id: int | None = None,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Job)
    count_stmt = select(func.count(Job.id))
    conds = []
    if state:
        conds.append(Job.state == JobState(state))
    if kind:
        conds.append(Job.kind == kind)
    if video_id is not None:
        conds.append(Job.video_id == video_id)
    for c in conds:
        stmt = stmt.where(c)
        count_stmt = count_stmt.where(c)

    stmt = stmt.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()
    return JobListResponse(items=[JobOut.model_validate(j) for j in rows], total=total)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return JobOut.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.state in (JobState.done, JobState.error, JobState.canceled):
        raise HTTPException(400, f"任务已结束（{job.state.value}），无法取消")

    if job.kind == "move_video" and job.state == JobState.running:
        raise HTTPException(409, "文件已开始移动，需完成文件校验与索引更新；请等待后台处理完成")

    changed = await session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.state.in_(
                [JobState.queued]
                if job.kind == "move_video"
                else [JobState.queued, JobState.running]
            ),
        )
        .values(state=JobState.canceled, message="任务已取消", finished_at=datetime.now(UTC))
    )
    await session.commit()
    if not changed.rowcount:
        detail = (
            "移动已开始或结束，请刷新状态" if job.kind == "move_video" else "任务已结束，请刷新状态"
        )
        raise HTTPException(409, detail)
    await session.refresh(job)
    return JobOut.model_validate(job)
