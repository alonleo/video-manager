"""Batch subtitle readiness and current work, without loading subtitle text or job payloads."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobState, SubtitleTrack, Video

StatusState = Literal[
    "idle", "imported", "queued", "running", "done", "error", "canceled", "missing"
]


class SubtitleTaskStatus(BaseModel):
    state: StatusState = "idle"
    job_id: int | None = None
    progress: float = 0
    active_count: int = 0
    message: str | None = None
    error: str | None = None
    target_language: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class VideoSubtitleStatus(BaseModel):
    video_id: int
    generation: SubtitleTaskStatus = Field(default_factory=SubtitleTaskStatus)
    translation: SubtitleTaskStatus = Field(default_factory=SubtitleTaskStatus)
    generated_count: int = 0
    imported_count: int = 0
    translated_count: int = 0
    original_languages: list[str] = Field(default_factory=list)
    translated_languages: list[str] = Field(default_factory=list)


class SubtitleStatusBatch(BaseModel):
    items: list[VideoSubtitleStatus]
    missing_ids: list[int]


async def read_subtitle_status(
    session: AsyncSession,
    video_ids: list[int],
) -> SubtitleStatusBatch:
    ids = list(dict.fromkeys(video_ids))
    existing = set(await session.scalars(select(Video.id).where(Video.id.in_(ids))))
    summaries = {vid: VideoSubtitleStatus(video_id=vid) for vid in ids if vid in existing}
    if not summaries:
        return SubtitleStatusBatch(items=[], missing_ids=ids)

    # A translation keeps a source snapshot even after its original track is deleted.
    translated = or_(
        SubtitleTrack.source_track_id.is_not(None),
        SubtitleTrack.source_cues[0].as_string().is_not(None),
    )
    tracks = await session.execute(
        select(
            SubtitleTrack.video_id,
            SubtitleTrack.id,
            SubtitleTrack.engine,
            SubtitleTrack.language,
            translated.label("translated"),
        ).where(SubtitleTrack.video_id.in_(existing))
    )
    track_ids: dict[tuple[int, str], set[int]] = {}
    for row in tracks:
        item = summaries[row.video_id]
        kind = "translation" if row.translated else "generation"
        track_ids.setdefault((row.video_id, kind), set()).add(row.id)
        if row.translated:
            item.translated_count += 1
            item.translated_languages.append(row.language)
        else:
            if row.engine == "import":
                item.imported_count += 1
            else:
                item.generated_count += 1
            item.original_languages.append(row.language)

    for item in summaries.values():
        item.original_languages = sorted(set(item.original_languages))
        item.translated_languages = sorted(set(item.translated_languages))
        if item.generated_count:
            item.generation.state = "done"
        elif item.imported_count:
            item.generation.state = "imported"
        if item.translated_count:
            item.translation.state = "done"

    # Select one representative per video/kind: running > queued > latest terminal job.
    # Windowing prevents a global "latest 50 jobs" cap from hiding older active tasks.
    active = Job.state.in_([JobState.queued, JobState.running])
    partition = [Job.video_id, Job.kind]
    ranked = (
        select(
            Job.video_id,
            Job.kind,
            Job.id,
            Job.state,
            Job.progress,
            Job.message,
            Job.error,
            Job.created_at,
            Job.started_at,
            Job.finished_at,
            Job.result["track_id"].as_integer().label("track_id"),
            Job.payload["target_language"].as_string().label("target_language"),
            func.sum(case((active, 1), else_=0)).over(partition_by=partition).label("active_count"),
            func.row_number()
            .over(
                partition_by=partition,
                order_by=[
                    case(
                        (Job.state == JobState.running, 0),
                        (Job.state == JobState.queued, 1),
                        else_=2,
                    ),
                    Job.created_at.desc(),
                    Job.id.desc(),
                ],
            )
            .label("rank"),
        )
        .where(
            Job.video_id.in_(existing),
            Job.kind.in_(["subtitle_generate", "subtitle_translate"]),
        )
        .subquery()
    )
    jobs = (await session.execute(select(ranked).where(ranked.c.rank == 1))).mappings()
    for row in jobs:
        item = summaries[row["video_id"]]
        kind = "generation" if row["kind"] == "subtitle_generate" else "translation"
        status = getattr(item, kind)
        state = row["state"].value
        if state == "done" and row["track_id"] not in track_ids.get((item.video_id, kind), set()):
            # Older retained tracks remain visible via counts, but this result is gone.
            state = "missing"
        progress = row["progress"] or 0
        if not math.isfinite(progress):
            progress = 0
        status.state = state
        status.job_id = row["id"]
        status.progress = max(0, min(1, progress))
        status.active_count = row["active_count"]
        status.message = row["message"]
        status.error = row["error"] if state == "error" else None
        status.target_language = row["target_language"] if kind == "translation" else None
        status.created_at = row["created_at"].isoformat() if row["created_at"] else None
        status.started_at = row["started_at"].isoformat() if row["started_at"] else None
        status.finished_at = row["finished_at"].isoformat() if row["finished_at"] else None
    return SubtitleStatusBatch(
        items=list(summaries.values()),
        missing_ids=[vid for vid in ids if vid not in existing],
    )
