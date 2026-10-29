"""Versioned, playable checkpoints for long subtitle jobs; no empty tracks."""

import asyncio
from weakref import WeakValueDictionary

from app.models import Job, JobState, SubtitleTrack
from app.services.subtitle_text import validate_cues

_job_locks = WeakValueDictionary()

PARTIAL_SUFFIX = " · 部分字幕"


def is_partial(track):
    return track.label.endswith(PARTIAL_SUFFIX)


async def building(session, track_id):
    from sqlalchemy import select, or_
    return await session.scalar(select(Job.id).where(
        Job.kind.in_(["subtitle_generate", "subtitle_translate"]),
        Job.state.in_([JobState.queued, JobState.running]),
        or_(Job.result["track_id"].as_integer() == track_id,
            Job.result["source_track_id"].as_integer() == track_id),
    ).limit(1)) is not None


class ProgressiveTrack:
    def __init__(self, session_factory, video_id, label, language, engine, model, *, source_id=None, job_id=None, result_key="track_id"):
        self.sessions = session_factory
        self.video_id, self.label, self.language = video_id, label, language
        self.engine, self.model, self.source_id, self.job_id = engine, model, source_id, job_id
        self.result_key = result_key
        self.track_id = None
        self.version = None
        self.lock = (_job_locks.setdefault((id(session_factory), job_id), asyncio.Lock())
                     if job_id else asyncio.Lock())

    async def publish(self, cues, source_cues=None, *, language=None, final=False):
        if not cues:
            return None
        cues = validate_cues(cues)
        async with self.lock:
            async with self.sessions() as session:
                job = await session.get(Job, self.job_id) if self.job_id else None
                if self.job_id and (job is None or job.state == JobState.canceled):
                    raise asyncio.CancelledError()
                if self.track_id is None:
                    track = SubtitleTrack(video_id=self.video_id, engine=self.engine, model=self.model,
                                          source_track_id=self.source_id, cues=cues, version=1)
                    session.add(track)
                else:
                    track = await session.get(SubtitleTrack, self.track_id)
                    if track is None or track.version != self.version:
                        raise ValueError("正在处理的字幕已被删除或修改，请重新发起任务")
                    track.version += 1
                    track.cues = cues
                track.label = self.label + ("" if final else PARTIAL_SUFFIX)
                track.language = language or self.language
                track.source_cues = source_cues
                await session.flush()
                result = {"track_id": track.id, "cue_count": len(cues)}
                if job:
                    if self.result_key == "source_track_id":
                        previous = job.result or {}
                        job.result = {**previous, "source_track_id": track.id,
                                      "track_id": previous.get("track_id", track.id),
                                      "generated_cue_count": len(cues), "source_partial": not final}
                    else:
                        job.result = {**(job.result or {}), **result, "partial": not final}
                await session.commit()
                self.track_id, self.version = track.id, track.version
                return result
