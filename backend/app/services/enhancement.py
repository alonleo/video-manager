"""Validated video enhancement settings, dimensions and encoder commands."""

from __future__ import annotations

import asyncio
import math
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import PROJECT_ROOT, settings
from app.services.probe import parse_probe_json

ENGINE_ROOT = PROJECT_ROOT / "tools" / "realesrgan"
MODELS = {
    "general": ("realesrgan-x4plus", 4, "通用影像"),
    "anime": ("realesr-animevideov3", 2, "动漫 / 动画"),
}


class EnhanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: int | None = Field(default=None, gt=0)
    resolution: Literal["source", "1080", "1440", "2160", "2x"] = "source"
    method: Literal["standard", "ai"] = "standard"
    ai_model: Literal["general", "anime"] = "general"
    fps: Literal[24, 25, 30, 48, 50, 60, 120] | None = None
    frame_method: Literal["motion", "duplicate"] = "motion"
    quality: Literal["quality", "bitrate"] = "quality"
    bitrate_mbps: float = Field(default=12, ge=1, le=150, allow_inf_nan=False)
    encoder: Literal["auto", "software"] = "auto"
    preview: bool = False
    preview_start: float = Field(default=0, ge=0, allow_inf_nan=False)
    keep_subtitles: bool = True

    @model_validator(mode="after")
    def valid_options(self):
        if self.method == "ai" and self.resolution == "source":
            raise ValueError("AI 超分请选择更高的分辨率或 2 倍放大")
        return self


def output_path(job_id: int, name="result.mp4") -> Path:
    if job_id <= 0 or name not in {"partial.mp4", "result.mp4", "original.mp4"}:
        raise ValueError("增强结果路径无效")
    return settings.downloads_dir / "enhanced" / f"job-{job_id}" / name


def model_size(model: str) -> int:
    name = MODELS[model][0] + ("-x2" if model == "anime" else "")
    return sum(
        p.stat().st_size
        for ext in [".param", ".bin"]
        if (p := ENGINE_ROOT / "models" / (name + ext)).is_file()
    )


def ai_available(model: str) -> bool:
    name = MODELS[model][0] + ("-x2" if model == "anime" else "")
    return (ENGINE_ROOT / "realesrgan-ncnn-vulkan").is_file() and all(
        (ENGINE_ROOT / "models" / (name + ext)).is_file() for ext in [".param", ".bin"]
    )


async def hardware_available() -> bool:
    if not Path(settings.vaapi_device).exists():
        return False
    proc = await asyncio.create_subprocess_exec(
        settings.ffmpeg_bin,
        "-nostdin",
        "-v",
        "error",
        "-init_hw_device",
        f"vaapi=enhance:{settings.vaapi_device}",
        "-filter_hw_device",
        "enhance",
        "-f",
        "lavfi",
        "-i",
        "color=size=64x64:rate=25",
        "-vf",
        "format=nv12,hwupload",
        "-frames:v",
        "2",
        "-c:v",
        "h264_vaapi",
        "-rc_mode",
        "ICQ",
        "-global_quality",
        "20",
        "-f",
        "null",
        "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), 8)
        return proc.returncode == 0
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    except TimeoutError:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        return False


def plan_enhancement(body: EnhanceRequest, raw: dict) -> dict:
    video = next(
        (
            s
            for s in raw.get("streams", [])
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if not video:
        raise ValueError("来源没有可处理的视频画面")
    info = parse_probe_json(raw)
    duration = (info.duration_ms or 0) / 1000
    if duration <= 0 or not math.isfinite(duration):
        raise ValueError("无法确定视频时长，请先检查视频文件")
    if video.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
        raise ValueError("当前增强用于 SDR 视频，HDR 视频请先转换为 SDR，避免色彩失真")
    width, height = int(video["width"]), int(video["height"])
    try:
        sar = float(Fraction(video.get("sample_aspect_ratio", "1:1").replace(":", "/")))
    except (ValueError, ZeroDivisionError):
        sar = 1.0
    if not 0.1 <= sar <= 10:
        raise ValueError("视频像素比例异常")
    width = max(2, round(width * sar / 2) * 2)
    rotation = next(
        (float(s["rotation"]) for s in video.get("side_data_list", []) if "rotation" in s),
        float(video.get("tags", {}).get("rotate", 0)),
    )
    if abs(round(rotation)) % 180 == 90:
        width, height = height, width
    width, height = max(2, width // 2 * 2), max(2, height // 2 * 2)
    if width * height > 3840 * 2160:
        raise ValueError("当前增强支持最高 4K 的输入，请先降低源视频分辨率")
    out_w, out_h = width, height
    if body.resolution == "2x":
        out_w, out_h = width * 2, height * 2
    elif body.resolution != "source":
        short = int(body.resolution)
        box_w, box_h = round(short * 16 / 9), short
        if height > width:
            box_w, box_h = box_h, box_w
        ratio = min(box_w / width, box_h / height)
        out_w, out_h = max(2, round(width * ratio / 2) * 2), max(2, round(height * ratio / 2) * 2)
    if max(out_w, out_h) > 4096 or out_w * out_h > 3840 * 2160:
        raise ValueError("增强结果最高支持 4K，请选择较低的目标分辨率")
    if body.method == "ai" and out_w <= width and out_h <= height:
        raise ValueError("AI 超分的目标分辨率需高于原视频")
    try:
        source_rate = Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "25")
        if source_rate <= 0:
            source_rate = Fraction(video.get("r_frame_rate") or "25")
    except (ValueError, ZeroDivisionError):
        source_rate = Fraction(25)
    if source_rate <= 0 or source_rate > 240:
        raise ValueError("来源帧率超出可处理范围")
    rate = Fraction(body.fps) if body.fps else source_rate
    if rate > 120:
        raise ValueError("增强结果最高支持 120 fps")
    start = body.preview_start if body.preview else 0
    if start >= duration - 0.1:
        raise ValueError("预览起点必须在视频结束之前")
    span = min(5, duration - start) if body.preview else duration
    audio = [s for s in raw.get("streams", []) if s.get("codec_type") == "audio"]
    subtitles = [s for s in raw.get("streams", []) if s.get("codec_type") == "subtitle"]
    from app.services.conversion_media import TEXT_CODECS

    text_subs = [s["index"] for s in subtitles if s.get("codec_name") in TEXT_CODECS]
    return {
        "width": out_w,
        "height": out_h,
        "source_width": width,
        "source_height": height,
        "rate": str(rate),
        "fps": float(rate),
        "source_fps": float(source_rate),
        "duration": span,
        "full_duration": duration,
        "start": start,
        "audio_count": len(audio),
        "subtitle_indices": text_subs if body.keep_subtitles and not body.preview else [],
        "skipped_subtitles": len(subtitles) - len(text_subs) if body.keep_subtitles else 0,
    }


def temporal_filters(body, plan):
    chain = [
        "setpts=PTS-STARTPTS",
        f"scale={plan['source_width']}:{plan['source_height']}:flags=lanczos",
        "setsar=1",
    ]
    # Pad before interpolation so its frame lookahead does not drop the ending.
    chain.append(f"tpad=stop_mode=clone:stop={max(2, math.ceil(plan['source_fps']))}")
    if body.fps and body.fps > plan["source_fps"] + 0.1 and body.frame_method == "motion":
        chain.append(
            f"minterpolate=fps={plan['rate']}:mi_mode=mci:mc_mode=aobmc:"
            "me_mode=bidir:me=hexbs:vsbmc=1:scd=fdiff:scd_threshold=10"
        )
    else:
        chain.append(f"fps=fps={plan['rate']}:round=near")
    chain.append(f"trim=duration={plan['duration']:.6f}")
    return chain


def command_start():
    return [
        settings.ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-n",
        "-threads",
        "2",
        "-filter_threads",
        "2",
        "-progress",
        "pipe:1",
        "-nostats",
    ]


def encode_options(body, hardware):
    if hardware:
        options = ["-c:v", "h264_vaapi"]
        if body.quality == "quality":
            options += ["-rc_mode", "ICQ", "-global_quality", "20"]
        else:
            options += ["-rc_mode", "VBR"]
    else:
        options = ["-c:v", "libx264", "-preset", "medium", "-threads", "2", "-pix_fmt", "yuv420p"]
        if body.quality == "quality":
            options += ["-crf", "20"]
    if body.quality == "bitrate":
        bitrate = round(body.bitrate_mbps * 1_000_000)
        options += [
            "-b:v",
            str(bitrate),
            "-maxrate",
            str(round(bitrate * 1.5)),
            "-bufsize",
            str(bitrate * 2),
        ]
    return options


def enhancement_command(source, output, body, plan, hardware=False, *, image_pipe=False):
    command = command_start()
    if hardware:
        command += [
            "-init_hw_device",
            f"vaapi=enhance:{settings.vaapi_device}",
            "-filter_hw_device",
            "enhance",
        ]
    if image_pipe:
        command += [
            "-probesize",
            "32",
            "-analyzeduration",
            "0",
            "-f",
            "image2pipe",
            "-framerate",
            plan["rate"],
            "-vcodec",
            "png",
            "-i",
            "pipe:0",
        ]
    command += ["-ss", str(plan["start"]), "-i", str(source)]
    media = 1 if image_pipe else 0
    command += ["-map", "0:v:0" if image_pipe else "0:V:0", "-map", f"{media}:a?"]
    for index in plan["subtitle_indices"]:
        command += ["-map", f"{media}:{index}"]
    filters = [] if image_pipe else temporal_filters(body, plan)
    filters += [f"scale={plan['width']}:{plan['height']}:flags=lanczos", "setsar=1"]
    if hardware:
        filters += ["format=nv12", "hwupload"]
    command += [
        "-vf",
        ",".join(filters),
        *encode_options(body, hardware),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-c:s",
        "mov_text",
        "-dn",
        "-map_metadata",
        str(media),
        "-metadata:s:v:0",
        "rotate=0",
        "-t",
        str(plan["duration"]),
        "-fps_mode",
        "cfr",
        "-r",
        plan["rate"],
        "-movflags",
        "+faststart",
        str(output),
    ]
    return command
