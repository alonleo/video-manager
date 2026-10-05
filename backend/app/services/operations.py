"""Basic video actions and validated conversion/subtitle options."""

from __future__ import annotations

import ctypes
import errno
import os
import re
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobState, Storage, StorageType, Video, VideoSource


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=180)
    rename_file: bool = True
    source_id: int | None = Field(default=None, gt=0)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        value = value.strip()
        if not value or value in {".", ".."} or value.endswith("."):
            raise ValueError("名称不能为空，也不能以句点结尾")
        if re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', value):
            raise ValueError("名称不能含路径分隔符、控制字符或特殊文件名字符")
        if len(value.encode("utf-8")) > 220:
            raise ValueError("名称过长，请缩短后重试")
        return value


class ConvertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["mp4", "mkv", "mov", "avi", "ts", "m2ts"] = "mkv"
    source_id: int | None = Field(default=None, gt=0)
    subtitle_mode: Literal["none", "embed", "burn"] = "none"
    subtitle_track_ids: list[int] = Field(default_factory=list, max_length=8)
    source_subtitle_indices: list[int] = Field(default_factory=list, max_length=8)
    bilingual: bool = False
    replace_source: bool = False

    @model_validator(mode="after")
    def validate_subtitles(self):
        if any(i <= 0 for i in self.subtitle_track_ids) or any(
            i < 0 for i in self.source_subtitle_indices
        ):
            raise ValueError("字幕标识无效")
        if len(set(self.subtitle_track_ids)) != len(self.subtitle_track_ids) or len(
            set(self.source_subtitle_indices)
        ) != len(self.source_subtitle_indices):
            raise ValueError("不能重复选择同一字幕")
        count = len(self.subtitle_track_ids) + len(self.source_subtitle_indices)
        if count > 8:
            raise ValueError("一次最多选择 8 条字幕轨")
        if self.subtitle_mode == "none" and count:
            raise ValueError("不添加字幕时不能选择字幕轨")
        if self.subtitle_mode != "none" and not count:
            raise ValueError("请至少选择一条字幕")
        if self.subtitle_mode == "burn" and count != 1:
            raise ValueError("烧录画面时请选择一条字幕，可使用双语显示")
        if (
            self.subtitle_mode == "embed"
            and self.format in {"avi", "ts", "m2ts"}
            and self.subtitle_track_ids
        ):
            raise ValueError("此格式不支持项目文字字幕软轨，请选择烧录画面或使用 MKV/MP4/MOV")
        return self


async def local_source(
    session: AsyncSession,
    video_id: int,
    source_id: int | None = None,
) -> tuple[VideoSource, Path]:
    stmt = (
        select(VideoSource, Storage)
        .join(Storage, VideoSource.storage_id == Storage.id)
        .where(
            VideoSource.video_id == video_id,
            VideoSource.available.is_(True),
            VideoSource.source_type == StorageType.local,
            Storage.enabled.is_(True),
        )
        .order_by(VideoSource.is_primary.desc(), VideoSource.id)
    )
    if source_id is not None:
        stmt = stmt.where(VideoSource.id == source_id)
    for source, storage in (await session.execute(stmt)).all():
        if not source.local_path or not storage.config.get("root"):
            continue
        raw = Path(source.local_path)
        path = raw.resolve()
        root = Path(storage.config["root"]).expanduser().resolve()
        # Only indexed regular files inside their configured storage are accepted.
        if raw.is_symlink() or not path.is_relative_to(root):
            continue
        if path.is_file() and os.access(path, os.R_OK):
            return source, path
    raise HTTPException(409, "没有可读取的本地来源，请检查存储目录或磁盘挂载")


def move_without_replace(source: Path, destination: Path) -> None:
    """Atomic, no-clobber rename on Linux; safe same-directory fallback elsewhere."""
    if source == destination:
        return
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is not None:
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) == 0:
            return
        code = ctypes.get_errno()
        if code not in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
            raise OSError(code, os.strerror(code), str(destination))
    os.link(source, destination)
    try:
        source.unlink()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


async def rename_video(session: AsyncSession, video: Video, body: RenameRequest) -> dict:
    destination = None
    original = None
    moved = False
    if body.rename_file:
        source, original = await local_source(session, video.id, body.source_id)
        related = (
            await session.scalars(
                select(VideoSource).where(VideoSource.local_path == source.local_path)
            )
        ).all()
        related_ids = [s.video_id for s in related]
        active = await session.scalar(
            select(Job.id)
            .where(
                Job.video_id.in_(related_ids),
                Job.state.in_([JobState.queued, JobState.running]),
            )
            .limit(1)
        )
        if active:
            raise HTTPException(409, "该文件正在处理或等待处理，请完成或取消任务后再重命名")
        # The form accepts the stem, while retaining the original suffix.
        destination = original.with_name(body.name + original.suffix)
        if len(destination.name.encode("utf-8")) > 255:
            raise HTTPException(422, "文件名过长，请缩短后重试")
        try:
            move_without_replace(original, destination)
            moved = original != destination
        except FileExistsError as exc:
            raise HTTPException(409, "同目录已存在该名称的文件，请换一个名称") from exc
        except OSError as exc:
            raise HTTPException(409, "重命名失败，请检查文件和目录的写入权限") from exc
        for ref in related:
            ref.local_path = str(destination)
            ref.uri = f"local:{destination}"
    video.title = body.name
    try:
        await session.commit()
    except BaseException:
        await session.rollback()
        if moved:
            move_without_replace(destination, original)
        raise
    return {
        "video_id": video.id,
        "title": body.name,
        "renamed_file": moved,
        "filename": destination.name if destination else None,
    }


def remux_command(source: Path, output: Path, ffmpeg: str) -> list[str]:
    # No user-supplied command/filter/codec. -c copy never decodes or encodes.
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(source),
        "-map",
        "0:V:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-sn",
        "-dn",
    ]
    if output.suffix in {".mp4", ".mov"}:
        command += ["-movflags", "+faststart"]
    return command + [str(output)]
