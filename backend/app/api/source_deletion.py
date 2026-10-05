"""Explicit deletion of one confirmed, indexed local video file."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import stat
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Job, JobState, Storage, StorageType, Video, VideoSource
from app.services.directory_categories import directory_path, library_write_lock, storage_root

log = logging.getLogger(__name__)
router = APIRouter()


class DeleteSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    confirmation: str = Field(pattern=r"^[a-f0-9]{64}$")


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def open_parent(path: Path) -> int:
    """Pin every directory, refusing symlinks even if paths change during validation."""
    handle = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handle)
            os.close(handle)
            handle = child
        return handle
    except BaseException:
        os.close(handle)
        raise


async def prepare(session, video_id, source_id):
    video = await session.get(Video, video_id)
    source = await session.get(VideoSource, source_id)
    if video is None or source is None or source.video_id != video_id:
        raise HTTPException(404, "视频或来源不存在，请刷新页面")
    storage = await session.get(Storage, source.storage_id)
    if source.source_type != StorageType.local or not source.local_path:
        raise HTTPException(400, "只能删除本地磁盘的视频源文件")
    if storage is None or not storage.enabled:
        raise HTTPException(409, "存储源不可用，请检查磁盘挂载")
    path = Path(source.local_path)
    root = storage_root(storage)
    try:
        relative = path.parent.relative_to(root).as_posix()
        directory_path(storage, "" if relative == "." else relative)
        parent = open_parent(path)
        try:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(parent)
        if not stat.S_ISREG(info.st_mode):
            raise HTTPException(409, "只能删除普通视频文件，不能删除目录或符号链接")
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "源文件不可用或路径已变化，请检查磁盘并重新扫描") from exc

    refs = list(
        await session.scalars(
            select(VideoSource).where(
                VideoSource.local_path == str(path),
            )
        )
    )
    if any(ref.video_id != video_id for ref in refs):
        raise HTTPException(409, "该文件还关联其他视频收录，请先整理重复收录后再删除")
    remaining = list(
        await session.scalars(
            select(VideoSource).where(
                VideoSource.video_id == video_id,
                VideoSource.id.not_in([ref.id for ref in refs]),
            )
        )
    )
    affected_storages = {ref.storage_id for ref in refs}
    for owner in await session.scalars(select(Storage).where(Storage.type == StorageType.local)):
        try:
            if path.is_relative_to(storage_root(owner)):
                affected_storages.add(owner.id)
        except HTTPException:
            continue
    for job in await session.scalars(
        select(Job).where(
            Job.state.in_([JobState.queued, JobState.running]),
        )
    ):
        payload = job.payload or {}
        if (
            job.video_id == video_id
            or (job.kind == "scan_library" and payload.get("storage_id") in affected_storages)
            or (job.kind == "move_video" and payload.get("_destination") == str(path))
        ):
            raise HTTPException(409, "视频或所在存储源还有处理任务，请完成或取消任务后再删除")

    # Include both file identity and impact: stale dialogs never delete a replacement file
    # or unexpectedly remove a video whose other source disappeared in the meantime.
    identity = [
        video_id,
        source_id,
        str(path),
        str(root),
        signature(info),
        info.st_ctime_ns,
        sorted(ref.id for ref in refs),
        sorted(ref.id for ref in remaining),
    ]
    confirmation = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    preview = {
        "video_id": video_id,
        "source_id": source_id,
        "path": str(path),
        "filename": path.name,
        "size_bytes": info.st_size,
        "affected_sources": len(refs),
        "remaining_sources": len(remaining),
        "removes_video": not remaining,
        "confirmation": confirmation,
    }
    return video, path, info, refs, remaining, preview


@router.get("/{video_id}/sources/{source_id}/deletion")
async def deletion_preview(video_id: int, source_id: int, session=Depends(get_session)):
    *_, preview = await prepare(session, video_id, source_id)
    return preview


class StagedFile:
    """A short same-filesystem rename makes a failed DB commit recoverable."""

    def __init__(self, path, info, preview):
        self.path, self.info, self.preview = path, info, preview
        self.parent = open_parent(path)
        self.directory = ".video-manage-delete-" + uuid.uuid4().hex
        self.folder = None
        self.moved = False

    def stage(self):
        current = os.stat(self.path.name, dir_fd=self.parent, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or signature(current) != signature(self.info):
            raise HTTPException(409, "源文件已变化，请重新打开删除窗口确认")
        os.mkdir(self.directory, 0o700, dir_fd=self.parent)
        self.folder = os.open(
            self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent
        )
        journal = os.open(
            "recovery.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self.folder
        )
        with os.fdopen(journal, "w") as stream:
            json.dump(self.preview, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(self.path.name, "original", src_dir_fd=self.parent, dst_dir_fd=self.folder)
        self.moved = True
        captured = os.stat("original", dir_fd=self.folder, follow_symlinks=False)
        if not stat.S_ISREG(captured.st_mode) or signature(captured) != signature(self.info):
            raise HTTPException(409, "源文件在删除前发生变化，已停止删除")
        os.fsync(self.parent)
        os.fsync(self.folder)

    @property
    def recovery_path(self):
        return str(self.path.parent / self.directory / "original")

    def restore(self):
        if self.moved:
            # Never overwrite a new file that appeared at the original path.
            os.link(
                "original",
                self.path.name,
                src_dir_fd=self.folder,
                dst_dir_fd=self.parent,
                follow_symlinks=False,
            )
            os.unlink("original", dir_fd=self.folder)
            self.moved = False
            os.fsync(self.parent)

    def purge(self):
        os.unlink("original", dir_fd=self.folder)
        self.moved = False
        try:
            os.fsync(self.folder)
        except OSError:
            log.warning("File deleted but directory sync failed: %s", self.directory)

    def close(self):
        if self.folder is not None:
            if not self.moved:
                try:
                    os.unlink("recovery.json", dir_fd=self.folder)
                    os.rmdir(self.directory, dir_fd=self.parent)
                except OSError:
                    log.warning("Could not remove empty deletion journal: %s", self.directory)
            os.close(self.folder)
        os.close(self.parent)


async def delete_source(session: AsyncSession, video_id, source_id, body):
    with library_write_lock(), library_write_lock(".move-submission.lock"):
        staged = None
        committed = False
        try:
            # Reserve the SQLite writer before checking jobs; concurrent submissions
            # cannot commit a new file operation halfway through deletion.
            await session.execute(text("BEGIN IMMEDIATE"))
            video, path, info, refs, remaining, preview = await prepare(
                session,
                video_id,
                source_id,
            )
            if body.confirmation != preview["confirmation"]:
                raise HTTPException(409, "文件或来源已变化，请重新打开删除窗口确认")
            staged = StagedFile(path, info, preview)
            staged.stage()
            if not remaining:
                await session.delete(video)
            else:
                for ref in refs:
                    await session.delete(ref)
                primary = sorted(
                    remaining,
                    key=lambda ref: (
                        not ref.available,
                        not ref.is_primary,
                        ref.id,
                    ),
                )[0]
                for ref in remaining:
                    ref.is_primary = ref.id == primary.id
                video.source_kind = primary.source_type.value
                if not any(ref.available for ref in remaining):
                    video.status = "unavailable"
            await session.commit()
            committed = True
            warning = None
            try:
                staged.purge()
            except OSError:
                log.exception(
                    "Index updated but deletion needs manual cleanup: %s", staged.recovery_path
                )
                warning = "收录已更新，但磁盘清理未完成；文件仍保留在：" + staged.recovery_path
            return {
                "video_id": video_id,
                "source_id": source_id,
                "path": str(path),
                "video_removed": not remaining,
                "remaining_sources": len(remaining),
                "deleted": not staged.moved,
                "warning": warning,
            }
        except BaseException as exc:
            if not committed:
                try:
                    await session.rollback()
                finally:
                    if staged is not None:
                        try:
                            staged.restore()
                        except OSError as recovery_error:
                            log.exception(
                                "Deletion rollback retained file: %s", staged.recovery_path
                            )
                            raise HTTPException(
                                500, "删除未完成，原文件保留在：" + staged.recovery_path
                            ) from recovery_error
            if isinstance(exc, OSError):
                raise HTTPException(409, "删除失败，文件已保留，请检查目录写入权限后重试") from exc
            raise
        finally:
            if staged is not None:
                staged.close()


@router.post("/{video_id}/sources/{source_id}/deletion")
async def confirm_deletion(
    video_id: int,
    source_id: int,
    body: DeleteSourceRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/"), "http://127.0.0.1:5173", "http://localhost:5173"}
    if origin and origin not in allowed:
        raise HTTPException(403, "拒绝来自其他网站的删除请求")
    task = asyncio.create_task(delete_source(session, video_id, source_id, body))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Keep the session alive until file and index changes finish or roll back.
        await task
        raise
