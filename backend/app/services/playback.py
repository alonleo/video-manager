"""Conservative direct play, H.264 remux, and universal H.264/AAC fallback."""

from __future__ import annotations

from dataclasses import dataclass

from app.providers.base import PlaybackMode

BLOCKED_H264_PROFILES = {"High 4:4:4 Predictive", "High 10", "High 4:2:2"}


@dataclass
class Decision:
    mode: PlaybackMode
    reason: str

    @property
    def needs_ffmpeg(self) -> bool:
        return self.mode in (PlaybackMode.remux, PlaybackMode.transcode)


def can_copy_h264(codec: str | None, profile: str | None, pix_fmt: str | None) -> bool:
    return (
        (codec or "").lower() == "h264"
        and profile not in BLOCKED_H264_PROFILES
        and pix_fmt in {None, "yuv420p", "yuvj420p"}
    )


def decide(
    *,
    container: str | None,
    video_codec: str | None,
    video_profile: str | None,
    audio_codec: str | None,
    pix_fmt: str | None = None,
    extension: str | None = None,
    force_compatible: bool = False,
) -> Decision:
    if force_compatible:
        return Decision(PlaybackMode.transcode, "兼容播放：转换为 H.264 / AAC / MP4")
    c = (container or "").lower().split(",")[0]
    v = (video_codec or "").lower()
    a = (audio_codec or "").lower()
    ext = (extension or "").lower()
    safe_h264 = can_copy_h264(v, video_profile, pix_fmt)
    # ffprobe calls MP4/3GP/MOV 'mov' and WebM/MKV 'matroska'. Use both
    # the actual demuxer and extension; never assume the suffix proves a codec.
    mp4 = c in {"mov", "mp4", "m4v"} and ext in {".mp4", ".m4v", ""}
    webm = c in {"webm", "matroska"} and (ext == ".webm" or (c == "webm" and not ext))
    if mp4 and safe_h264 and a in {"", "aac"}:
        return Decision(PlaybackMode.direct, "H.264 / AAC 兼容 MP4，直接播放")
    if (
        webm
        and v in {"vp8", "vp9", "av1"}
        and a in {"", "opus", "vorbis"}
        and pix_fmt in {None, "yuv420p"}
    ):
        return Decision(PlaybackMode.direct, "WebM 音视频编码兼容，直接播放")
    if safe_h264:
        audio_note = "" if a in {"", "aac"} else f"；{a.upper()} 音频转换为 AAC"
        return Decision(PlaybackMode.remux, "转换为兼容 MP4，视频画质保持不变" + audio_note)
    return Decision(
        PlaybackMode.transcode,
        f"{v.upper() or '未知编码'} 视频转换为兼容 H.264 / AAC / MP4",
    )
