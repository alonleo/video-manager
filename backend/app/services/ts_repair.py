"""Inspect and safely remux MPEG-TS files with inconsistent duration metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


TS_SUFFIXES = {".ts"}
THRESHOLD_SECONDS = 0.1


class TsRepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: int | None = Field(default=None, gt=0)
    replace_source: bool = False


@dataclass(frozen=True)
class TsDurationInspection:
    stream_duration: float | None
    format_duration: float | None
    difference: float | None
    needs_repair: bool
    reason: str


def _duration(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def inspect_ts_duration(path: Path, probe: dict) -> TsDurationInspection:
    if path.suffix.lower() not in TS_SUFFIXES:
        raise ValueError("只有 .ts 文件可以检查时长")
    video = next(
        (item for item in probe.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    stream_duration = _duration(video.get("duration")) if video else None
    format_duration = _duration((probe.get("format") or {}).get("duration"))
    if stream_duration is None or format_duration is None:
        return TsDurationInspection(
            stream_duration, format_duration, None, True,
            "TS 时长信息缺失，建议无损重封装后重新读取",
        )
    difference = abs(stream_duration - format_duration)
    return TsDurationInspection(
        stream_duration,
        format_duration,
        difference,
        difference > THRESHOLD_SECONDS,
        (
            f"视频流与容器时长相差 {difference:.3f} 秒，建议修复"
            if difference > THRESHOLD_SECONDS
            else "视频流与容器时长一致，无需修复"
        ),
    )


def ts_repair_command(source: Path, output: Path, ffmpeg: str) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-n",
        "-progress", "pipe:1", "-nostats", "-i", str(source),
        "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero",
        str(output),
    ]


def validate_ts_repair_output(original: dict, produced: dict) -> None:
    def codecs(raw: dict, kind: str) -> list[str | None]:
        return [
            item.get("codec_name")
            for item in raw.get("streams", [])
            if item.get("codec_type") == kind
        ]

    for kind, label in (("video", "视频"), ("audio", "音频"), ("subtitle", "字幕")):
        if codecs(original, kind) != codecs(produced, kind):
            raise ValueError(f"修复结果的{label}轨道与原文件不一致，已保留原文件")
