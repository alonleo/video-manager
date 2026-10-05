"""媒体探测：调用 ffprobe 解析元数据，并抽取封面/缩略图。

设计要点（计划书 §7.1）：probe 只读文件头，不整片读取，因此在 NAS/SMB
或网盘 raw_url 上开销都很小。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from app.config import settings

log = logging.getLogger(__name__)

# ffprobe 超时：网盘慢链接需要更宽松
PROBE_TIMEOUT = 60
COVER_TIMEOUT = 120


@dataclass
class ProbeResult:
    """归一化后的媒体信息，直接映射到 videos 表字段。"""

    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    video_profile: str | None = None
    pix_fmt: str | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    container: str | None = None
    bitrate_bps: int | None = None
    size_bytes: int | None = None
    raw: dict = field(default_factory=dict)

    @property
    def has_video(self) -> bool:
        return self.video_codec is not None


def _parse_fps(rate: str | None) -> float | None:
    """把 ffprobe 的 '30000/1001' 形式转成 float。"""
    if not rate or rate == "0/0":
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else None
        return round(float(rate), 3)
    except (ValueError, ZeroDivisionError):
        return None


def _parse_duration_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(float(value) * 1000)
    except (ValueError, TypeError):
        return None


def parse_probe_json(data: dict) -> ProbeResult:
    """从 ffprobe JSON 输出提取我们关心的字段。"""
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []

    video_stream = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    result = ProbeResult(raw=data)

    # 容器格式：ffprobe 的 format_name 可能是 "matroska,webm" 这种复合值
    fmt_name = fmt.get("format_name")
    if fmt_name:
        result.container = fmt_name.split(",")[0].strip()

    result.duration_ms = _parse_duration_ms(fmt.get("duration"))
    if fmt.get("bit_rate"):
        try:
            result.bitrate_bps = int(fmt["bit_rate"])
        except (ValueError, TypeError):
            pass
    if fmt.get("size"):
        try:
            result.size_bytes = int(fmt["size"])
        except (ValueError, TypeError):
            pass

    if video_stream:
        result.video_codec = video_stream.get("codec_name")
        result.video_profile = video_stream.get("profile")
        result.pix_fmt = video_stream.get("pix_fmt")
        result.width = video_stream.get("width")
        result.height = video_stream.get("height")
        result.fps = _parse_fps(
            video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
        )
        # 部分容器（如 MKV）format 段没有 duration，回退到流上的
        if result.duration_ms is None:
            result.duration_ms = _parse_duration_ms(
                video_stream.get("duration") or video_stream.get("tags", {}).get("DURATION")
            )

    if audio_stream:
        result.audio_codec = audio_stream.get("codec_name")
        result.audio_channels = audio_stream.get("channels")

    return result


async def _run_ffprobe(input_uri: str) -> dict:
    """执行 ffprobe。input_uri 可以是本地路径，也可以是 http(s) URL。"""
    cmd = [
        settings.ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-i",
        input_uri,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT)
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
        await proc.communicate()
        raise
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"ffprobe 超时（{PROBE_TIMEOUT}s）: {input_uri}") from None

    if proc.returncode != 0:
        msg = stderr.decode("utf-8", "replace").strip()[:500]
        raise RuntimeError(f"ffprobe 失败（code={proc.returncode}）: {msg}")

    return json.loads(stdout.decode("utf-8", "replace"))


async def probe(input_uri: str) -> ProbeResult:
    """探测媒体信息。失败时抛异常，由调用方决定如何标记。"""
    data = await _run_ffprobe(input_uri)
    result = parse_probe_json(data)
    if not result.has_video:
        raise ValueError(f"文件中没有视频流: {input_uri}")
    return result


# ------------------------------------------------------------------ 封面


async def extract_thumbnails(
    input_uri: str,
    video_id: int | None,
    duration_ms: int | None,
    *,
    cover_width: int = 640,
    thumb_width: int = 320,
) -> tuple[str | None, str | None]:
    """Generate a fresh pair of images; publish their paths only after success.

    Downloads may prepare images before a video ID is allocated. A unique name
    prevents recycled SQLite IDs, failed seeks and concurrent jobs reusing old images.
    """
    covers_dir = settings.data_dir / "covers"
    thumbs_dir = settings.data_dir / "thumbs"
    covers_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{video_id if video_id is not None else 'new'}-{uuid4().hex}"
    cover_abs = covers_dir / f"{stem}.jpg"
    thumb_abs = thumbs_dir / f"{stem}.jpg"

    async def _run(cmd: list[str]) -> bool:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=COVER_TIMEOUT)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return False
        if proc.returncode != 0:
            log.debug("抽封面失败: %s", stderr.decode("utf-8", "replace")[:200])
        return proc.returncode == 0

    complete = False
    try:
        seek_s = round(duration_ms / 1000 * 0.1, 2) if duration_ms and duration_ms > 10_000 else 1.0
        for seek in dict.fromkeys((seek_s, 1.0, 0.0)):
            # ffmpeg can succeed without producing a frame when seeking past EOF.
            cover_abs.unlink(missing_ok=True)
            ok = await _run([
                settings.ffmpeg_bin, "-nostdin", "-v", "error",
                "-ss", str(seek), "-i", input_uri, "-frames:v", "1",
                "-vf", f"scale={cover_width}:-2", "-q:v", "3", "-y", str(cover_abs),
            ])
            if ok and cover_abs.exists() and cover_abs.stat().st_size > 0:
                break
        else:
            return None, None

        ok = await _run([
            settings.ffmpeg_bin, "-nostdin", "-v", "error",
            "-i", str(cover_abs), "-frames:v", "1",
            "-vf", f"scale={thumb_width}:-2", "-q:v", "5", "-y", str(thumb_abs),
        ])
        thumb_valid = ok and thumb_abs.exists() and thumb_abs.stat().st_size > 0
        if not thumb_valid:
            thumb_abs.unlink(missing_ok=True)
        complete = True
        return f"covers/{stem}.jpg", f"thumbs/{stem}.jpg" if thumb_valid else None
    finally:
        if not complete:
            cover_abs.unlink(missing_ok=True)
            thumb_abs.unlink(missing_ok=True)

def delete_thumbnails(video_id: int) -> None:
    for sub in ("covers", "thumbs"):
        p = settings.data_dir / sub / f"{video_id}.jpg"
        p.unlink(missing_ok=True)
        for version in p.parent.glob(f"{video_id}-*.jpg"):
            version.unlink(missing_ok=True)


# ------------------------------------------------------------------ 音频抽取


async def extract_audio(input_uri: str, out_name: str, *, timeout: int = 7200) -> Path:
    """抽取 16kHz 单声道 FLAC 供 ASR 使用（计划书 §6.1）。

    输入可以是网盘 raw_url —— ffmpeg 直接按 Range 读取，视频不落盘。
    用完由调用方删除。
    """
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.work_dir / out_name
    cmd = [
        settings.ffmpeg_bin,
        "-nostdin",
        "-v",
        "error",
        "-i",
        input_uri,
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "flac",
        "-y",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        out_path.unlink(missing_ok=True)
        raise TimeoutError(f"抽取音频超时（{timeout}s）") from None

    if proc.returncode != 0:
        out_path.unlink(missing_ok=True)
        msg = stderr.decode("utf-8", "replace").strip()[:500]
        raise RuntimeError(f"抽取音频失败: {msg}")
    return out_path
