"""Bounded, cancellable enhancement and real motion interpolation jobs."""

from __future__ import annotations

import asyncio
import math
import re
import shutil
import struct
import time
from collections import deque
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import update

from app.config import settings
from app.db import SessionLocal
from app.models import Job, JobState, Video
from app.services.conversion_media import fingerprint
from app.services.enhancement import (
    ENGINE_ROOT,
    MODELS,
    EnhanceRequest,
    ai_available,
    enhancement_command,
    hardware_available,
    output_path,
    plan_enhancement,
    temporal_filters,
)
from app.services.operations import local_source, move_without_replace
from app.services.probe import _run_ffprobe, parse_probe_json
from app.workers.conversion import _stop

_slots = asyncio.Semaphore(1)


class EnhancementCanceled(Exception):
    pass


class Runner:
    def __init__(self, job_id, directory, duration):
        self.job_id, self.directory, self.duration = job_id, directory, duration
        self.progress, self.message = 0.0, "正在准备增强"
        self.processes, self.readers = [], []
        self.started = time.monotonic()

    async def spawn(self, command, *, stdin=None):
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.directory,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.processes.append(proc)
        errors = deque(maxlen=12)

        async def read_errors():
            while line := await proc.stderr.readline():
                errors.append(line.decode("utf-8", "replace")[:500])

        reader = asyncio.create_task(read_errors())
        self.readers.append(reader)
        return proc, reader, errors

    async def check(self):
        if time.monotonic() - self.started > settings.export_timeout_sec:
            raise ValueError("增强处理超时，请降低分辨率或帧率后重试")
        if shutil.disk_usage(self.directory).free < settings.playback_min_free_mb * 1024**2:
            raise ValueError("剩余磁盘空间不足，增强已停止")
        for name in ["partial.mp4", "original.mp4"]:
            path = self.directory / name
            if path.exists() and path.stat().st_size > settings.export_max_file_mb * 1024**2:
                raise ValueError("增强输出超过文件大小上限")
        async with SessionLocal() as session:
            changed = await session.execute(
                update(Job)
                .where(Job.id == self.job_id, Job.state == JobState.running)
                .values(progress=min(0.99, self.progress), message=self.message)
            )
            await session.commit()
            if not changed.rowcount:
                current = await session.get(Job, self.job_id)
                if current and current.state == JobState.done:
                    return
                raise EnhancementCanceled()

    async def watch(self):
        while True:
            await self.check()
            await asyncio.sleep(0.5)

    async def run(self, command, low, high):
        proc, reader, errors = await self.spawn(command)
        while line := await proc.stdout.readline():
            key, _, value = line.decode("utf-8", "replace").strip().partition("=")
            if key == "out_time_us":
                try:
                    fraction = int(value) / (self.duration * 1_000_000)
                    self.progress = max(self.progress, low + min(1, fraction) * (high - low))
                except ValueError:
                    pass
        await proc.wait()
        await reader
        if proc.returncode:
            raise ValueError("视频增强失败：" + "".join(errors)[-1000:])
        self.progress = max(self.progress, high)

    async def close(self):
        await asyncio.gather(*(_stop(p) for p in self.processes), return_exceptions=True)
        for reader in self.readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*self.readers, return_exceptions=True)


async def read_png(stream):
    """Read exactly one PNG from image2pipe, with a fixed per-frame memory limit."""
    try:
        signature = await stream.readexactly(8)
    except asyncio.IncompleteReadError as exc:
        if exc.partial:
            raise ValueError("视频解码帧不完整") from exc
        return None
    if signature != b"\x89PNG\r\n\x1a\n":
        raise ValueError("视频解码图像格式错误")
    chunks = [signature]
    total = 8
    while True:
        header = await stream.readexactly(8)
        size = struct.unpack(">I", header[:4])[0]
        total += size + 12
        if total > 64 * 1024**2:
            raise ValueError("单帧图像超出内存保护限制")
        chunks += [header, await stream.readexactly(size + 4)]
        if header[4:] == b"IEND":
            return b"".join(chunks)


async def run_ai(runner, source, output, body, plan, hardware):
    incoming, outgoing = runner.directory / "ai-in", runner.directory / "ai-out"
    incoming.mkdir(exist_ok=True)
    outgoing.mkdir(exist_ok=True)
    name, scale, _ = MODELS[body.ai_model]
    # Decode at the target cadence before AI, keeping its memory/disk usage
    # bounded to four frames. Motion estimation runs at the input resolution.
    decoder_command = [
        settings.ffmpeg_bin,
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "2",
        "-filter_threads",
        "2",
        "-ss",
        str(plan["start"]),
        "-i",
        str(source),
        "-map",
        "0:V:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        ",".join(temporal_filters(body, plan)),
        "-t",
        str(plan["duration"]),
        "-fps_mode",
        "passthrough",
        "-c:v",
        "png",
        "-threads",
        "2",
        "-f",
        "image2pipe",
        "pipe:1",
    ]
    decoder, decoder_reader, decoder_errors = await runner.spawn(decoder_command)
    encoder, encoder_reader, encoder_errors = await runner.spawn(
        enhancement_command(source, output, body, plan, hardware, image_pipe=True),
        stdin=asyncio.subprocess.PIPE,
    )

    async def drain_encoder():
        while await encoder.stdout.readline():
            pass

    drain = asyncio.create_task(drain_encoder())
    runner.readers.append(drain)
    frames, expected = 0, max(1, math.ceil(plan["duration"] * plan["fps"]))
    while True:
        paths = []
        for index in range(4):
            data = await read_png(decoder.stdout)
            if data is None:
                break
            path = incoming / f"{index:04d}.png"
            await asyncio.to_thread(path.write_bytes, data)
            paths.append(path)
        if not paths:
            break
        runner.message = f"AI 超分 · {frames}/{expected} 帧"
        proc, reader, errors = await runner.spawn(
            [
                str(ENGINE_ROOT / "realesrgan-ncnn-vulkan"),
                "-i",
                str(incoming),
                "-o",
                str(outgoing),
                "-m",
                str(ENGINE_ROOT / "models"),
                "-n",
                name,
                "-s",
                str(scale),
                "-t",
                "128",
                "-j",
                "1:1:1",
                "-g",
                "0",
                "-f",
                "png",
            ]
        )
        # The CLI may emit informational output; never leave a pipe undrained.
        await proc.stdout.read()
        await proc.wait()
        await reader
        if proc.returncode:
            raise ValueError("AI 超分处理失败：" + "".join(errors)[-800:])
        for path in paths:
            enhanced = outgoing / path.name
            if not enhanced.is_file():
                raise ValueError("AI 模型未生成有效画面")
            with enhanced.open("rb") as file:
                while chunk := file.read(256 * 1024):
                    encoder.stdin.write(chunk)
                    await encoder.stdin.drain()
            enhanced.unlink()
            path.unlink()
            frames += 1
        runner.progress = max(runner.progress, 0.08 + 0.82 * min(1, frames / expected))
    await decoder.wait()
    await decoder_reader
    if decoder.returncode:
        raise ValueError("视频解码失败：" + "".join(decoder_errors)[-800:])
    encoder.stdin.close()
    await encoder.wait()
    await encoder_reader
    await drain
    if encoder.returncode:
        raise ValueError("增强视频编码失败：" + "".join(encoder_errors)[-800:])
    if frames < max(1, expected - 2):
        raise ValueError("增强结果帧数不足")
    runner.progress = 0.93


def validate_result(raw, plan):
    info = parse_probe_json(raw)
    videos = [s for s in raw.get("streams", []) if s.get("codec_type") == "video"]
    if not videos or info.video_codec != "h264":
        raise ValueError("增强结果缺少有效 H.264 画面")
    if (info.width, info.height) != (plan["width"], plan["height"]):
        raise ValueError("增强结果分辨率与目标不符")
    if abs((info.fps or 0) - plan["fps"]) > 0.02:
        raise ValueError("增强结果帧率与目标不符")
    if abs((info.duration_ms or 0) / 1000 - plan["duration"]) > max(0.2, 2 / plan["fps"]):
        raise ValueError("增强结果时长异常，已停止发布")
    for kind, expected in [
        ("audio", plan["audio_count"]),
        ("subtitle", len(plan["subtitle_indices"])),
    ]:
        if len([s for s in raw.get("streams", []) if s.get("codec_type") == kind]) != expected:
            raise ValueError("增强结果音轨或字幕轨不完整")
    video_duration = float(videos[0].get("duration", 0))
    if abs(video_duration - plan["duration"]) > max(0.05, 1.1 / plan["fps"]):
        raise ValueError("增强结果片尾不完整，已停止发布")
    frames = int(videos[0].get("nb_frames", 0))
    if abs(frames - plan["duration"] * plan["fps"]) > 1.1:
        raise ValueError("增强结果帧数与目标不符")
    return info


async def _process(job_id, body, source, title, plan, source_fingerprint, runner):
    partial = output_path(job_id, "partial.mp4")
    hardware = body.encoder == "auto" and await hardware_available()
    runner.message = (
        "运动补帧与画质增强中" if body.fps and body.frame_method == "motion" else "正在增强视频"
    )
    if body.method == "ai":
        if not ai_available(body.ai_model):
            raise ValueError("本地 AI 超分模型尚未就绪")
        await run_ai(runner, source, partial, body, plan, hardware)
    else:
        await runner.run(enhancement_command(source, partial, body, plan, hardware), 0.02, 0.9)
    if body.preview:
        runner.message = "正在生成原片对比预览"
        original_plan = {
            **plan,
            "width": plan["source_width"],
            "height": plan["source_height"],
            "fps": plan["source_fps"],
            "rate": str(plan["source_fps"]),
            "subtitle_indices": [],
        }
        original_body = EnhanceRequest(encoder="software")
        await runner.run(
            enhancement_command(
                source, output_path(job_id, "original.mp4"), original_body, original_plan, False
            ),
            0.9,
            0.97,
        )
    runner.message = "正在校验分辨率、帧率和音画时长"
    raw = await _run_ffprobe(str(partial))
    info = validate_result(raw, plan)
    if fingerprint(source) != source_fingerprint:
        raise ValueError("处理中原视频发生变化，请重新提交")
    await runner.check()
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", title)[:60].strip(". ") or "video"
    name = f"{safe_title}-{'preview' if body.preview else 'enhanced'}-{job_id}.mp4"
    result = {
        "filename": name,
        "format": "mp4",
        "preview": body.preview,
        "width": info.width,
        "height": info.height,
        "fps": info.fps,
        "duration_ms": info.duration_ms,
        "size_bytes": partial.stat().st_size,
        "bitrate_bps": info.bitrate_bps,
        "target_bitrate_mbps": body.bitrate_mbps if body.quality == "bitrate" else None,
        "encoder": "h264_vaapi" if hardware else "libx264",
        "method": body.method,
        "frame_method": body.frame_method,
        "elapsed_seconds": round(time.monotonic() - runner.started, 2),
        "preview_start": plan["start"],
        "source_duration": plan["full_duration"],
        "skipped_subtitles": plan["skipped_subtitles"],
        "options": body.model_dump(),
    }
    move_without_replace(partial, output_path(job_id))
    async with SessionLocal() as session:
        done = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.state == JobState.running)
            .values(
                state=JobState.done,
                progress=1,
                message="预览已就绪" if body.preview else "增强完成，可下载或加入影像库",
                result=result,
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        if not done.rowcount:
            output_path(job_id).unlink(missing_ok=True)
            raise EnhancementCanceled()
    return result


async def run_enhancement(ctx, job_id):
    runner = None
    watcher = operation = None
    completed = False
    claimed = False
    try:
        async with _slots:
            async with SessionLocal() as session:
                changed = await session.execute(
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
                if not changed.rowcount:
                    return {}
                claimed = True
                job = await session.get(Job, job_id)
                body = EnhanceRequest.model_validate(job.payload["options"])
                video = await session.get(Video, job.video_id)
                if not video:
                    raise ValueError("视频记录不存在")
                _ref, source = await local_source(session, video.id, body.source_id)
                expected = job.payload["fingerprint"]
                if fingerprint(source) != expected:
                    raise ValueError("提交任务后视频已变化，请重新提交")
                title = video.title
            plan = plan_enhancement(body, await _run_ffprobe(str(source)))
            directory = output_path(job_id).parent
            directory.mkdir(parents=True, exist_ok=True)
            runner = Runner(job_id, directory, plan["duration"])
            await runner.check()
            watcher = asyncio.create_task(runner.watch())
            operation = asyncio.create_task(
                _process(job_id, body, source, title, plan, expected, runner)
            )
            done, _ = await asyncio.wait({watcher, operation}, return_when=asyncio.FIRST_COMPLETED)
            if operation not in done:
                await watcher
            result = await operation
            completed = True
            return result
    except EnhancementCanceled:
        return {"canceled": True}
    except BaseException as exc:
        message = (
            "处理进程中断，请重新提交"
            if isinstance(exc, asyncio.CancelledError)
            else (str(exc.detail) if isinstance(exc, HTTPException) else str(exc))
        )
        async with SessionLocal() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state.in_([JobState.running, JobState.queued]))
                .values(
                    state=JobState.error,
                    error=message[:2000],
                    message="增强处理失败",
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return {"error": message}
    finally:
        for task in [watcher, operation]:
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in [watcher, operation] if t), return_exceptions=True)
        if runner:
            await runner.close()
            for name in ["ai-in", "ai-out"]:
                shutil.rmtree(runner.directory / name, ignore_errors=True)
        if claimed and not completed:
            for name in ["partial.mp4", "result.mp4", "original.mp4"]:
                output_path(job_id, name).unlink(missing_ok=True)
