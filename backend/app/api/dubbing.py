"""Dubbing settings, editable scripts, previews and background exports."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, PositiveInt, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.api.subtitles import ENQUEUE_LOCK, enqueue_unique, same_origin
from app.config import settings
from app.db import get_session
from app.models import DubbingProject, Job, JobState, SubtitleTrack
from app.schemas import JobOut
from app.services import dubbing
from app.services import model_uninstall
from app.services import dubbing_config as cfg
from app.services import dubbing_local as local
from app.services import dubbing_references as refs
from app.services import subtitle_config as asr_cfg
from app.services.photos import require_video
from app.services.probe import _run_ffprobe, parse_probe_json

router = APIRouter(prefix="/api", tags=["dubbing"], dependencies=[Depends(same_origin)])
PREVIEW_LIMIT = asyncio.Semaphore(2)


class PrepareBody(BaseModel):
    target_language: str = "zh-CN"
    track_id: PositiveInt | None = None
    voice_mode: Literal["auto", "male", "female"] = "auto"

    @field_validator("target_language")
    @classmethod
    def language(cls, value):
        return cfg.DubbingConfig.language(value)


class EditBody(BaseModel):
    version: PositiveInt
    cues: list[dubbing.DubCue] = Field(min_length=1, max_length=10000)


class RenderBody(BaseModel):
    version: PositiveInt
    male_voice: str = Field(default="", max_length=150)
    female_voice: str = Field(default="", max_length=150)
    keep_original: bool = True


class PreviewBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    gender: Literal["male", "female"]
    male_voice: str = Field(default="", max_length=150)
    female_voice: str = Field(default="", max_length=150)


async def project_get(session, project_id):
    project = await session.get(DubbingProject, project_id)
    if not project:
        raise HTTPException(404, "译配稿不存在")
    return project


def project_out(project, detail=True):
    result = {k: getattr(project, k) for k in ("id", "video_id", "language", "version", "created_at")}
    result["cue_count"] = len(project.cues)
    if detail:
        result["cues"] = project.cues
    return result


@router.get("/dubbing/settings")
async def settings_get(session: AsyncSession = Depends(get_session)):
    return cfg.public_config(await cfg.read_config(session))


@router.put("/dubbing/settings")
async def settings_put(body: cfg.DubbingConfig, session: AsyncSession = Depends(get_session)):
    try:
        async with refs.LOCK:
            return await cfg.save_config(session, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/dubbing/languages")
async def languages():
    return [{"id": key, "name": value} for key, value in cfg.LANGUAGES.items()]


@router.get("/dubbing/voices")
async def voices(language: str = "zh-CN", session: AsyncSession = Depends(get_session)):
    try:
        return await cfg.voices(await cfg.read_config(session), language)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/videos/{video_id}/dubbing")
async def projects(video_id: int, session: AsyncSession = Depends(get_session)):
    await require_video(session, video_id)
    rows = await session.scalars(select(DubbingProject).where(
        DubbingProject.video_id == video_id).order_by(DubbingProject.id.desc()).limit(100))
    return [project_out(p, False) for p in rows]


@router.post("/videos/{video_id}/dubbing/prepare", status_code=202)
async def prepare(video_id: int, body: PrepareBody, session: AsyncSession = Depends(get_session)):
    await require_video(session, video_id)
    try:
        cfg.validate_reference_language(await cfg.read_config(session), body.target_language)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.track_id:
        track = await session.get(SubtitleTrack, body.track_id)
        if not track or track.video_id != video_id:
            raise HTTPException(422, "请选择当前视频的字幕")
    await dubbing.checked_source(session, video_id)
    return await enqueue_unique("dub_prepare", {"video_id": video_id, **body.model_dump()}, session)


@router.get("/dubbing/projects/{project_id}")
async def detail(project_id: int, session: AsyncSession = Depends(get_session)):
    return project_out(await project_get(session, project_id))


@router.put("/dubbing/projects/{project_id}")
async def edit(project_id: int, body: EditBody, session: AsyncSession = Depends(get_session)):
    project = await project_get(session, project_id)
    if project.version != body.version:
        raise HTTPException(409, "译配稿已更新，请重新载入")
    # Text and voice assignment can change; timing stays tied to the source segments.
    if len(body.cues) != len(project.cues) or any(
        (new.start_ms, new.end_ms) != (old["start_ms"], old["end_ms"])
        for new, old in zip(body.cues, project.cues, strict=True)
    ):
        raise HTTPException(422, "请保留原时间轴和句子数量，仅修改译文与声线")
    result = await session.execute(update(DubbingProject).where(
        DubbingProject.id == project_id, DubbingProject.version == body.version
    ).values(cues=[c.model_dump() for c in body.cues], version=body.version + 1))
    if not result.rowcount:
        await session.rollback()
        raise HTTPException(409, "译配稿已更新，请重新载入")
    await session.commit()
    await session.refresh(project)
    return project_out(project)


@router.post("/dubbing/projects/{project_id}/render", status_code=202)
async def render(project_id: int, body: RenderBody, session: AsyncSession = Depends(get_session)):
    project = await project_get(session, project_id)
    if project.version != body.version:
        raise HTTPException(409, "请保存或重新载入最新译配稿")
    if project.source_id is None:
        raise HTTPException(409, "原视频来源已移除，请重新准备译配稿")
    try:
        await dubbing.checked_source(session, project.video_id, project.source_id,
                                     project.source_fingerprint)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    try:
        config = await cfg.read_config(session)
        if config.provider in {"reference", "local"}:
            await cfg.resolve_voices(config, project.language, body.male_voice, body.female_voice,
                                     {cue["gender"] for cue in project.cues})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return await enqueue_unique("dub_render", {
        "video_id": project.video_id, "project_id": project.id, **body.model_dump()
    }, session)


@router.post("/dubbing/projects/{project_id}/preview")
async def preview(project_id: int, body: PreviewBody, session: AsyncSession = Depends(get_session)):
    project = await project_get(session, project_id)
    config = await cfg.read_config(session)
    # End the read transaction before waiting on a network provider.
    language = project.language
    await session.rollback()
    try:
        async with PREVIEW_LIMIT:
            voices = await cfg.resolve_voices(config, language, body.male_voice, body.female_voice,
                                              (body.gender,))
            settings.work_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="dub-preview-", dir=settings.work_dir) as work:
                path = Path(work) / "preview.mp3"
                await cfg.synthesize(config.model_copy(update={"target_language": language}), body.text, voices[body.gender], path)
                if not parse_probe_json(await _run_ffprobe(str(path))).audio_codec:
                    raise ValueError("服务未返回可播放的语音")
                return Response(path.read_bytes(), media_type="audio/mpeg",
                                headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/jobs/{job_id}/dubbing-file")
async def output(job_id: int, download: bool = Query(False),
                 session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if not job or job.kind not in {"dub_render", "dub_generate"}:
        raise HTTPException(404, "译配任务不存在")
    if job.state != JobState.done or not job.result:
        raise HTTPException(409, "译配尚未完成")
    extension = job.result.get("source_extension", "mp4")
    path = dubbing.output_path(job.result["output_key"], extension)
    if not path.is_file():
        raise HTTPException(410, "译配文件已被移除")
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = (
            f"attachment; filename=dubbed-{job_id}.{extension}; filename*=UTF-8''"
            + quote(job.result["filename"], safe="")
        )
    return FileResponse(path, media_type="video/mp4" if extension == "mp4"
                        else "video/x-matroska", headers=headers)


@router.get("/dubbing/references")
async def references_list():
    return refs.list_references()


@router.post("/dubbing/references", status_code=201)
async def references_create(request: Request, session: AsyncSession = Depends(get_session)):
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        length = 0
    if not 0 < length <= refs.MAX_UPLOAD + 16384:
        raise HTTPException(413, "参考音频不能超过 20 MB，请使用带 Content-Length 的文件上传")
    async with request.form(max_files=1, max_fields=3, max_part_size=8192) as form:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(422, "请选择音频文件")
        fields = {key: form.get(key, "") for key in ("name", "gender", "text")}
        if any(not isinstance(value, str) for value in fields.values()):
            raise HTTPException(422, "音色信息无效")
        try:
            row = await refs.create(file, **fields)
            if not row.text:
                try:
                    await queue_reference_transcription(row.id, session)
                except HTTPException as exc:
                    # Keep the validated clip so a queue failure can be retried.
                    return {**row.model_dump(), "transcription_error": exc.detail}
            return row
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None


async def queue_reference_transcription(key, session):
    async with refs.LOCK:
        row = refs.get(key)
        active = await session.scalar(select(Job.id).where(
            Job.kind == "dub_reference_transcribe",
            Job.state.in_([JobState.queued, JobState.running]),
            Job.payload["reference_id"].as_string() == key).limit(1))
        if active:
            raise HTTPException(409, "此音频正在识别，请等待完成或先取消")
        config = await asr_cfg.read_config(session)
        return await enqueue_unique("dub_reference_transcribe", {
            "reference_id": row.id, "version": row.version,
            "provider": config.asr_provider,
            "model": config.asr_model if config.asr_provider == "local" else config.asr_api.model,
            # Detect a provider address change without persisting credentials in jobs.
            "base_url": config.asr_api.base_url if config.asr_provider == "api" else "",
        }, session)


@router.get("/dubbing/references/jobs")
async def reference_jobs(session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(Job).where(Job.kind == "dub_reference_transcribe")
                                 .order_by(Job.id.desc()).limit(500))
    latest = {}
    for row in rows:
        key = (row.payload or {}).get("reference_id")
        if key and key not in latest:
            latest[key] = {**JobOut.model_validate(row).model_dump(),
                           "reference_id": key, "model": row.payload.get("model"),
                           "provider": row.payload.get("provider")}
    return {"items": list(latest.values())}


@router.post("/dubbing/references/{key}/transcribe", status_code=202)
async def reference_transcribe(key: str, session: AsyncSession = Depends(get_session)):
    try:
        return await queue_reference_transcription(key, session)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


class ReferenceTextBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    version: PositiveInt


@router.put("/dubbing/references/{key}")
async def reference_text(key: str, body: ReferenceTextBody):
    async with refs.LOCK:
        try:
            return refs.write_text(key, body.text, body.version)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None


@router.get("/dubbing/references/{key}/audio")
async def references_audio(key: str):
    try:
        refs.get(key)
        return FileResponse(refs.directory(key) / "audio.wav", media_type="audio/wav",
                            headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.delete("/dubbing/references/{key}", status_code=204)
async def references_delete(key: str, session: AsyncSession = Depends(get_session)):
    async with refs.LOCK:
        config = await cfg.read_config(session)
        if key in {config.male_reference, config.female_reference}:
            raise HTTPException(409, "此音色仍是默认男声或女声，请先更换或清除默认选择并保存")
        active = await session.scalar(select(Job.id).where(
            Job.kind.in_(["dub_render", "dub_generate"]), Job.state.in_([JobState.queued, JobState.running])).limit(1))
        if active:
            raise HTTPException(409, "有配音视频正在生成，请完成或取消任务后再删除音色")
        recognizing = await session.scalar(select(Job.id).where(
            Job.kind == "dub_reference_transcribe",
            Job.state.in_([JobState.queued, JobState.running]),
            Job.payload["reference_id"].as_string() == key).limit(1))
        if recognizing:
            raise HTTPException(409, "此音频正在识别，请完成或取消后再删除")
        try:
            refs.remove(key)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
    return Response(status_code=204)


class ReferencePreviewBody(BaseModel):
    reference_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    text: str = Field(min_length=1, max_length=4000)


@router.post("/dubbing/reference-preview")
async def reference_preview(body: ReferencePreviewBody,
                            session: AsyncSession = Depends(get_session)):
    config = await cfg.read_config(session)
    await session.rollback()
    if not local.is_reference(config):
        raise HTTPException(422, "请先选择并保存在线参考音色服务，或本地参考音色模型")
    try:
        async with PREVIEW_LIMIT:
            reference = refs.get(body.reference_id)
            await cfg.resolve_voices(
                config, config.target_language,
                reference.id if reference.gender == "male" else "",
                reference.id if reference.gender == "female" else "", (reference.gender,))
            settings.work_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="ref-preview-", dir=settings.work_dir) as work:
                path = Path(work) / "preview.mp3"
                await cfg.synthesize(config, body.text, reference.id, path)
                if not parse_probe_json(await _run_ffprobe(str(path))).audio_codec:
                    raise ValueError("服务未返回可播放的语音")
                return Response(path.read_bytes(), media_type="audio/mpeg",
                                headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/dubbing/local-models")
async def local_models():
    return local.catalog()


class LocalDownloadBody(BaseModel):
    model: Literal["qwen3-tts-0.6b-custom", "qwen3-tts-0.6b-base"]


@router.post("/dubbing/local-models/download", status_code=202)
async def local_model_download(body: LocalDownloadBody,
                               session: AsyncSession = Depends(get_session)):
    if not local.runtime_ready():
        raise HTTPException(422, "本地语音运行环境未安装，请先运行 scripts/setup-local-tts.sh")
    return await enqueue_unique("dub_model_download", body.model_dump(), session)

@router.post("/dubbing/local-models/uninstall")
async def local_model_uninstall(body: LocalDownloadBody,
                                session: AsyncSession = Depends(get_session)):
    async with ENQUEUE_LOCK:
        # Both preview slots must be idle before removing local speech assets.
        async with PREVIEW_LIMIT:
            async with PREVIEW_LIMIT:
                try:
                    return await model_uninstall.uninstall("dubbing", body.model, session)
                except model_uninstall.ModelBusy as exc:
                    raise HTTPException(409, str(exc)) from None
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None


@router.get("/dubbing/local-models/jobs")
async def local_model_jobs(session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(Job).where(Job.kind == "dub_model_download")
                                 .order_by(Job.id.desc()).limit(50))
    return {"items": [{**JobOut.model_validate(row).model_dump(),
                       "model": (row.payload or {}).get("model")} for row in rows]}


class GenerateBody(BaseModel):
    track_id: PositiveInt | None = None


@router.post("/videos/{video_id}/dubbing/generate", status_code=202)
async def generate_saved(video_id: int, body: GenerateBody,
                         session: AsyncSession = Depends(get_session)):
    await require_video(session, video_id)
    config = await cfg.read_config(session)
    try:
        cfg.validate_reference_language(config, config.target_language)
        if config.provider == "local":
            local.ensure_ready(config.local_model)
        await dubbing.checked_source(session, video_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.track_id:
        track = await session.get(SubtitleTrack, body.track_id)
        if not track or track.video_id != video_id:
            raise HTTPException(422, "请选择当前视频的字幕")
    return await enqueue_unique("dub_generate", {
        "video_id": video_id, "track_id": body.track_id,
        "target_language": config.target_language, "voice_mode": "auto",
        "keep_original": False,
    }, session)


@router.get("/videos/{video_id}/dubbing/tracks")
async def playback_tracks(video_id: int, session: AsyncSession = Depends(get_session)):
    await require_video(session, video_id)
    rows = await session.scalars(select(Job).where(
        Job.video_id == video_id, Job.kind.in_(["dub_render", "dub_generate"]),
        Job.state == JobState.done).order_by(Job.id.desc()).limit(100))
    result, checked = [], {}
    for row in rows:
        data = row.result or {}
        project = await session.get(DubbingProject, data.get("project_id")) if data.get("project_id") else None
        if not project or project.video_id != video_id:
            continue
        source_id = data.get("source_id", project.source_id)
        expected = data.get("source_fingerprint", project.source_fingerprint)
        if source_id is None or not expected:
            continue
        identity = (source_id, tuple(expected))
        try:
            if identity not in checked:
                await dubbing.checked_source(session, video_id, source_id, expected)
                checked[identity] = True
            if not dubbing.output_path(data.get("output_key"), data.get("source_extension", "mp4")).is_file():
                continue
        except (ValueError, OSError, HTTPException):
            continue
        language = data.get("language", project.language)
        result.append({
            "id": row.id, "language": language,
            "label": cfg.LANGUAGES.get(language, language) + "配音",
            "created_at": row.finished_at or row.created_at,
            "stream_url": f"/api/jobs/{row.id}/dubbing-file",
            "project_id": project.id,
            "source_extension": data.get("source_extension", "mp4"),
            "container": data.get("container", "mp4"),
            "video_codec": data.get("video_codec", "h264"),
            "audio_codec": data.get("audio_codec", "aac"),
            "video_copied": data.get("video_copied", False),
        })
    return result
