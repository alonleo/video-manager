"""Isolated yt-dlp subprocesses, cancellable progress and atomic library import."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import shutil
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import Job, JobState, StorageType, Video, VideoSource
from app.services import dedup, probe
from app.services.directory_categories import (
    directory_path,
    get_local_storage,
    library_write_lock,
    storage_root,
    transfer_file,
)
from app.services.download_config import DownloadConfig, read_config, validate_public_host
from app.services.download_cookies import browser_spec, download_environment
from app.services.library import _summarize_meta

log = logging.getLogger(__name__)

_slots = asyncio.Semaphore(settings.download_max_jobs)


def workspace(payload: dict) -> Path:
    token = payload.get("download_token", "")
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise ValueError("下载任务标识无效")
    return settings.work_dir / "url-downloads" / token


def command(payload: dict, config: DownloadConfig, stage: Path) -> list[str]:
    cap = "" if payload["quality"] == "best" else f"[height<=?{payload['quality']}]"
    fmt = f"bv*{cap}+ba/b{cap}"
    args = [
        sys.executable,
        "-m",
        "app.services.downloader_cli",
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-playlist",
        "--playlist-end",
        "1",
        "--no-simulate",
        "--newline",
        "--progress",
        "--no-colors",
        "--socket-timeout",
        "25",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--concurrent-fragments",
        "2",
        "--continue",
        "--no-overwrites",
        "--no-mtime",
        "--match-filters",
        "!is_live",
        "--max-filesize",
        f"{settings.export_max_file_mb}M",
        "--ffmpeg-location",
        shutil.which(settings.ffmpeg_bin) or settings.ffmpeg_bin,
        "-f",
        fmt,
        "-P",
        str(stage),
        "-o",
        "video.%(ext)s",
        "--print",
        'before_dl:VM_META:{"title":%(title)j,"id":%(id)j,"extractor":%(extractor_key)j}',
        "--print",
        "after_move:VM_FILE:%(filepath)j",
        "--progress-template",
        'download:VM_PROGRESS:{"downloaded":%(progress.downloaded_bytes)j,'
        '"total":%(progress.total_bytes,progress.total_bytes_estimate)j,'
        '"speed":%(progress.speed)j,"eta":%(progress.eta)j}',
    ]
    if payload["container"] != "auto":
        args += [
            "--merge-output-format",
            payload["container"],
            "--remux-video",
            payload["container"],
        ]
    if node := shutil.which("node"):
        args += ["--js-runtimes", f"node:{node}"]
    if config.proxy:
        args += ["--proxy", config.proxy]
    if config.cookie_file:
        # Work on a private copy: yt-dlp may update its cookie jar on exit.
        args += ["--cookies", str(stage / "cookies.txt")]
    elif config.cookies_browser:
        args += ["--cookies-from-browser", browser_spec(config)]
    return args + ["--", payload["url"]]


def error_message(lines: list[str]) -> str:
    raw = " ".join(lines)[-3000:]
    lower = raw.lower()
    if "vm_douyin_browser_no_video" in lower:
        return (
            "抖音浏览器解析未取得视频详情。请在所选浏览器确认该视频能播放，完成登录或验证后重试。"
        )
    if "vm_douyin_browser_unavailable" in lower:
        return (
            "抖音解析浏览器未就绪。请在后端电脑安装 Chrome，"
            "或运行 uv run playwright install chromium。"
        )
    if any(
        s in lower
        for s in (
            "cannot decrypt",
            "failed to decrypt",
            "no key found",
            "secretstorage",
            "keyring",
            "kwallet",
            "dbus",
            "could not copy chrome cookie",
        )
    ):
        return (
            "浏览器 Cookie 读取或解密失败。请确认后端与浏览器使用同一系统用户，"
            "在下载设置中选择正确的配置目录和 Linux 密钥环，并解锁桌面密钥环；"
            "也可改用导出的 cookies.txt。"
        )
    if "could not find" in lower and any(s in lower for s in ("cookie", "browser", "profile")):
        return "未找到浏览器 Cookie 数据库，请检查后端电脑上的浏览器和配置目录。"
    if "douyin" in lower and "fresh cookies" in lower:
        return (
            "抖音详情接口未返回视频数据，可能是接口变化或站点验证，"
            "并不一定是未登录。请先在所选浏览器确认该视频能播放，并检测 Cookie 读取情况。"
        )
    if any(s in lower for s in ("sign in", "login", "logged in", "403", "412", "cookie")):
        return (
            "站点要求登录或验证。请在下载设置中配置该站点有效的浏览器 Cookie "
            "或 cookies.txt 后重试；仍失败可更新 yt-dlp。"
        )
    if any(s in lower for s in ("proxy", "connection", "timed out", "network", "resolve")):
        return "连接视频站点失败，请检查网络或下载代理后重试。"
    if "unsupported url" in lower:
        return "暂不支持此链接，请使用单个视频的播放页或分享链接。"
    if any(s in lower for s in ("requested format", "not available")):
        return "所选清晰度或视频暂不可用，请尝试「最佳可用」或检查视频是否公开。"
    if "drm" in lower:
        return "该视频受 DRM 保护，无法下载。"
    # Do not persist signed media URLs, Cookie values or raw extractor output in jobs.
    return "下载未完成。请检查链接是否有效、视频是否可访问，或更新 yt-dlp 后重试。"


def progress_text(data: dict) -> tuple[float, str]:
    def number(value):
        return float(value) if isinstance(value, (int, float)) else 0.0

    done, total = number(data.get("downloaded")), number(data.get("total"))
    ratio = min(0.9, done / total * 0.9) if total > 0 else 0
    text = f"下载中 · {done / 1048576:.1f} MB"
    if total > 0:
        text += f" / {total / 1048576:.1f} MB"
    if (speed := number(data.get("speed"))) > 0:
        text += f" · {speed / 1048576:.1f} MB/s"
    if isinstance(data.get("eta"), (int, float)):
        text += f" · 剩余 {int(data['eta'])} 秒"
    return ratio, text


async def terminate(process):
    if process.returncode is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 3)
    except TimeoutError:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        await process.wait()


async def run_process(payload, config, stage, progress):
    state = {"ratio": 0.01, "message": "正在解析视频链接…", "meta": {}, "file": None}
    errors: list[str] = []
    private_cookie = stage / "cookies.txt"
    process = None
    readers = []
    try:
        if config.cookie_file:
            if not Path(config.cookie_file).is_file():
                raise ValueError("Cookie 文件已不存在，请重新设置")
            await asyncio.to_thread(shutil.copyfile, config.cookie_file, private_cookie)
            private_cookie.chmod(0o600)
        process = await asyncio.create_subprocess_exec(
            *command(payload, config, stage),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=download_environment(),
            limit=1024 * 1024,
        )

        async def consume(stream, stderr=False):
            async for raw in stream:
                line = raw.decode("utf-8", errors="replace").strip()
                if stderr:
                    errors.append(line[:1500])
                    del errors[:-12]
                    continue
                try:
                    if line.startswith("VM_PROGRESS:"):
                        ratio, message = progress_text(json.loads(line[12:]))
                        state.update(ratio=max(state["ratio"], ratio), message=message)
                    elif line.startswith("VM_META:"):
                        state["meta"] = json.loads(line[8:])
                    elif line.startswith("VM_FILE:"):
                        state.update(
                            file=json.loads(line[8:]), ratio=0.93, message="正在校验下载文件…"
                        )
                    elif "正在通过浏览器解析视频" in line:
                        state.update(message="正在通过浏览器解析抖音视频…")
                    elif line.startswith(("[Merger]", "[VideoRemuxer]", "[Fixup")):
                        state.update(ratio=0.92, message="正在合并音视频…")
                except (ValueError, TypeError):
                    continue

        readers = [
            asyncio.create_task(consume(process.stdout)),
            asyncio.create_task(consume(process.stderr, True)),
        ]
        started, last_disk = time.monotonic(), 0
        while process.returncode is None:
            # This also checks cancellation while extraction/ffmpeg emits no output.
            await progress(state["ratio"], 1, state["message"])
            now = time.monotonic()
            if now - started > settings.export_timeout_sec:
                raise ValueError("下载超时，已保留临时文件，可重试续传")
            if now - last_disk > 3:
                last_disk = now
                total = sum(p.stat().st_size for p in stage.iterdir() if p.is_file())
                if total > settings.export_max_file_mb * 1048576:
                    raise ValueError("下载超过单任务容量上限，已停止")
                if shutil.disk_usage(stage).free < settings.playback_min_free_mb * 1048576:
                    raise ValueError("临时目录磁盘空间不足，下载已停止")
            try:
                await asyncio.wait_for(asyncio.shield(readers[0]), 0.5)
            except TimeoutError:
                pass
            # stdout can finish before the child exits; avoid spinning.
            if readers[0].done() and process.returncode is None:
                try:
                    await asyncio.wait_for(asyncio.shield(process.wait()), 0.5)
                except TimeoutError:
                    pass
        await asyncio.gather(*readers)
        if process.returncode or not state["file"]:
            raise ValueError(error_message(errors))
        file = Path(state["file"]).resolve()
        if not file.is_relative_to(stage.resolve()) or not file.is_file() or file.is_symlink():
            raise ValueError("下载引擎未返回有效的视频文件")
        if file.stat().st_size > settings.export_max_file_mb * 1048576:
            raise ValueError("下载超过单任务容量上限")
        return file, state["meta"]
    finally:
        if process:
            await terminate(process)
        for task in readers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        private_cookie.unlink(missing_ok=True)


def filename(meta: dict, suffix: str) -> str:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(meta.get("title") or "视频")).strip(". ")
    identifier = re.sub(r"[^a-zA-Z0-9_-]", "_", str(meta.get("id") or "download"))[:32]
    return f"{title[:60] or '视频'} [{identifier}]{suffix}"


async def publish(payload, job_id, file, meta, progress):
    await progress(0.94, 1, "正在读取视频信息…")
    info = await probe.probe(str(file))
    if not info.has_video:
        raise ValueError("下载结果不包含视频画面，未加入影像库")
    quick_hash = await asyncio.to_thread(dedup.quick_hash_file, file)
    await progress(0.95, 1, "正在生成视频封面…")
    cover, thumb = None, None
    result = None
    try:
        try:
            cover, thumb = await probe.extract_thumbnails(str(file), None, info.duration_ms)
        except Exception:
            log.exception("下载封面生成失败 job=%s", job_id)
        result = await _publish_prepared(payload, job_id, file, meta, progress, info, quick_hash, cover, thumb)
        return result
    finally:
        # Failed/canceled imports and reused downloads never retain unused images.
        if result is None or result.get("reused"):
            for relative in (cover, thumb):
                if relative:
                    (settings.data_dir / relative).unlink(missing_ok=True)


async def _publish_prepared(payload, job_id, file, meta, progress, info, quick_hash, cover, thumb):
    from app.workers.tasks import JobCanceled

    await progress(0.96, 1, "正在加入影像库…")
    async with SessionLocal() as session:
        # Prevent scan/rename from observing half-published files.
        with library_write_lock():
            storage = await get_local_storage(session, payload["storage_id"])
            target = directory_path(storage, payload["path"])
            copy_size = file.stat().st_size if file.stat().st_dev != target.stat().st_dev else 0
            if shutil.disk_usage(target).free < copy_size + settings.playback_min_free_mb * 1048576:
                raise ValueError("保存目录磁盘空间不足")
            output = target / filename(meta, file.suffix)
            if output.exists() or output.is_symlink():
                # A previous job already published this URL to this path: return its library item.
                source = await session.scalar(
                    select(VideoSource).where(
                        VideoSource.local_path == str(output),
                        VideoSource.origin_url == payload["url"],
                        VideoSource.available.is_(True),
                    )
                )
                if (
                    source
                    and output.is_file()
                    and not output.is_symlink()
                    and output.stat().st_size == file.stat().st_size
                    and await asyncio.to_thread(dedup.quick_hash_file, output) == quick_hash
                ):
                    result = {
                        "video_id": source.video_id,
                        "filename": output.name,
                        "local_path": str(output),
                        "reused": True,
                    }
                    changed = await session.execute(
                        update(Job)
                        .where(Job.id == job_id, Job.state == JobState.running)
                        .values(
                            state=JobState.done,
                            progress=1,
                            result=result,
                            message="视频已在影像库中",
                            finished_at=datetime.now(UTC),
                        )
                    )
                    if not changed.rowcount:
                        raise JobCanceled()
                    await session.commit()
                    return result
                raise ValueError("保存目录已有同名文件，未覆盖；请改用其他分类")
            copied, _ = await asyncio.to_thread(transfer_file, file, output)
            try:
                dup = (
                    await session.scalar(
                        select(VideoSource).where(VideoSource.quick_hash == quick_hash).limit(1)
                    )
                    if quick_hash
                    else None
                )
                video = await session.get(Video, dup.video_id) if dup else None
                created = video is None
                if created:
                    fields = {
                        key: getattr(info, key)
                        for key in (
                            "duration_ms",
                            "width",
                            "height",
                            "fps",
                            "video_codec",
                            "video_profile",
                            "pix_fmt",
                            "audio_codec",
                            "audio_channels",
                            "container",
                            "bitrate_bps",
                        )
                    }
                    video = Video(
                        title=str(meta.get("title") or output.stem),
                        status="indexed",
                        source_kind="local",
                        size_bytes=output.stat().st_size,
                        meta=_summarize_meta(info.raw),
                        **fields,
                    )
                    session.add(video)
                    await session.flush()
                session.add(
                    VideoSource(
                        video_id=video.id,
                        storage_id=storage.id,
                        source_type=StorageType.local,
                        uri=f"local:{output.relative_to(storage_root(storage)).as_posix()}",
                        local_path=str(output),
                        origin_url=payload["url"],
                        extractor=str(meta.get("extractor") or "Generic"),
                        provider_file_id=str(meta.get("id") or ""),
                        quick_hash=quick_hash,
                        size_bytes=output.stat().st_size,
                        duration_ms=info.duration_ms,
                        is_primary=created,
                        available=True,
                        last_seen_at=datetime.now(UTC),
                    )
                )
                if cover:
                    video.cover_path, video.thumb_path = cover, thumb
                result = {
                    "video_id": video.id,
                    "filename": output.name,
                    "local_path": str(output),
                    "title": video.title,
                }
                # Terminal state and import are committed together; cancellation cannot win halfway.
                changed = await session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.state == JobState.running)
                    .values(
                        state=JobState.done,
                        result=result,
                        progress=1,
                        message="下载完成，已加入影像库",
                        finished_at=datetime.now(UTC),
                    )
                )
                if not changed.rowcount:
                    raise JobCanceled()
                await session.commit()
            except BaseException:
                await session.rollback()
                if copied:
                    output.unlink(missing_ok=True)
                elif output.exists():
                    await asyncio.to_thread(transfer_file, output, file)
                raise
    return result


async def download(payload, job_id, progress):
    while _slots.locked():
        await progress(0, 1, "等待下载空位…")
        await asyncio.sleep(0.5)
    async with _slots:
        await progress(0, 1, "准备下载…")
        await validate_public_host(payload["url"])
        async with SessionLocal() as session:
            storage = await get_local_storage(session, payload["storage_id"])
            directory_path(storage, payload["path"])
            config = await read_config(session)
        stage = workspace(payload)
        stage.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage.chmod(0o700)
        with (stage / ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("同一下载的旧进程尚未退出，请稍后重试") from exc
            try:
                file, meta = await run_process(payload, config, stage, progress)
                result = await publish(payload, job_id, file, meta, progress)
            except HTTPException as exc:
                raise ValueError(str(exc.detail)) from exc
            else:
                await asyncio.to_thread(shutil.rmtree, stage, True)
                return result
