"""On-demand, seekable playback cache for every local video format. Originals are never modified.

Preparation is serialized (also across API processes via flock), cancellable,
size/time limited, and committed atomically only after ffprobe validation.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.config import settings
from app.services.playback import can_copy_h264
from app.services.probe import probe

log = logging.getLogger(__name__)
TS_CONTAINERS = {"mpegts", "mpegtsraw", "ts", "m2ts", "mts"}
TS_EXTENSIONS = {".ts", ".m2ts", ".mts"}


def is_transport_stream(container: str | None, source: Path) -> bool:
    return (container or "").lower() in TS_CONTAINERS or source.suffix.lower() in TS_EXTENSIONS


@dataclass(frozen=True)
class MediaSpec:
    source: Path
    size: int
    mtime_ns: int
    mode: str
    audio_codec: str | None
    duration_ms: int | None
    audio_profile: str | None = None

    @classmethod
    def from_path(cls, path: Path, mode: str, audio_codec: str | None, duration_ms: int | None):
        path = path.resolve(strict=True)
        if not path.is_file():
            raise ValueError("视频来源不是普通文件")
        st = path.stat()
        return cls(path, st.st_size, st.st_mtime_ns, mode, audio_codec, duration_ms)

    @property
    def key(self) -> str:
        # Include encoding policy version; changing the source invalidates its cache.
        identity = f"v1|{self.source}|{self.size}|{self.mtime_ns}|{self.mode}|{self.audio_codec}"
        return hashlib.sha256(identity.encode()).hexdigest()

    def unchanged(self) -> bool:
        try:
            st = self.source.stat()
            return (st.st_size, st.st_mtime_ns) == (self.size, self.mtime_ns)
        except OSError:
            return False


@dataclass
class Preparation:
    key: str
    state: str = "queued"
    progress: float = 0.0
    error: str | None = None
    path: Path | None = None
    touched: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = field(default=None, repr=False)


def ffmpeg_command(spec: MediaSpec, output: Path, max_bytes: int) -> list[str]:
    cmd = [
        settings.ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-y",
        "-fflags",
        "+genpts",
        "-i",
        str(spec.source),
        "-map",
        "0:V:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
    ]
    if spec.mode == "remux":
        cmd += ["-c:v", "copy"]
    else:
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-threads",
            "2",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-pix_fmt",
            "yuv420p",
        ]
    if (
        spec.audio_codec == "aac"
        and spec.audio_profile in {None, "LC"}
        and spec.mode != "transcode"
    ):
        cmd += ["-c:a", "copy", "-bsf:a", "aac_adtstoasc"]
    else:
        # AC3/MP2/DTS etc. are decoded by ffmpeg, not handed to the browser.
        cmd += ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ac", "2", "-ar", "48000"]
    cmd += [
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        "-fs",
        str(max_bytes),
        "-progress",
        "pipe:1",
        "-nostats",
        str(output),
    ]
    return cmd


class PlaybackCache:
    def __init__(self, cache_root: Path | None = None):
        self.root = cache_root or settings.cache_dir
        self.directory = self.root / "ts-playback-v1"
        self.jobs: dict[str, Preparation] = {}
        self._guard = asyncio.Lock()
        self._serial = asyncio.Lock()
        self._leases: dict[str, int] = {}

    async def ensure(self, spec: MediaSpec, *, retry: bool = False) -> Preparation:
        async with self._guard:
            self.directory.mkdir(parents=True, exist_ok=True)
            output = self.directory / f"{spec.key}.mp4"
            if output.is_file() and output.stat().st_size > 0:
                (self.directory / f"{spec.key}.used").touch()
                return Preparation(spec.key, state="ready", progress=1, path=output)
            now = time.monotonic()
            self.jobs = {
                k: j
                for k, j in self.jobs.items()
                if j.task and (not j.task.done() or now - j.touched < 3600)
            }
            job = self.jobs.get(spec.key)
            if job and (
                job.state in {"queued", "preparing"} or (job.state == "error" and not retry)
            ):
                job.touched = now
                return job
            if len([j for j in self.jobs.values() if j.state in {"queued", "preparing"}]) >= 32:
                return Preparation(spec.key, state="error", error="准备队列已满，请稍后重试")
            job = Preparation(spec.key)
            self.jobs[spec.key] = job
            job.task = asyncio.create_task(
                self._prepare(spec, job), name=f"playback-{spec.key[:12]}"
            )
            return job

    @asynccontextmanager
    async def _disk_lock(self):
        with (self.directory / ".prepare.lock").open("a") as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.2)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _usage(self) -> int:
        total = 0
        for p in self.root.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except FileNotFoundError:
                pass
        return total

    def _make_room(self, spec: MediaSpec) -> int:
        # Only files owned by this feature may be evicted; other cache usage counts too.
        cap = int(settings.cache_max_gb * 1024**3)
        reserve = settings.playback_min_free_mb * 1024**2
        estimate = int(spec.size * 1.15) + 2 * 1024**2

        def last_used(path: Path) -> float:
            marker = path.with_suffix(".used")
            return (marker if marker.is_file() else path).stat().st_mtime

        candidates = sorted(self.directory.glob("*.mp4"), key=last_used)
        for p in candidates:
            if (
                cap - self._usage() >= estimate
                and shutil.disk_usage(self.root).free - reserve >= estimate
            ):
                break
            recently_used = time.time() - last_used(p) < settings.hls_session_ttl_min * 60
            if not recently_used and not self._leases.get(p.stem) and not p.is_symlink():
                p.unlink(missing_ok=True)
                p.with_suffix(".used").unlink(missing_ok=True)
        available = min(cap - self._usage(), shutil.disk_usage(self.root).free - reserve)
        if available < 1024**2:
            raise RuntimeError("播放缓存空间不足。请清理缓存或调整 CACHE_MAX_GB 后重试")
        return available

    async def _prepare(self, spec: MediaSpec, job: Preparation) -> None:
        output = self.directory / f"{spec.key}.mp4"
        partial = self.directory / f"{spec.key}.{uuid.uuid4().hex}.part.mp4"
        try:
            async with self._serial, self._disk_lock():
                if not spec.unchanged():
                    raise RuntimeError("源文件已变化或已离线，请重新打开视频")
                if output.is_file() and output.stat().st_size > 0:
                    job.state, job.path, job.progress = "ready", output, 1
                    return
                # This cross-process lock guarantees that these are abandoned intermediates.
                for orphan in self.directory.glob("*.part.mp4"):
                    orphan.unlink(missing_ok=True)
                allowance = await asyncio.to_thread(self._make_room, spec)
                job.state = "preparing"
                # Re-probe the source: old library metadata may be incomplete or stale.
                original = await probe(str(spec.source))
                audio = next(
                    (s for s in original.raw.get("streams", []) if s.get("codec_type") == "audio"),
                    {},
                )
                mode = spec.mode
                if mode == "remux" and not can_copy_h264(
                    original.video_codec, original.video_profile, original.pix_fmt
                ):
                    mode = "transcode"
                conversion = replace(
                    spec,
                    mode=mode,
                    audio_codec=original.audio_codec,
                    audio_profile=audio.get("profile"),
                    duration_ms=original.duration_ms,
                )
                await self._convert(conversion, job, partial, allowance)
                actual = await probe(str(partial))
                if (
                    actual.video_codec != "h264"
                    or not actual.size_bytes
                    or actual.audio_codec not in {None, "aac"}
                    or not actual.duration_ms
                ):
                    raise RuntimeError("生成的播放文件未通过媒体验证")
                if (
                    conversion.duration_ms
                    and (actual.duration_ms or 0) < conversion.duration_ms * 0.97 - 1500
                ):
                    raise RuntimeError("转换结果不完整，可能已达到缓存容量限制")
                if partial.stat().st_size >= allowance * 0.99:
                    raise RuntimeError("播放文件达到缓存容量限制，请调整 CACHE_MAX_GB 后重试")
                if not spec.unchanged():
                    raise RuntimeError("源文件在准备过程中发生变化，请重新打开视频")
                partial.replace(output)
                (self.directory / f"{spec.key}.used").touch()
                job.path, job.progress, job.state = output, 1, "ready"
        except asyncio.CancelledError:
            job.state, job.error = "error", "播放准备已中止，请重试"
            raise
        except Exception as exc:
            message = str(exc)
            if message.startswith(("ffprobe ", "文件中没有视频流:")):
                message = "无法读取此视频。文件可能已损坏、仍在写入，或没有可解码的视频轨道。"
            job.state, job.error = "error", message[:600]
            log.warning("Playback preparation failed %s: %s", spec.source, exc)
        finally:
            partial.unlink(missing_ok=True)
            job.touched = time.monotonic()

    async def _convert(self, spec: MediaSpec, job: Preparation, output: Path, limit: int):
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_command(spec, output, limit),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        error_tail = bytearray()

        async def stderr_reader():
            while chunk := await proc.stderr.read(4096):
                error_tail.extend(chunk)
                if len(error_tail) > 8192:
                    del error_tail[:-8192]

        async def progress_reader():
            while line := await proc.stdout.readline():
                if line.startswith(b"out_time_us=") and spec.duration_ms:
                    try:
                        elapsed_us = int(line.split(b"=", 1)[1])
                        job.progress = max(
                            job.progress, min(0.98, elapsed_us / (spec.duration_ms * 1000))
                        )
                    except ValueError:
                        pass

        readers = [asyncio.create_task(stderr_reader()), asyncio.create_task(progress_reader())]
        try:
            async with asyncio.timeout(settings.playback_prepare_timeout_sec):
                while proc.returncode is None:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1)
                    except TimeoutError:
                        free = await asyncio.to_thread(lambda: shutil.disk_usage(self.root).free)
                        if free < settings.playback_min_free_mb * 1024**2:
                            raise RuntimeError("磁盘剩余空间不足，已停止准备以保护原文件") from None
                await asyncio.gather(*readers)
            if proc.returncode:
                message = error_tail.decode("utf-8", "replace").strip()[-400:]
                log.warning("ffmpeg could not prepare %s: %s", spec.source, message)
                raise RuntimeError(
                    "无法读取此视频。文件可能已损坏、仍在写入，或包含不支持的音视频编码。"
                )
            if not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError("未生成有效的播放文件")
        except TimeoutError:
            raise RuntimeError(
                "视频准备超时，请检查文件或提高 PLAYBACK_PREPARE_TIMEOUT_SEC"
            ) from None
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    def hold(self, key: str) -> None:
        self._leases[key] = self._leases.get(key, 0) + 1

    def release(self, key: str) -> None:
        count = self._leases.get(key, 0) - 1
        if count > 0:
            self._leases[key] = count
        else:
            self._leases.pop(key, None)

    async def close(self) -> None:
        tasks = [j.task for j in self.jobs.values() if j.task and not j.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


cache = PlaybackCache()
