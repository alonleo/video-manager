"""Directory-backed categories and explicit, no-overwrite local file moves."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import logging
import os
import shutil
import tempfile
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from app.config import settings
from app.models import CategoryScope, Job, JobState, Storage, StorageType, Video, VideoSource
from app.services.operations import RenameRequest, local_source, move_without_replace

log = logging.getLogger(__name__)


def relative_directory(value: str) -> str:
    if value in {"", "."}:
        return ""
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("分类路径必须位于存储源内，不能包含上级目录或绝对路径")
    return p.as_posix()


class DirectoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage_id: int = Field(gt=0)
    path: str = Field(default="", max_length=4096)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return relative_directory(value)


class CreateCategoryRequest(DirectoryRequest):
    media_type: Literal["video", "photo"] = "video"
    name: str = Field(min_length=1, max_length=180)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = RenameRequest.safe_name(value)
        if value.startswith(".") or value.lower() in {"#recycle", "@eadir", "lost+found"}:
            raise ValueError("请使用非隐藏、非系统目录的分类名称")
        return value


class MoveVideoRequest(DirectoryRequest):
    source_id: int = Field(gt=0)


@contextmanager
def library_write_lock(name=".directory-write.lock"):
    """Shared by API mutations and scanner across processes; never block the event loop."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with (settings.data_dir / name).open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HTTPException(409, "目录正在扫描或整理，请稍后重试") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def storage_root(storage: Storage) -> Path:
    raw = (storage.config or {}).get("root")
    if storage.type != StorageType.local or not isinstance(raw, str) or not raw:
        raise HTTPException(400, "分类仅支持配置了根目录的本地存储源")
    return Path(raw).expanduser().resolve()


def directory_path(storage: Storage, relative: str) -> Path:
    root = storage_root(storage)
    target = root / relative_directory(relative)
    if not target.resolve().is_relative_to(root):
        raise HTTPException(400, "分类目录超出存储源范围")
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(400, "不能将符号链接目录用作分类操作目标")
    if not root.is_dir() or not target.is_dir():
        raise HTTPException(409, "分类目录不可用，请检查磁盘挂载或刷新分类")
    return target


async def get_local_storage(session, storage_id: int) -> Storage:
    storage = await session.get(Storage, storage_id)
    if storage is None:
        raise HTTPException(404, "存储源不存在")
    if not storage.enabled:
        raise HTTPException(409, "存储源已禁用")
    storage_root(storage)
    return storage


def category_info(storage: Storage, relative: str) -> dict:
    return {
        "id": f"{storage.id}:{relative}",
        "storage_id": storage.id,
        "storage_name": storage.name,
        "path": relative,
        "name": PurePosixPath(relative).name if relative else storage_root(storage).name,
    }


def source_category(source: VideoSource, storage: Storage) -> dict | None:
    if source.source_type != StorageType.local or not source.local_path:
        return None
    try:
        root = storage_root(storage)
        # Paths stored by the scanner are canonical; avoid touching each media file.
        rel = Path(source.local_path).parent.relative_to(root).as_posix()
    except (ValueError, HTTPException):
        return None
    return category_info(storage, "" if rel == "." else rel)


async def video_categories(session, video_ids: list[int]) -> dict[int, list[dict]]:
    result = defaultdict(dict)
    if not video_ids:
        return {}
    rows = (
        await session.execute(
            select(VideoSource, Storage)
            .join(Storage, Storage.id == VideoSource.storage_id)
            .where(VideoSource.video_id.in_(video_ids), VideoSource.available.is_(True))
            .order_by(VideoSource.is_primary.desc(), VideoSource.id)
        )
    ).all()
    for source, storage in rows:
        info = source_category(source, storage)
        if info:
            result[source.video_id][info["id"]] = info
    return {vid: list(items.values()) for vid, items in result.items()}


async def category_video_ids(session, token: str) -> list[int]:
    try:
        sid, raw = token.split(":", 1)
        sid = int(sid)
        rel = relative_directory(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "无效的分类") from exc
    storage = await session.get(Storage, sid)
    if storage is None:
        return []
    parent = storage_root(storage) / rel
    rows = (
        (
            await session.execute(
                select(VideoSource).where(
                    VideoSource.storage_id == sid,
                    VideoSource.source_type == StorageType.local,
                    VideoSource.available.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return list({s.video_id for s in rows if s.local_path and Path(s.local_path).parent == parent})


def _directories(storage: Storage) -> tuple[set[str], list[str]]:
    root = storage_root(storage)
    if not storage.enabled or not root.is_dir():
        return set(), [f"{storage.name}：存储源已禁用或目录不可用"]
    found = {""}
    warnings = []

    def onerror(_error):
        warnings.append(f"{storage.name}：部分目录无法读取，请检查权限")

    for current, dirs, _files in os.walk(root, followlinks=False, onerror=onerror):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d.lower() not in {"#recycle", "@eadir", "lost+found"}
            and not (Path(current) / d).is_symlink()
        )
        for name in dirs:
            found.add((Path(current) / name).relative_to(root).as_posix())
        if len(found) >= 20000:
            warnings.append(f"{storage.name}：目录较多，仅列出前 20000 个目录及已收录分类")
            break
    return found, list(dict.fromkeys(warnings))


async def remember_category(session, storage_id: int, path: str, media_type: str) -> None:
    await session.execute(insert(CategoryScope).values(
        storage_id=storage_id, path=path, media_type=media_type,
    ).on_conflict_do_nothing())


async def list_categories(session, media_type: str = "video") -> dict:
    storages = list(await session.scalars(
        select(Storage).where(Storage.type == StorageType.local).order_by(Storage.id)
    ))
    storage_map = {s.id: s for s in storages}
    scopes = list(await session.scalars(select(CategoryScope)))
    memberships = {(s.storage_id, s.path, s.media_type) for s in scopes}
    counts = defaultdict(set)
    # Derive current categories as well: downloads/imports can add sources outside the scanner.
    rows = (await session.execute(
        select(VideoSource, Video.media_type).join(Video)
        .where(VideoSource.source_type == StorageType.local)
    )).all()
    for source, kind in rows:
        storage = storage_map.get(source.storage_id)
        info = source_category(source, storage) if storage else None
        if info:
            memberships.add((storage.id, info["path"], kind))
            if kind == media_type and source.available:
                counts[(storage.id, info["path"])].add(source.video_id)
    kinds_by_storage = defaultdict(set)
    owned = defaultdict(set)
    for sid, path, kind in memberships:
        kinds_by_storage[sid].add(kind)
        if kind != media_type:
            continue
        owned[sid].add(path)
        # Ancestors allow directory creation/navigation without exposing the other library's branches.
        owned[sid].update("" if str(p) == "." else str(p) for p in PurePosixPath(path).parents)
        owned[sid].add("")
    items, roots, warnings = [], [], []
    for storage in storages:
        # Existing empty folders in a single-library storage retain that ownership.
        # A mixed storage exposes only the branches belonging to this library.
        if kinds_by_storage[storage.id] == {media_type}:
            try:
                directories, problems = await asyncio.to_thread(_directories, storage)
                owned[storage.id].update(directories)
                warnings.extend(problems)
            except (OSError, HTTPException):
                pass
        def available(path):
            if not storage.enabled:
                return False
            try:
                directory_path(storage, path)
                return True
            except HTTPException:
                return False
        # Unassigned storage roots are creation destinations, not shared categories.
        if not kinds_by_storage[storage.id] or media_type in kinds_by_storage[storage.id]:
            roots.append({**category_info(storage, ""), "video_count": len(counts[(storage.id, "")]), "available": available("")})
        for rel in sorted(owned[storage.id]):
            items.append({**category_info(storage, rel), "video_count": len(counts[(storage.id, rel)]), "available": available(rel)})
        if owned[storage.id] and not available(""):
            warnings.append(f"{storage.name}：存储源已禁用或目录不可用")
    return {"items": items, "roots": roots, "warnings": warnings}


async def check_category_destination(session, storage_id: int, path: str, media_type: str):
    current = await list_categories(session, media_type)
    opposite = await list_categories(session, "photo" if media_type == "video" else "video")
    token = f"{storage_id}:{path}"
    own = {c["id"] for c in current["items"] + current["roots"]}
    other = {c["id"] for c in opposite["items"]}
    if token not in own and (token in other or f"{storage_id}:" not in own):
        raise HTTPException(409, "目标分类属于另一个媒体库，请选择当前库的分类")


async def create_category(session, body: CreateCategoryRequest) -> dict:
    with library_write_lock():
        storage = await get_local_storage(session, body.storage_id)
        await check_category_destination(session, storage.id, body.path, body.media_type)
        parent = directory_path(storage, body.path)
        target = parent / body.name
        try:
            await asyncio.to_thread(target.mkdir)
        except FileExistsError as exc:
            raise HTTPException(409, "该目录下已存在同名文件或分类") from exc
        except OSError as exc:
            raise HTTPException(409, "创建目录失败，请检查磁盘空间和写入权限") from exc
        rel = target.relative_to(storage_root(storage)).as_posix()
        try:
            await remember_category(session, storage.id, rel, body.media_type)
            await session.commit()
        except BaseException:
            await session.rollback()
            # Only remove the empty directory just created by this operation.
            try:
                target.rmdir()
            except OSError:
                pass
            raise
        return {**category_info(storage, rel), "video_count": 0, "available": True}


def _fingerprint(path: Path) -> tuple:
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def transfer_file(original: Path, destination: Path, progress=None) -> tuple[bool, tuple]:
    """Cross-device copy verifies bytes before publication; keep original until DB commit."""
    before = _fingerprint(original)
    report = progress or (lambda *_: None)
    total = max(1, before[2])
    report(0.02, "正在准备移动文件")
    try:
        move_without_replace(original, destination)
        return False, before
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    fd, temporary = tempfile.mkstemp(prefix=".video-move-", dir=destination.parent)
    temporary = Path(temporary)
    try:
        digest = hashlib.sha256()
        copied = 0
        with os.fdopen(fd, "wb") as output, original.open("rb") as source:
            while chunk := source.read(4 * 1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
                report(0.05 + 0.55 * copied / total, f"正在复制文件 · {copied / total:.0%}")
            output.flush()
            os.fsync(output.fileno())
        checked = 0
        verified = hashlib.sha256()
        with temporary.open("rb") as verify:
            while chunk := verify.read(4 * 1024 * 1024):
                verified.update(chunk)
                checked += len(chunk)
                report(0.60 + 0.32 * checked / total, f"正在校验完整文件 · {checked / total:.0%}")
        if verified.digest() != digest.digest():
            raise OSError("目标文件校验失败")
        if _fingerprint(original) != before:
            raise OSError("移动过程中源文件发生变化，请稍后重试")
        shutil.copystat(original, temporary)
        move_without_replace(temporary, destination)
        return True, before
    finally:
        temporary.unlink(missing_ok=True)


async def prepare_move(session, video_id: int, body: MoveVideoRequest, job_id=None):
    """Validate without changing files or ORM records; called again by the worker."""
    source, original = await local_source(session, video_id, body.source_id)
    origin_storage = await session.get(Storage, source.storage_id)
    # Reject symbolic-link ancestors as well as symbolic-link files.
    try:
        origin_rel = (
            Path(source.local_path).parent.relative_to(storage_root(origin_storage)).as_posix()
        )
    except ValueError as exc:
        raise HTTPException(409, "来源路径与存储根目录不匹配，请重新扫描") from exc
    directory_path(origin_storage, "" if origin_rel == "." else origin_rel)
    target_storage = await get_local_storage(session, body.storage_id)
    video = await session.get(Video, video_id)
    await check_category_destination(session, body.storage_id, body.path, video.media_type)
    destination = directory_path(target_storage, body.path) / original.name
    if original == destination:
        raise HTTPException(409, "视频已经位于该分类中")
    if destination.exists() or destination.is_symlink():
        raise HTTPException(409, "目标分类已存在同名文件，移动不会覆盖它")
    related = list(
        (
            await session.scalars(
                select(VideoSource).where(
                    VideoSource.local_path == source.local_path,
                )
            )
        ).all()
    )
    related_ids = {s.video_id for s in related}
    jobs = (
        await session.scalars(
            select(Job).where(
                Job.state.in_([JobState.queued, JobState.running]),
            )
        )
    ).all()
    affected_storages = {s.storage_id for s in related} | {body.storage_id}
    if any(
        j.id != job_id
        and (
            j.video_id in related_ids
            or (j.kind == "scan_library" and j.payload.get("storage_id") in affected_storages)
            or (j.kind == "move_video" and j.payload.get("_destination") == str(destination))
        )
        for j in jobs
    ):
        raise HTTPException(409, "视频或存储源正在处理任务，请完成或取消任务后再移动")

    # Reconcile overlapping storage roots without creating duplicate source URIs.
    plans, seen, remove = [], {}, []
    for ref in sorted(related, key=lambda r: (r.id != source.id, r.id)):
        owner = await session.get(Storage, ref.storage_id)
        root = storage_root(owner)
        if ref.id == source.id or not destination.is_relative_to(root):
            owner, root = target_storage, storage_root(target_storage)
        uri = f"local:{destination.relative_to(root).as_posix()}"
        key = owner.id, uri
        if key in seen:
            keeper = seen[key]
            if keeper.video_id != ref.video_id:
                raise HTTPException(409, "同一文件关联了多个独立视频记录，请先整理重复索引")
            remove.append(ref)
        else:
            seen[key] = ref
            plans.append((ref, owner.id, uri))
    for _ref, owner_id, uri in plans:
        conflict = await session.scalar(
            select(VideoSource.id)
            .where(
                VideoSource.storage_id == owner_id,
                VideoSource.uri == uri,
                VideoSource.id.not_in([r.id for r in related]),
            )
            .limit(1)
        )
        if conflict:
            raise HTTPException(409, "目标路径已有索引记录，请先重新扫描或整理该记录")
    return source, original, destination, target_storage, plans, remove


async def move_video(
    session,
    video_id: int,
    body: MoveVideoRequest,
    *,
    job_id=None,
    expected=None,
    progress=None,
    locked=False,
) -> dict:
    with nullcontext() if locked else library_write_lock():
        source, original, destination, target_storage, plans, remove = await prepare_move(
            session, video_id, body, job_id=job_id
        )
        if expected and (
            str(original) != expected.get("_original")
            or list(_fingerprint(original)) != expected.get("_fingerprint")
            or str(destination) != expected.get("_destination")
        ):
            raise HTTPException(409, "排队期间文件或目录发生变化，请刷新后重新移动")
        report = progress or (lambda *_: None)
        try:
            copied, before = await asyncio.to_thread(transfer_file, original, destination, progress)
        except FileExistsError as exc:
            raise HTTPException(409, "目标分类已出现同名文件，已停止移动") from exc
        except OSError as exc:
            raise HTTPException(409, f"移动失败，原文件保留：{exc.strerror or str(exc)}") from exc
        try:
            report(0.95, "正在更新影像库索引")
            for ref in remove:
                if ref.is_primary:
                    for keeper, _, _ in plans:
                        if keeper.video_id == ref.video_id:
                            keeper.is_primary = True
                            break
                await session.delete(ref)
            await session.flush()
            for ref, owner_id, uri in plans:
                ref.storage_id, ref.uri, ref.local_path = owner_id, uri, str(destination)
                ref.available, ref.unavailable_reason = True, None
                ref.last_seen_at = datetime.now(UTC)
            video = await session.get(Video, video_id)
            await remember_category(session, target_storage.id, body.path, video.media_type)
            result = {
                "video_id": video_id,
                "source_id": source.id,
                "local_path": str(destination),
                "filename": destination.name,
                "category": category_info(target_storage, body.path),
                "warning": None,
            }
            if job_id is not None:
                from sqlalchemy import update

                await session.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        result=result,
                        message="索引已更新，正在整理原文件",
                        progress=0.98,
                    )
                )
            await session.commit()
        except BaseException:
            await session.rollback()
            try:
                if copied:
                    destination.unlink()
                else:
                    move_without_replace(destination, original)
            except OSError:
                log.exception("移动索引回滚失败；请核对 %s 和 %s", original, destination)
            raise
        warning = None
        if copied:
            try:
                if _fingerprint(original) != before:
                    raise OSError("源文件已改变")
                original.unlink()
            except OSError:
                warning = "目标文件和索引已保存，但原文件未能清理；请手动核对后整理原文件"
        result["warning"] = warning
        if job_id is not None:
            from sqlalchemy import update

            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    state=JobState.done,
                    progress=1.0,
                    result=result,
                    message=warning or f"已移动到「{result['category']['name']}」",
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()
        return result
