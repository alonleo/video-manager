"""Read-only task updates, without a time-based cursor or a recent-job limit."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Job, JobState
from app.schemas import JobOut

router = APIRouter()
ACTIVE = (JobState.queued, JobState.running)
BATCH_SIZE = 200


class NotificationCursor(BaseModel):
    cursor: int | None = Field(default=None, ge=0)
    pending: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=10000)


class NotificationBatch(BaseModel):
    cursor: int
    pending: list[int]
    items: list[JobOut]
    has_more: bool = False


@router.post("/notifications", response_model=NotificationBatch)
async def poll_notifications(
    body: NotificationCursor,
    session: AsyncSession = Depends(get_session),
):
    # Bootstrap silently. Keep all running tasks, including ones older than the
    # task centre's latest 100 records. A restored/replaced DB resets the cursor.
    latest = (await session.execute(select(func.max(Job.id)))).scalar_one() or 0
    if body.cursor is None or body.cursor > latest:
        pending = sorted(
            (
                await session.scalars(select(Job.id).where(Job.id <= latest, Job.state.in_(ACTIVE)))
            ).all()
        )
        return NotificationBatch(cursor=latest, pending=pending, items=[])

    # New jobs are read by monotonically increasing ID. This catches a job that
    # starts AND finishes between two polls, even when finished_at is missing.
    fresh = list(
        (
            await session.scalars(
                select(Job).where(Job.id > body.cursor).order_by(Job.id).limit(BATCH_SIZE + 1)
            )
        ).all()
    )
    has_more = len(fresh) > BATCH_SIZE
    fresh = fresh[:BATCH_SIZE]
    watched = []
    # Chunk IN queries to remain within SQLite's parameter limit.
    ids = sorted({i for i in body.pending if i <= body.cursor})
    for start in range(0, len(ids), 500):
        watched.extend(
            (await session.scalars(select(Job).where(Job.id.in_(ids[start : start + 500])))).all()
        )
    rows = watched + fresh
    return NotificationBatch(
        cursor=fresh[-1].id if fresh else body.cursor,
        pending=sorted(j.id for j in rows if j.state in ACTIVE),
        items=[
            JobOut.model_validate(j) for j in rows if j.state in (JobState.done, JobState.error)
        ],
        has_more=has_more,
    )
