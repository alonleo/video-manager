"""存储源管理与扫描触发。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Storage, StorageType, Video, VideoSource
from app.schemas import (
    ScanRequest,
    ScanResponse,
    StorageCreate,
    StorageOut,
    StorageTestResult,
    StorageUpdate,
)
from app.services.library import get_provider

router = APIRouter(prefix="/api/storages", tags=["storages"])


@router.get("", response_model=list[StorageOut])
async def list_storages(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Storage).order_by(Storage.id))).scalars().all()
    return [StorageOut.model_validate(s) for s in rows]


@router.post("", response_model=StorageOut, status_code=201)
async def create_storage(payload: StorageCreate, session: AsyncSession = Depends(get_session)):
    exists = (
        await session.execute(select(Storage).where(Storage.name == payload.name))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(409, f"同名存储源已存在: {payload.name}")

    if payload.type == "local" and not payload.config.get("root"):
        raise HTTPException(400, "local 类型必须提供 config.root")

    storage = Storage(
        name=payload.name,
        type=StorageType(payload.type),
        enabled=payload.enabled,
        config=payload.config,
    )
    session.add(storage)
    await session.commit()
    await session.refresh(storage)
    return StorageOut.model_validate(storage)


@router.patch("/{storage_id}", response_model=StorageOut)
async def update_storage(
    storage_id: int, payload: StorageUpdate, session: AsyncSession = Depends(get_session)
):
    storage = await session.get(Storage, storage_id)
    if storage is None:
        raise HTTPException(404, "存储源不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(storage, k, v)
    await session.commit()
    await session.refresh(storage)
    return StorageOut.model_validate(storage)


@router.delete("/{storage_id}", status_code=204)
async def delete_storage(storage_id: int, session: AsyncSession = Depends(get_session)):
    """删除存储源配置。

    只删除索引数据，**不动磁盘上的视频文件**。

    为什么必须显式清理：video_sources.storage_id 是 NOT NULL，若直接
    delete(storage)，SQLAlchemy 会尝试把子行的 storage_id 置 NULL 而违反约束
    （报 IntegrityError）。因此按顺序手工处理：

      1. 删掉该 storage 下的所有 video_sources
      2. 删掉因此失去全部来源的 videos（否则库里会留下点不进去的空条目）
    """
    storage = await session.get(Storage, storage_id)
    if storage is None:
        raise HTTPException(404, "存储源不存在")

    # 先记下受影响的 video_id，删完 source 后再判断哪些成了孤儿
    affected = (
        await session.execute(
            select(VideoSource.video_id).where(VideoSource.storage_id == storage_id).distinct()
        )
    ).scalars().all()

    await session.execute(
        delete(VideoSource).where(VideoSource.storage_id == storage_id)
    )

    if affected:
        # 仍有其他来源的 video 保留（多源视频）
        still_has_source = (
            await session.execute(
                select(VideoSource.video_id).where(VideoSource.video_id.in_(affected)).distinct()
            )
        ).scalars().all()
        orphans = set(affected) - set(still_has_source)
        if orphans:
            await session.execute(delete(Video).where(Video.id.in_(orphans)))

    await session.delete(storage)
    await session.commit()


@router.post("/{storage_id}/test", response_model=StorageTestResult)
async def test_storage(storage_id: int, session: AsyncSession = Depends(get_session)):
    storage = await session.get(Storage, storage_id)
    if storage is None:
        raise HTTPException(404, "存储源不存在")
    try:
        provider = get_provider(storage)
        ok, msg = await provider.test()
    except NotImplementedError as e:
        return StorageTestResult(ok=False, message=str(e))
    except Exception as e:
        return StorageTestResult(ok=False, message=f"自检异常: {e}")
    return StorageTestResult(ok=ok, message=msg)


@router.post("/{storage_id}/scan", response_model=ScanResponse)
async def scan_storage(
    storage_id: int, payload: ScanRequest, session: AsyncSession = Depends(get_session)
):
    """入队扫描任务。API 进程不直接跑扫描，避免长任务阻塞请求。"""
    storage = await session.get(Storage, storage_id)
    if storage is None:
        raise HTTPException(404, "存储源不存在")
    if not storage.enabled:
        raise HTTPException(400, "存储源已禁用")

    from app.workers.queue import enqueue_job

    job_id = await enqueue_job(
        "scan_library",
        storage_id=storage_id,
        sub_path=payload.path,
        do_probe=payload.probe,
    )
    if job_id is None:
        raise HTTPException(
            503, "任务队列不可用（Redis 未启动？）。可先用同步扫描接口。"
        )
    return ScanResponse(job_id=job_id, message="扫描任务已入队")


@router.post("/{storage_id}/browse")
async def browse_storage(
    storage_id: int,
    path: str = "",
    session: AsyncSession = Depends(get_session),
):
    """浏览存储源目录，供前端选择扫描子路径。"""
    storage = await session.get(Storage, storage_id)
    if storage is None:
        raise HTTPException(404, "存储源不存在")
    try:
        provider = get_provider(storage)
        entries = await provider.list_dir(path)
    except NotImplementedError as e:
        raise HTTPException(501, str(e)) from None
    except Exception as e:
        raise HTTPException(400, f"读取目录失败: {e}") from None
    return {
        "path": path,
        "entries": [
            {"name": e.name, "path": e.path, "is_dir": e.is_dir, "size": e.size}
            for e in sorted(entries, key=lambda x: (not x.is_dir, x.name.lower()))
        ],
    }
