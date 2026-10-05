"""媒体库扫描与入库（计划书 §7.1）。

核心流程：
    walk 目录 → quick_hash 去重 → 建/复用 videos → 建 video_sources → ffprobe 补元数据

关键安全设计：外接盘/NAS 未挂载时根目录会「存在但为空」，若照常扫描会把
整库 source 误标为不可用。因此扫描前先做 guard，且「标记消失」这一步要求
本次扫描确实有产出。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Storage, StorageType, Video, VideoSource
from app.providers.local import LocalProvider
from app.services import dedup, probe
from app.services.photos import PHOTO_EXTENSIONS, inspect_photo, photo_signature

log = logging.getLogger(__name__)


@dataclass
class ScanStats:
    scanned: int = 0          # 发现的影像文件数
    scanned_photos: int = 0
    scanned_videos: int = 0
    created: int = 0          # 新建的 videos
    linked: int = 0           # 关联到已有 video 的新 source
    updated: int = 0          # 已存在、仅刷新 last_seen
    probed: int = 0           # 完成 ffprobe
    probe_failed: int = 0
    marked_unavailable: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _now() -> datetime:
    return datetime.now(UTC)


def get_provider(storage: Storage):
    """按 storage.type 构造 provider。Phase 1 只有 local。"""
    if storage.type == StorageType.local:
        return LocalProvider(storage.config or {})
    raise NotImplementedError(f"暂不支持的存储类型: {storage.type}")


async def scan_storage(session: AsyncSession, storage_id: int, **kwargs) -> ScanStats:
    from app.services.directory_categories import library_write_lock

    with library_write_lock():
        return await _scan_storage(session, storage_id, **kwargs)


async def _scan_storage(
    session: AsyncSession,
    storage_id: int,
    *,
    sub_path: str = "",
    do_probe: bool = True,
    progress_cb=None,
) -> ScanStats:
    """扫描本地存储源并入库。返回统计信息。

    progress_cb: 可选的 async 回调 (done, total, message)，用于上报进度。
    """
    storage = await session.get(Storage, storage_id)
    if storage is None:
        raise ValueError(f"存储源不存在: {storage_id}")
    if not storage.enabled:
        raise ValueError(f"存储源已禁用: {storage.name}")

    provider = get_provider(storage)
    if not isinstance(provider, LocalProvider):
        raise NotImplementedError

    # 安全闸：根目录不可用直接拒绝，避免误删索引
    provider.guard_root_available()

    stats = ScanStats()

    # 遍历是同步阻塞 IO，放到线程池，避免卡住事件循环
    import asyncio

    entries = await asyncio.to_thread(provider.walk_videos, sub_path)
    stats.scanned = len(entries)
    stats.scanned_photos = sum(Path(e.name).suffix.lower() in PHOTO_EXTENSIONS for e in entries)
    stats.scanned_videos = stats.scanned - stats.scanned_photos

    # 安全闸：一个都没扫到（可能是盘掉线但目录还在）→ 不做「标记消失」
    if not entries:
        log.warning("扫描 %s 未发现任何视频或照片文件，跳过后续处理", storage.name)
        await session.commit()
        return stats

    seen_source_ids: set[int] = set()
    seen_categories: set[tuple[str, str]] = set()

    for i, entry in enumerate(entries):
        abs_path = provider.to_abs(entry.path)
        media_type = "photo" if abs_path.suffix.lower() in PHOTO_EXTENSIONS else "video"

        # --- 快速指纹 ---
        qh = await asyncio.to_thread(dedup.quick_hash_file, abs_path)

        # --- 该 source 是否已入库（按 storage + uri 唯一）---
        uri = f"local:{entry.path}"
        existing = (
            await session.execute(
                select(VideoSource).where(
                    VideoSource.storage_id == storage.id, VideoSource.uri == uri
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.last_seen_at = _now()
            existing.available = True
            existing.unavailable_reason = None
            existing.size_bytes = entry.size
            if qh:
                existing.quick_hash = qh
            seen_source_ids.add(existing.id)
            stats.updated += 1
            video_id = existing.video_id
            if media_type == "photo":
                photo = await session.get(Video, video_id)
                photo_meta = (photo.meta or {}).get("photo", {})
                changed = (photo_meta.get("quick_hash") != qh if qh
                           else photo_meta.get("signature") != photo_signature(abs_path))
                needs_probe = (changed
                               or not photo.cover_path
                               or not (settings.data_dir / photo.cover_path).is_file()
                               or not photo_meta.get("preview_path")
                               or not (settings.data_dir / photo_meta["preview_path"]).is_file())
            else:
                needs_probe = do_probe and _needs_probe(existing)
        else:
            # --- 跨路径去重：同指纹的 source 已存在 → 挂到同一个 video ---
            video_id = None
            if qh:
                dup = (
                    await session.execute(
                        select(VideoSource).join(Video).where(
                            VideoSource.quick_hash == qh, Video.media_type == media_type
                        ).limit(1)
                    )
                ).scalar_one_or_none()
                if dup is not None:
                    video_id = dup.video_id

            if video_id is None:
                video = Video(
                    title=abs_path.stem,
                    media_type=media_type,
                    status="indexed",
                    source_kind="local",
                    size_bytes=entry.size,
                )
                session.add(video)
                await session.flush()
                video_id = video.id
                stats.created += 1
            else:
                stats.linked += 1

            has_other = (
                await session.execute(
                    select(VideoSource.id).where(VideoSource.video_id == video_id).limit(1)
                )
            ).scalar_one_or_none() is not None

            source = VideoSource(
                video_id=video_id,
                storage_id=storage.id,
                source_type=StorageType.local,
                uri=uri,
                local_path=str(abs_path),
                quick_hash=qh,
                size_bytes=entry.size,
                is_primary=not has_other,
                available=True,
                last_seen_at=_now(),
            )
            session.add(source)
            await session.flush()
            seen_source_ids.add(source.id)
            needs_probe = do_probe
            if media_type == "photo":
                photo = await session.get(Video, video_id)
                needs_probe = not has_other or not photo.cover_path

        relative_parent = abs_path.parent.relative_to(provider.root.resolve()).as_posix()
        relative_parent = "" if relative_parent == "." else relative_parent
        if (relative_parent, media_type) not in seen_categories:
            from app.services.directory_categories import remember_category
            await remember_category(session, storage.id, relative_parent, media_type)
            seen_categories.add((relative_parent, media_type))

        # Commit every entry, including duplicates and metadata-skipped sources.
        # SQLite has one writer: progress_cb writes through a separate session.
        # Never await that writer (or slow image/ffmpeg work) with our write lock held.
        await session.commit()

        # --- ffprobe 探测（耗时不均，逐个提交以便进度可见）---
        if needs_probe:
            ok = await _probe_video(session, video_id, str(abs_path))
            if ok:
                stats.probed += 1
            else:
                stats.probe_failed += 1

        if progress_cb and (i % 5 == 0 or i == len(entries) - 1):
            await progress_cb(i + 1, len(entries), f"已处理 {i + 1}/{len(entries)}")

    # --- 标记消失的 source（仅当本次扫描确有产出）---
    if seen_source_ids:
        scope = []
        if sub_path:
            # Restrict missing-file reconciliation to this subtree, escaping LIKE metacharacters.
            prefix = str(provider.to_abs(sub_path)).rstrip("/") + "/"
            scope.append(VideoSource.local_path.startswith(prefix, autoescape=True))
        result = await session.execute(
            update(VideoSource)
            .where(
                VideoSource.storage_id == storage.id,
                VideoSource.id.not_in(seen_source_ids),
                VideoSource.available.is_(True),
                *scope,
            )
            .values(available=False, unavailable_reason="扫描中未发现（可能已删除或移走）")
        )
        stats.marked_unavailable = result.rowcount or 0

    storage.last_scan_at = _now()
    await session.commit()
    log.info("扫描完成 %s: %s", storage.name, stats.as_dict())
    return stats


def _needs_probe(source: VideoSource) -> bool:
    """source 存在但 video 元数据缺失时才重新探测。"""
    return source.duration_ms is None


async def _probe_video(session: AsyncSession, video_id: int, input_uri: str) -> bool:
    """对单个视频做 ffprobe + 抽封面，写回 videos 与 source。"""
    video = await session.get(Video, video_id)
    if video is None:
        return False

    if video.media_type == "photo":
        import asyncio
        try:
            info = await asyncio.to_thread(inspect_photo, Path(input_uri))
            info["quick_hash"] = await asyncio.to_thread(dedup.quick_hash_file, Path(input_uri))
            video.width, video.height = info["width"], info["height"]
            video.size_bytes = Path(input_uri).stat().st_size
            video.container = info["format"].lower()
            video.cover_path = info["cover_path"]
            video.thumb_path = info["thumb_path"]
            video.duration_ms = video.fps = video.bitrate_bps = None
            video.audio_codec = video.video_codec = None
            video.meta = {"photo": info}
            video.status = "indexed"
        except Exception as exc:
            log.warning("照片读取失败 id=%s: %s", video_id, exc)
            video.status = "error"
            video.cover_path = video.thumb_path = None
            video.meta = {"photo_error": "无法解码照片，请检查文件是否损坏或超过安全像素上限后重新扫描"}
            await session.commit()
            return False
        await session.commit()
        return True

    try:
        result = await probe.probe(input_uri)
    except Exception as e:
        log.warning("探测失败 video=%s %s: %s", video_id, input_uri, e)
        await session.commit()
        return False

    video.duration_ms = result.duration_ms
    video.width = result.width
    video.height = result.height
    video.fps = result.fps
    video.video_codec = result.video_codec
    video.video_profile = result.video_profile
    video.pix_fmt = result.pix_fmt
    video.audio_codec = result.audio_codec
    video.audio_channels = result.audio_channels
    video.container = result.container
    video.bitrate_bps = result.bitrate_bps
    video.size_bytes = result.size_bytes or video.size_bytes
    # 只保留摘要，避免把整个 ffprobe JSON（含大量 tags）塞进库
    video.meta = _summarize_meta(result.raw)

    # 同步 source 上的时长，供后续按 source 判重
    src = (
        await session.execute(
            select(VideoSource).where(VideoSource.video_id == video_id).limit(1)
        )
    ).scalar_one_or_none()
    if src is not None and result.duration_ms:
        src.duration_ms = result.duration_ms

    # 封面（失败不影响入库）
    try:
        cover, thumb = await probe.extract_thumbnails(
            input_uri, video_id, result.duration_ms
        )
        if cover:
            video.cover_path = cover
            video.thumb_path = thumb
    except Exception as e:
        log.warning("抽封面失败 video=%s: %s", video_id, e)

    await session.commit()
    return True


def _summarize_meta(raw: dict) -> dict:
    """裁剪 ffprobe 原始输出，只留可能有用的部分。"""
    fmt = raw.get("format", {}) or {}
    streams = []
    for s in raw.get("streams", []) or []:
        streams.append(
            {
                k: s.get(k)
                for k in (
                    "index", "codec_type", "codec_name", "profile", "width",
                    "height", "channels", "sample_rate", "bit_rate", "pix_fmt",
                    "avg_frame_rate",
                )
                if s.get(k) is not None
            }
        )
    tags = fmt.get("tags", {}) or {}
    keep_tags = {
        k: v for k, v in tags.items()
        if k.lower() in {"title", "artist", "album", "comment", "encoder", "date"}
    }
    return {
        "format_name": fmt.get("format_name"),
        "tags": keep_tags,
        "streams": streams,
        "nb_streams": fmt.get("nb_streams"),
    }
