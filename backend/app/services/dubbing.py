"""Editable, timed dubbing. Outputs never replace a source file."""
from __future__ import annotations

import asyncio
import json
import math
import shutil
import sys
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.config import settings
from app.db import SessionLocal
from app.models import DubbingProject, SubtitleTrack, Video
from app.services import dubbing_config as cfg
from app.services import subtitles
from app.services.conversion_media import fingerprint
from app.services.operations import local_source
from app.services.probe import _run_ffprobe, parse_probe_json, probe
from app.services.subtitle_text import Cue

RATE = 24000
RENDER_LOCK = asyncio.Lock()


class DubCue(Cue):
    gender: Literal["male", "female"]
    source_text: str = Field(default="", max_length=4000)
    uncertain: bool = False
    pitch_hz: float | None = Field(default=None, ge=50, le=500)


def output_path(key, extension="mp4"):
    if not isinstance(key, str) or len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("配音文件编号无效")
    if extension not in {"mp4", "mkv"}:
        raise ValueError("配音封装格式无效")
    return settings.data_dir / "dubbing" / "outputs" / key / ("result." + extension)


def stage(progress, start, width, label):
    async def report(done, total, message=""):
        if done is None:
            return await progress(None, None, "")
        ratio = max(0, min(1, done / total if total else 0))
        await progress(start + width * ratio, 100, f"{label} · {message}")
    return report


async def checked_source(session, video_id, source_id=None, expected=None):
    source, path = await local_source(session, video_id, source_id)
    if expected is not None and fingerprint(path) != expected:
        raise ValueError("原视频已修改，请重新准备译配稿")
    return source, path


async def prepare(payload, progress):
    async with SessionLocal() as session:
        config = await subtitles.read_config(session)
        voice_config = await cfg.read_config(session)
        source, path = await checked_source(session, payload["video_id"])
        source_id, original = source.id, fingerprint(path)
        track = await session.get(SubtitleTrack, payload.get("track_id")) if payload.get("track_id") else None
        if track and track.video_id != payload["video_id"]:
            raise ValueError("字幕不属于此视频")
        if payload.get("track_id") and not track:
            raise ValueError("所选字幕已不存在")
    media = await probe(str(path))
    if not media.audio_codec or not media.duration_ms or media.duration_ms > 6 * 3600_000:
        raise ValueError("译配需要有音轨且时长不超过 6 小时的视频")
    if track is None:
        result = await subtitles.dispatch("subtitle_generate", {
            "video_id": payload["video_id"], "provider": config.asr_provider,
            "model": config.asr_model if config.asr_provider == "local" else config.asr_api.model,
            "language": "auto",
        }, stage(progress, 0, 40, "识别对白"))
        async with SessionLocal() as session:
            track = await session.get(SubtitleTrack, result["track_id"])
    target = payload["target_language"]
    # Regional variants share the translation language, while TTS keeps the selected locale.
    same_language = track.language.lower().split("-")[0] == target.lower().split("-")[0]
    if not same_language:
        model = {"local": config.local_translation_model, "api": config.translation_api.model,
                 "ollama": config.ollama_model}[config.translation_provider]
        result = await subtitles.dispatch("subtitle_translate", {
            "video_id": payload["video_id"], "track_id": track.id, "source_version": track.version,
            "provider": config.translation_provider, "model": model, "target_language": target,
        }, stage(progress, 40, 30, "翻译对白"))
        async with SessionLocal() as session:
            track = await session.get(SubtitleTrack, result["track_id"])
    if len(track.cues) > 10000:
        raise ValueError("译配最多处理 10000 句，请拆分长视频")
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dub-prepare-", dir=settings.work_dir) as work:
        work = Path(work)
        wav = work / "source.wav"
        manifest, result_file = work / "input.json", work / "voices.json"
        if shutil.disk_usage(work).free < media.duration_ms * 32 + 256 * 1024**2:
            raise ValueError("磁盘空间不足，无法准备译配音轨")
        await subtitles.subprocess_job([
            settings.ffmpeg_bin, "-nostdin", "-v", "error", "-y", "-i", str(path),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
        ], stage(progress, 70, 5, "提取对白"))
        manifest.write_text(json.dumps({"cues": track.cues, "fallback": voice_config.fallback_gender,
                                       "mode": payload.get("voice_mode", "auto")}))
        await subtitles.subprocess_job([
            sys.executable, "-m", "app.services.dubbing_audio", "classify",
            str(wav), str(manifest), str(result_file),
        ], stage(progress, 75, 24, "分析声线"), parse_progress=True)
        guesses = json.loads(result_file.read_text())
    cues = []
    for i, cue in enumerate(track.cues):
        if cue["start_ms"] >= media.duration_ms:
            continue
        cue = {**cue, "end_ms": min(cue["end_ms"], media.duration_ms)}
        source_cues = track.source_cues or track.cues
        cues.append(DubCue(**cue, **guesses[i],
                           source_text=source_cues[i]["text"] if i < len(source_cues) else "").model_dump())
    if not cues:
        raise ValueError("没有处于视频时间范围内的对白")
    await progress(99, 100, "保存可编辑译配稿")
    async with SessionLocal() as session:
        await checked_source(session, payload["video_id"], source_id, original)
        project = DubbingProject(video_id=payload["video_id"], source_id=source_id,
                                 source_fingerprint=original, language=target, cues=cues)
        session.add(project)
        await session.commit()
        return {"project_id": project.id, "cue_count": len(cues), "language": target}


def tempo_filters(ratio):
    filters = []
    while ratio > 2:
        filters.append("atempo=2")
        ratio /= 2
    if ratio > 1.001:
        filters.append(f"atempo={ratio:.8f}")
    return filters


def replacement_format(media):
    # Container choice follows the existing codec. Never transcode the picture
    # merely to force every source into MP4.
    return "mp4" if media.video_codec in {"h264", "hevc", "av1", "mpeg4"} else "mkv"


def replacement_args(path, timeline, partial, media, language, keep_original):
    streams = media.raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    video_map = "0:" + str(video["index"]) if video is not None else "0:v:0"
    duration = media.duration_ms / 1000
    args = [settings.ffmpeg_bin, "-nostdin", "-v", "error", "-y", "-i", str(path),
            "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", str(timeline),
            "-map", video_map, "-map", "1:a:0"]
    if keep_original:
        args += ["-map", "0:a:0?"]
    args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-threads", "2",
             "-t", str(duration), "-map_metadata", "0", "-map_chapters", "0",
             "-metadata:s:a:0", "title=AI dubbing - " + language,
             "-disposition:a:0", "default"]
    if keep_original:
        # Copy common original audio codecs too; otherwise encode only that audio.
        original_copy = {"aac", "mp3", "ac3", "eac3", "alac"}
        if partial.suffix == ".mkv":
            original_copy |= {"flac", "opus", "vorbis", "dts", "pcm_s16le", "pcm_s24le"}
        if media.audio_codec in original_copy:
            args += ["-c:a:1", "copy"]
        args += ["-metadata:s:a:1", "title=Original audio", "-disposition:a:1", "0"]
    if partial.suffix == ".mp4":
        args += ["-movflags", "+faststart"]
    args += ["-progress", "pipe:1", str(partial)]
    return args


async def render(payload, progress):
    async with SessionLocal() as session:
        project = await session.get(DubbingProject, payload["project_id"])
        if not project or project.version != payload["version"]:
            raise ValueError("译配稿已修改或删除，请重新提交")
        config = await cfg.read_config(session)
        source, path = await checked_source(session, project.video_id, project.source_id,
                                            project.source_fingerprint)
        video = await session.get(Video, project.video_id)
        title = video.title
        cues = [DubCue.model_validate(c).model_dump() for c in project.cues]
    resolved = await subtitles.wait_cancelable(
        cfg.resolve_voices(config, project.language, payload.get("male_voice", ""),
                           payload.get("female_voice", ""), {c["gender"] for c in cues}),
        progress, 1, 100, "读取目标语言音色")
    media = await probe(str(path))
    duration = (media.duration_ms or 0) / 1000
    if not 0 < duration <= 21600:
        raise ValueError("视频时长无效")
    if any(c["end_ms"] > media.duration_ms for c in cues):
        raise ValueError("译配稿时间轴超过视频时长")
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    extension = replacement_format(media)
    destination = output_path(key, extension)
    destination.parent.mkdir(parents=True, exist_ok=False)
    published = False
    try:
        with tempfile.TemporaryDirectory(prefix="dub-render-", dir=settings.work_dir) as work:
            work = Path(work)
            required = int(duration * RATE * 2 + path.stat().st_size * 2 + 256 * 1024**2)
            if shutil.disk_usage(work).free < required:
                raise ValueError("磁盘空间不足，无法保存配音音轨与封装副本")
            timeline = work / "timeline.pcm"
            with timeline.open("wb") as file:
                file.truncate(math.ceil(duration * RATE) * 2)
            accelerated = 0
            local_audio = {}
            if config.provider == "local":
                from app.services.dubbing_local import synthesize_many
                local_audio = {i: work / f"local-{i}.mp3" for i in range(len(cues))}
                await synthesize_many(config, [
                    {"text": cue["text"], "voice": resolved[cue["gender"]],
                     "language": project.language, "output": str(local_audio[i])}
                    for i, cue in enumerate(cues)
                ], stage(progress, 5, 60, "本地模型生成配音"))
            start, width = (65, 10) if local_audio else (5, 70)
            for i, cue in enumerate(cues):
                await progress(start + width * i / len(cues), 100, f"合成配音 {i + 1}/{len(cues)}")
                audio, fitted = local_audio.get(i, work / "speech.mp3"), work / "fitted.wav"
                report = stage(progress, start + width * i / len(cues), width / len(cues), f"配音 {i + 1}/{len(cues)}")
                if not local_audio:
                    await subtitles.wait_cancelable(
                        cfg.synthesize(config, cue["text"], resolved[cue["gender"]], audio),
                        report, 0, 1, "生成语音")
                info = parse_probe_json(await _run_ffprobe(str(audio)))
                if not info.duration_ms:
                    raise ValueError(f"第 {i + 1} 句未返回可解码语音")
                slot = (cue["end_ms"] - cue["start_ms"]) / 1000
                ratio = info.duration_ms / 1000 / slot
                accelerated += ratio > 1.5
                filters = tempo_filters(ratio) + [
                    f"apad=whole_dur={slot:.6f}", f"atrim=duration={slot:.6f}", "asetpts=PTS-STARTPTS"]
                await subtitles.subprocess_job([
                    settings.ffmpeg_bin, "-nostdin", "-v", "error", "-y", "-i", str(audio),
                    "-af", ",".join(filters), "-ar", str(RATE), "-ac", "1",
                    "-c:a", "pcm_s16le", str(fitted),
                ], report)
                # Sparse on-disk PCM timeline; overlapping speakers are mixed, never overwritten.
                await asyncio.to_thread(mix_segment, timeline, fitted, cue["start_ms"])
            await progress(76, 100, "替换音轨，直接复制原画面（不重新编码）")
            partial = destination.parent / ("partial." + extension)
            args = replacement_args(path, timeline, partial, media, project.language,
                                    payload.get("keep_original", config.keep_original))
            # ffmpeg progress is parsed by a dedicated wrapper to keep percentages meaningful.
            await run_mux(args, progress, duration)
            async with SessionLocal() as session:
                await checked_source(session, project.video_id, project.source_id,
                                     project.source_fingerprint)
            verified = await probe(str(partial))
            if not verified.video_codec or not verified.audio_codec or not verified.duration_ms:
                raise ValueError("配音成片校验失败")
            if (verified.video_codec, verified.width, verified.height) != (
                    media.video_codec, media.width, media.height):
                raise ValueError("原画面复制校验失败")
            if abs(verified.duration_ms - media.duration_ms) > 1000:
                raise ValueError("配音成片时长校验失败")
            await progress(99, 100, "音轨替换完成，原画面已保留")
            partial.rename(destination)
            published = True
            return {"project_id": project.id, "version": project.version, "output_key": key,
                    "language": project.language, "cue_count": len(cues),
                    "filename": f"{title[:100]}-{project.language}-配音.{extension}",
                    "accelerated_cues": accelerated, "video_copied": True,
                    "source_extension": extension, "container": extension,
                    "video_codec": verified.video_codec, "audio_codec": verified.audio_codec,
                    "source_id": project.source_id, "source_fingerprint": project.source_fingerprint,
                    "male_voice": resolved.get("male", ""), "female_voice": resolved.get("female", "")}
    finally:
        if not published:
            shutil.rmtree(destination.parent, ignore_errors=True)


def mix_segment(timeline, fitted, start_ms):
    import numpy as np
    with wave.open(str(fitted)) as wav, timeline.open("r+b") as file:
        file.seek(round(start_ms * RATE / 1000) * 2)
        while data := wav.readframes(RATE):
            position = file.tell()
            old = file.read(len(data))
            if not old:
                break
            new = np.frombuffer(data[:len(old)], dtype="<i2").astype(np.int32)
            merged = np.clip(new + np.frombuffer(old, dtype="<i2"), -32768, 32767).astype("<i2")
            file.seek(position)
            file.write(merged.tobytes())


async def run_mux(args, progress, duration):
    # Reuse the cancellation-safe child lifecycle while converting ffmpeg progress to JSON.
    with tempfile.TemporaryDirectory(prefix="dub-mux-", dir=settings.work_dir) as work:
        manifest = Path(work) / "mux.json"
        manifest.write_text(json.dumps({"args": args, "duration": duration}))
        await subtitles.subprocess_job(
            [sys.executable, "-m", "app.services.dubbing_audio", "mux", str(manifest)],
            stage(progress, 76, 22, "替换音轨 · 原画面直接复制"), parse_progress=True, timeout=21600)


async def generate(payload, progress):
    prepared = await prepare(payload, stage(progress, 0, 40, "准备配音"))
    return await render({**payload, "project_id": prepared["project_id"], "version": 1,
                         "keep_original": False}, stage(progress, 40, 59, "生成配音音轨"))


async def dispatch(kind, payload, progress):
    async def locked():
        async with RENDER_LOCK:
            handler = {"dub_prepare": prepare, "dub_render": render, "dub_generate": generate}[kind]
            return await handler(payload, progress)
    return await subtitles.wait_cancelable(locked(), progress, None, None, "")
