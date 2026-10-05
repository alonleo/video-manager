"""Create, configure and explicitly retry URL download jobs."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Job, JobState, Setting, Storage, StorageType
from app.schemas import JobOut
from app.services.directory_categories import (
    directory_path,
    get_local_storage,
    library_write_lock,
)
from app.services.download_config import (
    KEY,
    DownloadConfig,
    DownloadRequest,
    engine_status,
    read_config,
    validate_public_host,
)
from app.workers.queue import enqueue_job

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


class DownloadJobOut(JobOut):
    payload: dict


def serialize_job(job):
    data = JobOut.model_validate(job).model_dump()
    data["payload"] = {k: v for k, v in job.payload.items() if k in DownloadRequest.model_fields}
    return DownloadJobOut(**data)


@router.get("")
async def list_downloads(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.scalars(
            select(Job).where(Job.kind == "download_url").order_by(Job.id.desc()).limit(50)
        )
    ).all()
    return {"items": [serialize_job(j) for j in rows], "total": len(rows)}


@router.get("/status")
async def status():
    return engine_status()


@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_session)):
    return await read_config(session)


@router.put("/settings")
async def save_settings(body: DownloadConfig, session: AsyncSession = Depends(get_session)):
    if body.cookies_browser and body.cookie_file:
        raise HTTPException(422, "浏览器 Cookie 与 Cookie 文件请选择一种")
    if body.cookie_file:
        from pathlib import Path

        if not Path(body.cookie_file).is_file():
            raise HTTPException(422, "Cookie 文件不存在；请填写后端电脑上的文件路径")
    row = await session.get(Setting, KEY)
    if row is None:
        session.add(Setting(key=KEY, value=body.model_dump()))
    else:
        row.value = body.model_dump()
    await session.commit()
    return body


@router.post("/cookies/check")
async def check_cookies(body: DownloadConfig):
    from app.services.download_cookies import download_environment
    from app.services.downloads import terminate

    if body.cookies_browser and body.cookie_file:
        raise HTTPException(422, "浏览器 Cookie 与 Cookie 文件请选择一种")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.services.cookie_probe",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=download_environment(),
        start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(body.model_dump_json().encode()), 30)
        if process.returncode:
            raise HTTPException(422, "Cookie 检测失败，请检查浏览器配置与桌面密钥环")
        return json.loads(output)
    except TimeoutError:
        raise HTTPException(422, "Cookie 检测超时，请先解锁桌面密钥环后重试") from None
    finally:
        await terminate(process)


async def destination(session, body: DownloadRequest) -> Storage:
    if body.storage_id is not None:
        storage = await get_local_storage(session, body.storage_id)
    else:
        if body.path:
            raise HTTPException(422, "使用自定义分类前请选择存储源")
        root = (settings.downloads_dir / "online").resolve()
        root.mkdir(parents=True, exist_ok=True)
        storages = (
            await session.scalars(select(Storage).where(Storage.type == StorageType.local))
        ).all()
        storage = next((s for s in storages if (s.config or {}).get("root") == str(root)), None)
        if storage is None:
            name = "链接下载"
            names = {s.name for s in (await session.scalars(select(Storage))).all()}
            if name in names:
                name += f" {uuid.uuid4().hex[:6]}"
            storage = Storage(name=name, type=StorageType.local, config={"root": str(root)})
            session.add(storage)
            await session.flush()
        if storage.enabled is False:
            raise HTTPException(409, "默认下载存储源已禁用，请先启用或选择其他分类")
    directory_path(storage, body.path)
    return storage


async def submit(body: DownloadRequest, session, token: str | None = None) -> JobOut:
    status = engine_status()
    if not status["installed"] or not status["ffmpeg"]:
        raise HTTPException(503, "下载引擎或 FFmpeg 未安装，请按项目 DOWNLOADS.md 安装依赖")
    await validate_public_host(body.url)
    with library_write_lock():
        rows = (
            await session.scalars(
                select(Job).where(
                    Job.kind == "download_url", Job.state.in_([JobState.queued, JobState.running])
                )
            )
        ).all()
        duplicate = next((j for j in rows if j.payload.get("url") == body.url), None)
        if duplicate:
            raise HTTPException(409, f"此链接已有任务 #{duplicate.id}，请在下载记录中查看")
        storage = await destination(session, body)
        payload = body.model_dump()
        payload.update(storage_id=storage.id, download_token=token or uuid.uuid4().hex)
        await session.commit()
        job_id = await enqueue_job("download_url", **payload)
        if job_id is None:
            raise HTTPException(503, "下载任务未能入队，请检查任务队列后重试")
        job = await session.get(Job, job_id)
        return serialize_job(job)


@router.post("", status_code=202, response_model=DownloadJobOut)
async def create_download(body: DownloadRequest, session: AsyncSession = Depends(get_session)):
    return await submit(body, session)


@router.post("/{job_id}/retry", status_code=202, response_model=DownloadJobOut)
async def retry_download(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if job is None or job.kind != "download_url":
        raise HTTPException(404, "下载任务不存在")
    if job.state not in (JobState.error, JobState.canceled):
        raise HTTPException(409, "只有失败或已取消的下载可以重试")
    # Canceled workers acknowledge termination before their workspace can be reused.
    token = job.payload.get("download_token")
    from app.services.downloads import workspace

    stage = workspace(job.payload)
    import fcntl

    if stage.exists():
        with (stage / ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise HTTPException(409, "正在停止下载进程，请稍后重试") from exc
    body = DownloadRequest.model_validate(
        {key: value for key, value in job.payload.items() if key in DownloadRequest.model_fields}
    )
    return await submit(body, session, token)
