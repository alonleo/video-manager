"""Background container conversion, with real cancellation and bounded output."""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import update

from app.config import settings
from app.db import SessionLocal
from app.models import Job, JobState, Video
from app.services.conversion_media import (
    FORMATS,
    check_conflicting_jobs,
    conversion_command,
    fingerprint,
    native_selection,
    prepare_subtitles,
    selected_tracks,
    validate_output,
)
from app.services.conversion_replace import install_replacement, replacement_paths
from app.services.operations import ConvertRequest, local_source, remux_command
from app.services.probe import _run_ffprobe, parse_probe_json
from app.services.ts_repair import (
    inspect_ts_duration,
    ts_repair_command,
    validate_ts_repair_output,
)

_slots = asyncio.Semaphore(2)


class ConversionCanceled(Exception):
    pass


def export_path(job_id: int, extension: str, *, partial: bool = False) -> Path:
    if extension not in FORMATS:
        raise ValueError("不支持的输出格式")
    root = (settings.downloads_dir / "exports").resolve()
    path = root / f"job-{job_id}" / (("partial" if partial else "result") + "." + extension)
    if not path.resolve().is_relative_to(root):
        raise ValueError("输出路径无效")
    return path


async def _stop(proc) -> None:
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except TimeoutError:
            proc.kill()
            await proc.wait()


async def run_remux(
    source: Path, output: Path, duration_ms: int | None, check, *, command=None
) -> None:
    """check(progress) persists progress and raises when cancellation is requested."""
    output.parent.mkdir(parents=True, exist_ok=True)
    reserve = settings.playback_min_free_mb * 1024**2
    maximum = settings.export_max_file_mb * 1024**2
    if shutil.disk_usage(output.parent).free < reserve + source.stat().st_size * 1.1:
        raise ValueError("可用磁盘空间不足，无法安全保存转换结果")
    if source.stat().st_size > maximum:
        raise ValueError(f"源文件超过单次转换限制（{settings.export_max_file_mb} MB）")
    await check(0)
    proc = await asyncio.create_subprocess_exec(
        *(command or remux_command(source, output, settings.ffmpeg_bin)),
        cwd=output.parent,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    errors = deque(maxlen=15)
    fraction = 0.0

    async def read_progress():
        nonlocal fraction
        while line := await proc.stdout.readline():
            key, _, value = line.decode("utf-8", "replace").strip().partition("=")
            if key == "out_time_us" and duration_ms:
                try:
                    fraction = max(fraction, min(0.98, int(value) / (duration_ms * 1000)))
                except ValueError:
                    pass

    async def read_errors():
        while line := await proc.stderr.readline():
            errors.append(line.decode("utf-8", "replace")[:500])

    readers = [asyncio.create_task(read_progress()), asyncio.create_task(read_errors())]
    waiter = asyncio.create_task(proc.wait())
    started = time.monotonic()
    try:
        while not waiter.done():
            await check(fraction)
            if time.monotonic() - started > settings.export_timeout_sec:
                raise ValueError("格式转换超时，请检查源文件或磁盘状态")
            if output.exists() and output.stat().st_size > maximum:
                raise ValueError("转换结果超过单个文件大小限制，已停止并清理临时文件")
            if shutil.disk_usage(output.parent).free < reserve:
                raise ValueError("磁盘剩余空间不足，已停止并清理临时文件")
            await asyncio.wait({waiter}, timeout=0.5)
        await asyncio.gather(*readers)
        await check(fraction)
        if proc.returncode:
            detail = "".join(errors).strip()
            raise ValueError(
                "无法在保留原编码的情况下转换为该格式。可尝试 MKV；"
                "若仍失败，请检查源文件是否损坏。" + (f"（{detail[:600]}）" if detail else "")
            )
        if not output.is_file() or output.stat().st_size < 16:
            raise ValueError("未生成有效的转换文件")
    finally:
        await _stop(proc)
        await asyncio.gather(waiter, *readers, return_exceptions=True)


async def run_conversion(ctx: dict, job_id: int) -> dict:
    partial = final = None
    subtitle_files = []
    body = None

    async def check(progress: float) -> None:
        async with SessionLocal() as session:
            changed = await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == JobState.running)
                .values(
                    progress=progress,
                    message="正在烧录字幕，重新编码视频"
                    if body and body.subtitle_mode == "burn"
                    else "正在转换格式并处理所选字幕轨",
                )
            )
            await session.commit()
            if not changed.rowcount:
                raise ConversionCanceled()

    async def finish_error(message: str):
        async with SessionLocal() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state.in_([JobState.queued, JobState.running]))
                .values(
                    state=JobState.error,
                    error=message[:2000],
                    message="格式转换失败",
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()

    try:
        async with _slots:
            async with SessionLocal() as session:
                claimed = await session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.state == JobState.queued)
                    .values(
                        state=JobState.running,
                        started_at=datetime.now(UTC),
                        attempts=Job.attempts + 1,
                        message="正在读取视频信息",
                    )
                )
                await session.commit()
                if not claimed.rowcount:
                    return {}
                job = await session.get(Job, job_id)
                body = ConvertRequest.model_validate(
                    {k: v for k, v in job.payload.items() if k in ConvertRequest.model_fields}
                )
                video = await session.get(Video, job.video_id)
                if video is None:
                    raise ValueError("视频已从媒体库移除")
                ref, source = await local_source(session, video.id, body.source_id)
                source_id, video_id = ref.id, video.id
                original_fingerprint = fingerprint(source)
                if (
                    job.payload.get("_source_fingerprint", original_fingerprint)
                    != original_fingerprint
                ):
                    raise ValueError("提交任务后原视频发生变化，请重新提交转换")
                await check_conflicting_jobs(session, ref, body, job_id)
                tracks = await selected_tracks(session, video.id, body)
                for track in tracks:
                    if (
                        job.payload.get("_subtitle_versions", {}).get(str(track.id), track.version)
                        != track.version
                    ):
                        raise ValueError("所选字幕已修改，请重新提交转换")
                title = video.title
            if body.replace_source:
                partial, _backup = replacement_paths(source, job_id, body.format, create=True)
            else:
                partial = export_path(job_id, body.format, partial=True)
                final = export_path(job_id, body.format)
            partial.parent.mkdir(parents=True, exist_ok=True)
            original_raw = await _run_ffprobe(str(source))
            info = parse_probe_json(original_raw)
            if not info.has_video:
                raise ValueError("源文件不包含视频画面")
            native = native_selection(body, original_raw)
            subtitle_files = [partial.parent / f"subtitle-{i}.srt" for i in range(len(tracks))]
            subtitle_files.append(partial.parent / "subtitle-source")
            files = await prepare_subtitles(body, tracks, source, partial.parent, info.duration_ms)
            command = (
                conversion_command(source, partial, settings.ffmpeg_bin, body, native, files)
                if body.subtitle_mode != "none" or body.format == "m2ts"
                else None
            )
            if job.payload.get("_ts_repair"):
                command = ts_repair_command(source, partial, settings.ffmpeg_bin)
            await run_remux(source, partial, info.duration_ms, check, command=command)
            produced_raw = await _run_ffprobe(str(partial))
            if job.payload.get("_ts_repair"):
                validate_ts_repair_output(original_raw, produced_raw)
                produced = parse_probe_json(produced_raw)
                repaired = inspect_ts_duration(partial, produced_raw)
                if repaired.needs_repair:
                    raise ValueError("无损重封装后时长信息仍不一致，已保留原文件")
            else:
                produced = validate_output(body, original_raw, produced_raw)
            if fingerprint(source) != original_fingerprint:
                raise ValueError("转换过程中原文件发生变化，已停止发布结果")
            await check(0.99)
            safe_title = (
                re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", title)[:60].strip(". ") or "video"
            )
            result = {
                "filename": f"{safe_title}-{job_id}.{body.format}",
                "format": body.format,
                "video_id": video_id,
                "size_bytes": partial.stat().st_size,
                "duration_ms": produced.duration_ms,
                "download_url": f"/api/jobs/{job_id}/file",
                "subtitle_mode": body.subtitle_mode,
                "subtitle_count": len(body.subtitle_track_ids) + len(body.source_subtitle_indices),
                "replaced": False,
            }
            if job.payload.get("_ts_repair"):
                result.update({
                    "ts_repair": True,
                    "duration_before": job.payload.get("_duration_before"),
                    "duration_after": repaired.__dict__,
                })
            if body.replace_source:
                async with SessionLocal() as session:
                    installation = asyncio.create_task(
                        install_replacement(
                            session,
                            job_id,
                            source_id,
                            source,
                            partial,
                            produced_raw,
                            body,
                            original_fingerprint,
                            result,
                        )
                    )
                    try:
                        installed = await asyncio.shield(installation)
                        if job.payload.get("_ts_repair"):
                            backup = Path(installed["backup_path"])
                            await asyncio.to_thread(backup.unlink)
                            installed.pop("backup_path", None)
                            installed["original_removed"] = True
                            await session.execute(
                                update(Job).where(Job.id == job_id, Job.state == JobState.done).values(
                                    result=installed,
                                    message="TS 时长修复完成，已替换来源并移除原片",
                                )
                            )
                            await session.commit()
                        return installed
                    except asyncio.CancelledError:
                        await installation
                        raise

            from app.services.operations import move_without_replace

            move_without_replace(partial, final)
            async with SessionLocal() as session:
                done = await session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.state == JobState.running)
                    .values(
                        state=JobState.done,
                        progress=1.0,
                        result=result,
                        message="转换完成，可下载新文件",
                        finished_at=datetime.now(UTC),
                    )
                )
                await session.commit()
                if not done.rowcount:
                    raise ConversionCanceled()
            final = None  # Successful files remain until explicitly removed by the user.
            return result
    except ConversionCanceled:
        return {}
    except asyncio.CancelledError:
        await finish_error("处理进程中断，请重新提交转换")
        raise
    except Exception as exc:
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        await finish_error(str(message))
        return {}
    finally:
        for temporary in subtitle_files:
            temporary.unlink(missing_ok=True)
        if partial:
            partial.unlink(missing_ok=True)
        if final:
            final.unlink(missing_ok=True)
