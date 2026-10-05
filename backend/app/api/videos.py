"""视频库 API：列表/详情/编辑/标签/收藏/进度。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.source_deletion import router as source_deletion_router
from app.db import get_session
from app.models import Job, JobState, Tag, Video, VideoSource, VideoTag, WatchHistory
from app.schemas import (
    DirectoryCategory,
    ProgressOut,
    ProgressUpdate,
    TagAssign,
    TagOut,
    VideoDetail,
    VideoListItem,
    VideoListResponse,
    VideoSourceOut,
    VideoUpdate,
)
from app.services.directory_categories import (
    category_video_ids,
    library_write_lock,
    video_categories,
)

router = APIRouter(prefix="/api/videos", tags=["videos"])
router.include_router(source_deletion_router)


def _to_list_item(video: Video, history: WatchHistory | None) -> VideoListItem:
    item = VideoListItem.model_validate(video)
    if history is not None:
        item.position_ms = history.position_ms
        item.finished = history.finished
    return item


def _to_detail(
    video: Video, sources: list[VideoSource], history: WatchHistory | None
) -> VideoDetail:
    """显式构造详情。

    不能直接 model_validate(video) —— VideoDetail 含 sources 字段，pydantic 会
    去访问 Video.sources 关系，在 async 上下文里触发同步懒加载从而报
    MissingGreenlet。因此从列属性构造，再手工塞入已查好的 sources。
    """
    data = {c.name: getattr(video, c.name) for c in video.__table__.columns}
    data["tags"] = video.tags  # selectin 加载，安全
    detail = VideoDetail.model_validate(data)
    detail.sources = [VideoSourceOut.model_validate(s) for s in sources]
    if history is not None:
        detail.position_ms = history.position_ms
        detail.finished = history.finished
    return detail


@router.get("", response_model=VideoListResponse)
async def list_videos(
    media_type: str = Query("video", pattern="^(video|photo)$"),
    category: str | None = Query(None, description="父目录分类标识：存储源ID:相对目录"),
    q: str | None = Query(None, description="标题/描述模糊搜索"),
    tag: str | None = Query(None, description="标签名"),
    source: str | None = Query(None, description="local|openlist|url"),
    status: str | None = None,
    favorite: bool | None = None,
    sort: str = Query(
        "created_desc",
        pattern="^(created_desc|created_asc|title|duration_desc|size_desc)$",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Video)
    count_stmt = select(func.count(Video.id))

    conditions = []
    if media_type:
        conditions.append(Video.media_type == media_type)
    if category:
        conditions.append(Video.id.in_(await category_video_ids(session, category)))
    if q:
        like = f"%{q}%"
        conditions.append(or_(Video.title.ilike(like), Video.description.ilike(like)))
    if status:
        conditions.append(Video.status == status)
    if favorite is not None:
        conditions.append(Video.is_favorite.is_(favorite))
    if source:
        conditions.append(Video.source_kind == source)
    if tag:
        sub = (
            select(VideoTag.video_id)
            .join(Tag, Tag.id == VideoTag.tag_id)
            .where(Tag.name == tag, Tag.media_type == media_type)
        )
        conditions.append(Video.id.in_(sub))

    for c in conditions:
        stmt = stmt.where(c)
        count_stmt = count_stmt.where(c)

    order = {
        "created_desc": Video.created_at.desc(),
        "created_asc": Video.created_at.asc(),
        "title": Video.title.asc(),
        "duration_desc": Video.duration_ms.desc().nullslast(),
        "size_desc": Video.size_bytes.desc().nullslast(),
    }[sort]
    stmt = stmt.order_by(order, Video.id.desc()).offset((page - 1) * page_size).limit(page_size)

    videos = (await session.execute(stmt)).scalars().unique().all()
    total = (await session.execute(count_stmt)).scalar_one()

    # 一次性把进度取回来，避免 N+1
    ids = [v.id for v in videos]
    histories: dict[int, WatchHistory] = {}
    if ids:
        rows = (
            await session.execute(select(WatchHistory).where(WatchHistory.video_id.in_(ids)))
        ).scalars().all()
        histories = {h.video_id: h for h in rows}

    categories_by_video = await video_categories(session, ids)
    items = [_to_list_item(v, histories.get(v.id)) for v in videos]
    for item in items:
        item.categories = [DirectoryCategory(**c) for c in categories_by_video.get(item.id, [])]
    return VideoListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{video_id}", response_model=VideoDetail)
async def get_video(video_id: int, session: AsyncSession = Depends(get_session)):
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")

    sources = (
        await session.execute(
            select(VideoSource)
            .where(VideoSource.video_id == video_id)
            .order_by(VideoSource.is_primary.desc(), VideoSource.id)
        )
    ).scalars().all()
    history = await session.get(WatchHistory, video_id)
    detail = _to_detail(video, list(sources), history)
    detail.categories = [
        DirectoryCategory(**c)
        for c in (await video_categories(session, [video_id])).get(video_id, [])
    ]
    return detail


@router.patch("/{video_id}", response_model=VideoDetail)
async def update_video(
    video_id: int, payload: VideoUpdate, session: AsyncSession = Depends(get_session)
):
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(video, k, v)
    await session.commit()
    await session.refresh(video)
    return await get_video(video_id, session)


@router.delete("/{video_id}", status_code=204)
async def delete_video(video_id: int, session: AsyncSession = Depends(get_session)):
    """移除收录及关联记录，保留原文件；下次显式扫描才重新入库。"""
    # Serialize with scans/rename/move so a scan cannot silently undo removal.
    with library_write_lock():
        video = await session.get(Video, video_id)
        if video is None:
            raise HTTPException(404, "视频不存在")
        storage_ids = set(await session.scalars(
            select(VideoSource.storage_id).where(VideoSource.video_id == video_id)
        ))
        active_jobs = await session.scalars(
            select(Job).where(Job.state.in_([JobState.queued, JobState.running]))
        )
        for job in active_jobs:
            if job.video_id == video_id:
                raise HTTPException(409, "该视频还有处理任务，请完成或取消任务后再移除收录")
            if job.kind == "scan_library" and (job.payload or {}).get("storage_id") in storage_ids:
                raise HTTPException(409, "存储源正在扫描或等待扫描，请扫描完成后再移除收录")
        await session.delete(video)
        await session.commit()


# ---------------------------------------------------------------- 标签


@router.post("/{video_id}/tags", response_model=list[TagOut])
async def add_tag(
    video_id: int, payload: TagAssign, session: AsyncSession = Depends(get_session)
):
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")

    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")

    tag = (await session.execute(select(Tag).where(Tag.name == name, Tag.media_type == video.media_type))).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name, media_type=video.media_type)
        session.add(tag)
        await session.flush()

    exists = await session.get(VideoTag, (video_id, tag.id))
    if exists is None:
        session.add(VideoTag(video_id=video_id, tag_id=tag.id))
    await session.commit()

    await session.refresh(video)
    return [TagOut.model_validate(t) for t in video.tags]


@router.delete("/{video_id}/tags/{tag_id}", response_model=list[TagOut])
async def remove_tag(
    video_id: int, tag_id: int, session: AsyncSession = Depends(get_session)
):
    link = await session.get(VideoTag, (video_id, tag_id))
    if link is not None:
        await session.delete(link)
        await session.commit()

    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")
    await session.refresh(video)
    return [TagOut.model_validate(t) for t in video.tags]


# ---------------------------------------------------------------- 进度


@router.put("/{video_id}/progress", response_model=ProgressOut)
async def save_progress(
    video_id: int, payload: ProgressUpdate, session: AsyncSession = Depends(get_session)
):
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")

    if video.media_type == "photo":
        raise HTTPException(422, "照片没有播放进度")

    h = await session.get(WatchHistory, video_id)
    if h is None:
        h = WatchHistory(video_id=video_id)
        session.add(h)

    h.position_ms = payload.position_ms
    if payload.played_ms is not None:
        h.played_ms = payload.played_ms
    if payload.finished is not None:
        h.finished = payload.finished
    elif video.duration_ms:
        # 看到 95% 以上视为看完
        h.finished = payload.position_ms >= video.duration_ms * 0.95

    await session.commit()
    await session.refresh(h)
    return ProgressOut.model_validate(h)


@router.get("/{video_id}/progress", response_model=ProgressOut | None)
async def get_progress(video_id: int, session: AsyncSession = Depends(get_session)):
    h = await session.get(WatchHistory, video_id)
    return ProgressOut.model_validate(h) if h else None
