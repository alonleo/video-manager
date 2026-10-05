"""Read photo metadata and render disposable previews; never modify the original."""

from __future__ import annotations

import warnings
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from app.config import settings
from app.models import Video

register_heif_opener()
PHOTO_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
    ".heic",
    ".heif",
}
BROWSER_MIMES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "AVIF": "image/avif",
}


async def require_video(session, video_id: int) -> Video:
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "影像不存在")
    if video.media_type == "photo":
        raise HTTPException(422, "照片不支持视频播放、转码、增强或字幕操作，请使用照片预览")
    return video


def photo_signature(path: Path) -> list[int]:
    st = path.stat()
    return [st.st_size, st.st_mtime_ns]


def inspect_photo(path: Path) -> dict:
    """Orientation-aware JPEG cache, bounded to 2560px, with unique cache identities."""
    outputs = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.format not in {*BROWSER_MIMES, "BMP", "TIFF", "HEIF"}:
                    raise ValueError("不支持的照片编码")
                format_name = source.format
                frame_count = getattr(source, "n_frames", 1)
                signature = photo_signature(path)
                frame = ImageOps.exif_transpose(source)
                frame.load()
                width, height = frame.size
                # Flatten transparency onto the preview canvas, without touching the source.
                if frame.mode in {"RGBA", "LA", "P"}:
                    rgba = frame.convert("RGBA")
                    rgb = Image.new("RGB", frame.size, (245, 242, 235))
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    rgb = frame.convert("RGB")
                stem = uuid4().hex
                result = {
                    "format": format_name,
                    "frames": frame_count,
                    "width": width,
                    "height": height,
                    "signature": signature,
                }
                for key, folder, limit in (
                    ("preview_path", "photos", 2560),
                    ("cover_path", "covers", 640),
                    ("thumb_path", "thumbs", 320),
                ):
                    relative = f"{folder}/photo-{stem}.jpg"
                    out = settings.data_dir / relative
                    out.parent.mkdir(parents=True, exist_ok=True)
                    outputs.append(out)
                    rgb.thumbnail((limit, limit), Image.Resampling.LANCZOS)
                    rgb.save(out, "JPEG", quality=90 if key == "preview_path" else 85)
                    result[key] = relative
                return result
    except BaseException:
        for out in outputs:
            out.unlink(missing_ok=True)
        raise
