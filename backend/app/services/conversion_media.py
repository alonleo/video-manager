"""Explicit subtitle mapping and container-aware validation for conversion."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Job, JobState, SubtitleTrack, VideoSource
from app.services.operations import ConvertRequest
from app.services.subtitle_text import bilingual_cues, export_subtitles, validate_cues

FORMATS = {"mkv", "mp4", "mov", "avi", "ts", "m2ts"}
TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "sami"}
NATIVE_BITMAP = {
    "mkv": {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"},
    "avi": {"xsub"},
    "ts": {"dvb_subtitle", "dvb_teletext", "hdmv_pgs_subtitle"},
    "m2ts": {"dvb_subtitle", "hdmv_pgs_subtitle"},
    "mp4": set(),
    "mov": set(),
}


def fingerprint(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def subtitle_catalog(raw: dict) -> list[dict]:
    items = []
    for stream in raw.get("streams", []):
        if stream.get("codec_type") != "subtitle":
            continue
        codec = stream.get("codec_name", "unknown")
        text = codec in TEXT_CODECS
        items.append(
            {
                "index": stream["index"],
                "ordinal": len(items),
                "codec": codec,
                "language": stream.get("tags", {}).get("language", "und"),
                "label": stream.get("tags", {}).get("title", f"原视频字幕 {len(items) + 1}"),
                "burn_supported": text,
                "embed_formats": [
                    fmt
                    for fmt in sorted(FORMATS)
                    if (text and fmt in {"mkv", "mp4", "mov"}) or codec in NATIVE_BITMAP[fmt]
                ],
            }
        )
    return items


async def selected_tracks(session, video_id: int, body: ConvertRequest) -> list[SubtitleTrack]:
    tracks = []
    for track_id in body.subtitle_track_ids:
        track = await session.get(SubtitleTrack, track_id)
        if track is None or track.video_id != video_id:
            raise HTTPException(422, "选择的字幕不存在或不属于这个视频")
        validate_cues(track.cues)
        tracks.append(track)
    return tracks


async def check_conflicting_jobs(session, source, body: ConvertRequest, job_id=None):
    related_ids = set(
        (
            await session.scalars(
                select(VideoSource.video_id).where(
                    VideoSource.local_path == source.local_path,
                )
            )
        ).all()
    )
    pending = (
        await session.scalars(
            select(Job).where(
                Job.state.in_([JobState.queued, JobState.running]),
            )
        )
    ).all()
    for job in pending:
        if job.id == job_id:
            continue
        if job.video_id in related_ids and (
            body.replace_source
            or (job.payload or {}).get("replace_source")
            or job.kind == "move_video"
        ):
            raise HTTPException(409, "该视频还有处理任务；替换原视频前请完成或取消这些任务")
        if (
            body.replace_source
            and job.kind == "scan_library"
            and (job.payload or {}).get("storage_id") == source.storage_id
        ):
            raise HTTPException(409, "存储源正在扫描，请完成后再提交替换转换")


def native_selection(body: ConvertRequest, raw: dict) -> list[dict]:
    catalog = {item["index"]: item for item in subtitle_catalog(raw)}
    selected = []
    for index in body.source_subtitle_indices:
        item = catalog.get(index)
        if item is None:
            raise ValueError("原视频字幕轨已改变，请刷新后重新选择")
        if body.subtitle_mode == "embed" and body.format not in item["embed_formats"]:
            raise ValueError(
                f"{item['codec']} 字幕不能直接写入 {body.format.upper()}，请使用 MKV 或文字字幕烧录"
            )
        if body.subtitle_mode == "burn" and not item["burn_supported"]:
            raise ValueError("当前烧录支持文字字幕；图形字幕请选择兼容容器保留软字幕轨")
        selected.append(item)
    return selected


def _language(value: str) -> str:
    code = value.lower().split("-")[0]
    return {
        "zh": "zho",
        "en": "eng",
        "ja": "jpn",
        "ko": "kor",
        "fr": "fra",
        "de": "deu",
        "es": "spa",
        "ru": "rus",
    }.get(code, code if len(code) == 3 else "und")


async def prepare_subtitles(body, tracks, source, directory, duration_ms):
    files = []
    for index, track in enumerate(tracks):
        cues = (
            bilingual_cues(track.cues, track.source_cues)
            if body.bilingual and track.source_cues
            else track.cues
        )
        if duration_ms:
            cues = [
                {**cue, "end_ms": min(cue["end_ms"], duration_ms)}
                for cue in cues
                if cue["start_ms"] < duration_ms
            ]
        if not cues:
            raise ValueError("所选字幕在视频时长内没有有效内容")
        path = directory / f"subtitle-{index}.srt"
        await asyncio.to_thread(path.write_text, export_subtitles(cues, "srt"), encoding="utf-8")
        files.append({"path": path, "label": track.label, "language": _language(track.language)})
    if body.subtitle_mode == "burn" and body.source_subtitle_indices:
        # Use a controlled filename in the filter expression, never interpolate a user path.
        (directory / "subtitle-source").symlink_to(source)
    return files


def conversion_command(source: Path, output: Path, ffmpeg: str, body, native, files) -> list[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(source),
    ]
    if body.subtitle_mode == "embed":
        for item in files:
            command += ["-i", str(item["path"])]
    command += ["-map", "0:V:0", "-map", "0:a?", "-c:v", "copy", "-c:a", "copy", "-dn"]
    if body.subtitle_mode == "embed":
        for item in native:
            command += ["-map", f"0:{item['index']}"]
        for index in range(len(files)):
            command += ["-map", f"{index + 1}:s:0"]
        for index, item in enumerate(native):
            codec = (
                "copy"
                if item["codec"] not in TEXT_CODECS
                else ("mov_text" if body.format in {"mp4", "mov"} else "srt")
            )
            command += [f"-c:s:{index}", codec]
        for index, item in enumerate(files, len(native)):
            command += [
                f"-c:s:{index}",
                "mov_text" if body.format in {"mp4", "mov"} else "srt",
                f"-metadata:s:s:{index}",
                f"language={item['language']}",
                f"-metadata:s:s:{index}",
                f"title={item['label']}",
            ]
        command += ["-disposition:s:0", "default"]
    else:
        command += ["-sn"]
    if body.subtitle_mode == "burn":
        name = files[0]["path"].name if files else "subtitle-source"
        filt = f"subtitles=filename={name}"
        if native:
            filt += f":si={native[0]['ordinal']}"
        filt += (
            ":force_style='FontName=Noto Sans CJK SC,Alignment=2,"
            "FontSize=22,MarginV=24,Outline=1.5'"
        )
        command += [
            "-vf",
            filt,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "2",
        ]
    if body.format in {"mp4", "mov"}:
        command += ["-movflags", "+faststart"]
    if body.format in {"ts", "m2ts"}:
        command += ["-f", "mpegts"]
        if body.format == "m2ts":
            command += ["-mpegts_m2ts_mode", "1"]
    return command + [str(output)]


def validate_output(body, original, produced):
    def streams(raw, kind):
        return [s for s in raw.get("streams", []) if s.get("codec_type") == kind]

    output_video = streams(produced, "video")
    if not output_video:
        raise ValueError("转换结果缺少有效视频流")
    original_video = [
        s for s in streams(original, "video") if not s.get("disposition", {}).get("attached_pic")
    ]
    expected = "h264" if body.subtitle_mode == "burn" else original_video[0].get("codec_name")
    if output_video[0].get("codec_name") != expected:
        raise ValueError("转换结果的视频编码校验失败")
    if len(streams(original, "audio")) != len(streams(produced, "audio")):
        raise ValueError("转换结果缺少音轨，已停止发布")
    wanted = (
        len(body.subtitle_track_ids) + len(body.source_subtitle_indices)
        if body.subtitle_mode == "embed"
        else 0
    )
    if len(streams(produced, "subtitle")) != wanted:
        raise ValueError("转换结果的字幕轨数量不正确，已停止发布")
    from app.services.probe import parse_probe_json

    before, after = parse_probe_json(original), parse_probe_json(produced)
    if before.duration_ms and (
        not after.duration_ms
        or abs(after.duration_ms - before.duration_ms) > max(1500, before.duration_ms * 0.02)
    ):
        raise ValueError("转换结果时长与原视频不一致，原文件保持不变")
    return after
