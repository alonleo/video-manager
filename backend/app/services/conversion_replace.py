"""Install a verified conversion while retaining an exact, hidden original backup."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update

from app.models import Job, JobState, Storage, Video, VideoSource
from app.services.conversion_media import check_conflicting_jobs, fingerprint
from app.services.dedup import quick_hash_file
from app.services.directory_categories import directory_path, library_write_lock, storage_root
from app.services.operations import local_source, move_without_replace
from app.services.probe import parse_probe_json


def replacement_paths(original: Path, job_id: int, extension: str, *, create=False):
    directory = original.parent / ".video-manage-backups" / f"conversion-{job_id}"
    if create:
        root = directory.parent
        if root.is_symlink():
            raise ValueError("原目录的备份位置不能是符号链接")
        root.mkdir(mode=0o700, exist_ok=True)
        directory.mkdir(mode=0o700)
        (directory / "new").mkdir(mode=0o700)
        (directory / "original").mkdir(mode=0o700)
    if directory.resolve() != directory or directory.is_symlink():
        raise ValueError("备份路径存在符号链接，已停止替换")
    return directory / "new" / f"result.{extension}", directory / "original" / original.name


def write_journal(path: Path, data: dict):
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


async def install_replacement(
    session, job_id, source_id, original, staged, raw, body, expected, result
):
    """The job completion and all source metadata are committed together after file installation."""
    _, backup = replacement_paths(original, job_id, body.format)
    destination = original.with_suffix("." + body.format)
    if len(destination.name.encode("utf-8")) > 255:
        raise ValueError("替换后的文件名过长")
    new_hash = await asyncio.to_thread(quick_hash_file, staged)
    produced = parse_probe_json(raw)
    journal = backup.parent.parent / "recovery.json"
    moved_original = moved_result = False
    with library_write_lock():
        try:
            # This write reserves SQLite's writer slot. A racing cancel can win before it,
            # or observe the completed job afterwards, never cancel halfway through install.
            changed = await session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.state == JobState.running,
                )
                .values(message="正在安全替换原文件，保留原片备份")
            )
            if not changed.rowcount:
                await session.rollback()
                raise ValueError("任务已取消，未替换原视频")
            source, current = await local_source(session, result["video_id"], source_id)
            if current != original or fingerprint(original) != expected:
                raise ValueError("转换期间原文件发生变化，未执行替换")
            storage = await session.get(Storage, source.storage_id)
            relative = original.parent.relative_to(storage_root(storage)).as_posix()
            directory_path(storage, "" if relative == "." else relative)
            await check_conflicting_jobs(session, source, body, job_id)
            if destination != original and (destination.exists() or destination.is_symlink()):
                raise ValueError("目标格式的同名文件已存在，未执行替换")
            refs = list(
                (
                    await session.scalars(
                        select(VideoSource).where(
                            VideoSource.local_path == source.local_path,
                        )
                    )
                ).all()
            )
            plans = []
            for ref in refs:
                owner = await session.get(Storage, ref.storage_id)
                uri = "local:" + destination.relative_to(storage_root(owner)).as_posix()
                conflict = await session.scalar(
                    select(VideoSource.id)
                    .where(
                        VideoSource.storage_id == ref.storage_id,
                        VideoSource.uri == uri,
                        VideoSource.id.not_in([r.id for r in refs]),
                    )
                    .limit(1)
                )
                if conflict:
                    raise ValueError("目标路径已有其他视频索引，未执行替换")
                plans.append((ref, uri))
            # All expensive work precedes the short filesystem/DB critical section.
            with staged.open("rb") as handle:
                os.fsync(handle.fileno())
            os.chmod(staged, original.stat().st_mode & 0o777)
            write_journal(
                journal,
                {
                    "job_id": job_id,
                    "phase": "prepared",
                    "original": str(original),
                    "destination": str(destination),
                    "backup": str(backup),
                    "staged": str(staged),
                },
            )
            move_without_replace(original, backup)
            moved_original = True
            move_without_replace(staged, destination)
            moved_result = True
            for ref, uri in plans:
                ref.local_path, ref.uri = str(destination), uri
                ref.quick_hash, ref.file_hash = new_hash, None
                ref.size_bytes, ref.duration_ms = destination.stat().st_size, produced.duration_ms
                ref.available, ref.unavailable_reason = True, None
                ref.last_seen_at = datetime.now(UTC)
            from app.services.library import _summarize_meta

            for video_id in {r.video_id for r in refs}:
                candidates = [r for r in refs if r.video_id == video_id]
                primary = next((r for r in candidates if r.id == source_id), candidates[0])
                await session.execute(
                    update(VideoSource)
                    .where(VideoSource.video_id == video_id)
                    .values(is_primary=(VideoSource.id == primary.id))
                )
                video = await session.get(Video, video_id)
                for field in (
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
                ):
                    setattr(video, field, getattr(produced, field))
                video.size_bytes, video.meta = destination.stat().st_size, _summarize_meta(raw)
                video.status = "indexed"
            result.update(
                {
                    "filename": destination.name,
                    "replaced": True,
                    "source_id": source_id,
                    "output_path": str(destination),
                    "output_fingerprint": fingerprint(destination),
                    "backup_path": str(backup),
                }
            )
            await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == JobState.running)
                .values(
                    state=JobState.done,
                    progress=1.0,
                    result=result,
                    message="转换完成，已替换原视频并保留原片备份",
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()
        except BaseException:
            await session.rollback()
            if moved_result:
                move_without_replace(destination, staged)
            if moved_original:
                move_without_replace(backup, original)
            raise
    # A journal update failure must never undo an already committed replacement.
    try:
        write_journal(
            journal,
            {
                "job_id": job_id,
                "phase": "committed",
                "original": str(original),
                "destination": str(destination),
                "backup": str(backup),
            },
        )
    except OSError:
        pass
    return result
