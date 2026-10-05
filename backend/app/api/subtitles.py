"""Subtitle workspace API, provider settings and local model catalog."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, PositiveInt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.photos import require_video
from app.db import get_session
from app.models import Job, JobState, SubtitleTrack
from app.services import subtitle_config as config_svc
from app.services import subtitle_translation_local as local_translation
from app.services.subtitle_config import SubtitleConfig
from app.services.subtitle_options import SubtitleTaskOptions
from app.services.subtitle_status import SubtitleStatusBatch, read_subtitle_status
from app.services.subtitle_text import (
    Cue,
    bilingual_cues,
    export_subtitles,
    parse_subtitles,
    validate_cues,
)
from app.workers.queue import enqueue_job


async def same_origin(request: Request):
    # JSON writes + Origin validation prevent arbitrary websites changing local AI settings.
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/"), "http://127.0.0.1:5173", "http://localhost:5173"}
    if request.method != "GET" and origin and origin not in allowed:
        raise HTTPException(403, "拒绝来自其他网站的修改请求")


router = APIRouter(prefix="/api", tags=["subtitles"], dependencies=[Depends(same_origin)])
ENQUEUE_LOCK = asyncio.Lock()


class ImportBody(BaseModel):
    label: str = Field(default="导入字幕", min_length=1, max_length=120)
    language: str = Field(default="und", pattern=r"^[a-zA-Z][a-zA-Z0-9-]{0,19}$")
    content: str = Field(min_length=1, max_length=3_000_000)


class EditBody(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    cues: list[Cue] = Field(min_length=1, max_length=30000)
    version: int


class GenerateBody(BaseModel):
    provider: Literal["local", "api"]
    model: str = Field(min_length=1, max_length=150)
    language: str = Field(default="auto", pattern=r"^(auto|[a-z]{2,3})$")


class TranslateBody(BaseModel):
    provider: Literal["local", "ollama", "api"]
    source_language: str = Field(default="auto", pattern=r"^[a-zA-Z][a-zA-Z0-9-]{0,19}$")
    model: str = Field(min_length=1, max_length=150)
    target_language: str = Field(default="zh-CN", pattern=r"^[a-zA-Z][a-zA-Z0-9-]{0,19}$")


class DownloadBody(BaseModel):
    engine: Literal["whisper", "ollama", "translation"]
    model: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_./:\-]{0,149}$")


async def get_track(session, track_id):
    track = await session.get(SubtitleTrack, track_id)
    if not track:
        raise HTTPException(404, "字幕不存在")
    return track


def track_out(track, detail=False):
    data = {
        name: getattr(track, name)
        for name in (
            "id",
            "video_id",
            "label",
            "language",
            "engine",
            "model",
            "source_track_id",
            "version",
            "created_at",
        )
    }
    data.update(cue_count=len(track.cues), bilingual=bool(track.source_cues))
    if detail:
        data["cues"] = track.cues
    return data


async def enqueue_unique(kind, payload, session):
    async with ENQUEUE_LOCK:
        pending = (
            await session.scalars(
                select(Job).where(
                    Job.kind == kind, Job.state.in_([JobState.queued, JobState.running])
                )
            )
        ).all()
        if any(
            j.video_id == payload.get("video_id")
            and j.payload == {k: v for k, v in payload.items() if k != "video_id"}
            for j in pending
        ):
            raise HTTPException(409, "相同任务已在处理，请等待完成或先取消")
        job_id = await enqueue_job(kind, **payload)
    if job_id is None:
        raise HTTPException(503, "任务队列不可用，请启动 Redis 和 worker 后重试")
    return {"job_id": job_id}


@router.get("/subtitle-settings")
async def settings_get(session: AsyncSession = Depends(get_session)):
    return config_svc.public_config(await config_svc.read_config(session))


@router.put("/subtitle-settings")
async def settings_put(body: SubtitleConfig, session: AsyncSession = Depends(get_session)):
    try:
        return await config_svc.save_config(session, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/subtitle-models")
async def models_get(session: AsyncSession = Depends(get_session)):
    return await config_svc.catalog(await config_svc.read_config(session))


@router.post("/subtitle-models/download")
async def models_download(body: DownloadBody, session: AsyncSession = Depends(get_session)):
    if body.engine == "whisper":
        if body.model not in config_svc.WHISPER_MODELS:
            raise HTTPException(422, "未知 Whisper 模型")
        if config_svc.importlib.util.find_spec("faster_whisper") is None:
            raise HTTPException(409, "请安装本地语音依赖：cd backend && uv sync --extra subtitles")
    if body.engine == "translation":
        if body.model != local_translation.MODEL_ID:
            raise HTTPException(422, "未知本地翻译模型")
        if not local_translation.runtime_ready():
            raise HTTPException(409, "请安装本地翻译依赖：cd backend && uv sync --extra subtitles")
    return await enqueue_unique("subtitle_model_download", body.model_dump(), session)


@router.post("/subtitle-settings/test/{provider}")
async def provider_test(
    provider: Literal["asr_api", "translation_api", "ollama"],
    session: AsyncSession = Depends(get_session),
):
    config = await config_svc.read_config(session)
    try:
        if provider == "ollama":
            result = await config_svc.api_request("GET", config.ollama_url + "/api/tags")
            models = [m["name"] for m in result.get("models", [])]
        else:
            item = getattr(config, provider)
            result = await config_svc.api_request(
                "GET", item.base_url + "/models", headers=config_svc.auth(item)
            )
            models = [m["id"] for m in result.get("data", []) if "id" in m]
        return {
            "ok": True,
            "models": models,
            "message": "连接成功；这是模型列表检测，实际任务还需所选模型支持相应接口",
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/subtitle-status", response_model=SubtitleStatusBatch)
async def subtitle_status(
    video_ids: list[PositiveInt] = Query(..., min_length=1, max_length=100),
    session: AsyncSession = Depends(get_session),
):
    return await read_subtitle_status(session, video_ids)


@router.get("/videos/{video_id}/subtitles")
async def list_tracks(video_id: int, session: AsyncSession = Depends(get_session)):
    await require_video(session, video_id)
    tracks = (
        await session.scalars(
            select(SubtitleTrack)
            .where(SubtitleTrack.video_id == video_id)
            .order_by(SubtitleTrack.id.desc())
        )
    ).all()
    return [track_out(t) for t in tracks]


@router.post("/videos/{video_id}/subtitles/import", status_code=201)
async def import_track(
    video_id: int, body: ImportBody, session: AsyncSession = Depends(get_session)
):
    await require_video(session, video_id)
    try:
        cues = parse_subtitles(body.content)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    track = SubtitleTrack(
        video_id=video_id,
        label=body.label,
        language=body.language,
        engine="import",
        model=None,
        cues=cues,
    )
    session.add(track)
    await session.commit()
    return track_out(track, True)


@router.post("/videos/{video_id}/subtitles/generate", status_code=202)
async def generate_track(
    video_id: int, body: GenerateBody, session: AsyncSession = Depends(get_session)
):
    await require_video(session, video_id)
    if body.provider == "local":
        if body.model not in config_svc.WHISPER_MODELS:
            raise HTTPException(422, "未知 Whisper 模型")
        if not config_svc.model_ready(body.model):
            raise HTTPException(409, "请先到 AI 设置下载所选模型")
    return await enqueue_unique(
        "subtitle_generate", {"video_id": video_id, **body.model_dump()}, session
    )


@router.get("/subtitles/{track_id}")
async def detail_track(track_id: int, session: AsyncSession = Depends(get_session)):
    return track_out(await get_track(session, track_id), True)


@router.put("/subtitles/{track_id}")
async def edit_track(track_id: int, body: EditBody, session: AsyncSession = Depends(get_session)):
    track = await get_track(session, track_id)
    if track.version != body.version:
        raise HTTPException(409, "字幕已在其他页面修改，请重新载入后编辑")
    changed = await session.execute(
        update(SubtitleTrack)
        .where(SubtitleTrack.id == track_id, SubtitleTrack.version == body.version)
        .values(
            cues=validate_cues([c.model_dump() for c in body.cues]),
            label=body.label,
            version=body.version + 1,
        )
    )
    if not changed.rowcount:
        await session.rollback()
        raise HTTPException(409, "字幕已在其他页面修改，请重新载入后编辑")
    await session.commit()
    await session.refresh(track)
    return track_out(track, True)


@router.delete("/subtitles/{track_id}", status_code=204)
async def delete_track(track_id: int, session: AsyncSession = Depends(get_session)):
    await session.delete(await get_track(session, track_id))
    await session.commit()


@router.post("/subtitles/{track_id}/translate", status_code=202)
async def translate_track(
    track_id: int, body: TranslateBody, session: AsyncSession = Depends(get_session)
):
    track = await get_track(session, track_id)
    if body.provider == "local":
        try:
            local_translation.language_code(
                track.language if body.source_language == "auto" else body.source_language
            )
            local_translation.language_code(body.target_language)
            ready = local_translation.model_ready(body.model)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if not local_translation.runtime_ready():
            raise HTTPException(409, "请先安装本地翻译依赖")
        if not ready:
            raise HTTPException(409, "请先到 AI 设置下载所选本地翻译模型")
    return await enqueue_unique(
        "subtitle_translate",
        {
            "video_id": track.video_id,
            "track_id": track_id,
            "source_version": track.version,
            **body.model_dump(),
        },
        session,
    )


@router.get("/subtitles/{track_id}/export")
async def export_track(
    track_id: int,
    format: Literal["srt", "vtt"] = "vtt",
    bilingual: bool = False,
    download: bool = False,
    session: AsyncSession = Depends(get_session),
):
    track = await get_track(session, track_id)
    cues = (
        bilingual_cues(track.cues, track.source_cues)
        if bilingual and track.source_cues
        else track.cues
    )
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="subtitles-{track.id}.{format}"'
    return Response(
        export_subtitles(cues, format),
        media_type="text/vtt" if format == "vtt" else "application/x-subrip",
        headers=headers,
    )


# Read current saved parameters on the server at click time. Credentials stay server-side.
@router.get("/videos/{video_id}/subtitle-options")
async def options_get(video_id: int, session: AsyncSession = Depends(get_session)):
    from app.services.subtitle_options import read_options
    await require_video(session, video_id)
    return await read_options(session, video_id)



@router.put("/videos/{video_id}/subtitle-options")
async def options_put(
    video_id: int, body: SubtitleTaskOptions, session: AsyncSession = Depends(get_session)
):
    from app.services.subtitle_options import save_options
    await require_video(session, video_id)
    return await save_options(session, video_id, body)


@router.post("/videos/{video_id}/subtitles/generate-saved", status_code=202)
async def generate_saved(video_id: int, session: AsyncSession = Depends(get_session)):
    from app.services.subtitle_options import generate_parameters, read_options
    await require_video(session, video_id)
    config = await config_svc.read_config(session)
    options = await read_options(session, video_id)
    return await generate_track(video_id, GenerateBody(**generate_parameters(config, options)), session)


@router.post("/subtitles/{track_id}/translate-saved", status_code=202)
async def translate_saved(track_id: int, session: AsyncSession = Depends(get_session)):
    from app.services.subtitle_options import read_options, translation_parameters
    track = await get_track(session, track_id)
    config = await config_svc.read_config(session)
    options = await read_options(session, track.video_id)
    return await translate_track(track_id, TranslateBody(**translation_parameters(config, options)), session)
