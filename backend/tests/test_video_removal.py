"""Removing library entries keeps media intact until an explicit rescan."""
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import (
    Base,
    Job,
    JobState,
    Storage,
    StorageType,
    Tag,
    Video,
    VideoSource,
    VideoTag,
    WatchHistory,
)
from app.services.directory_categories import library_write_lock
from app.services.library import scan_storage


@pytest_asyncio.fixture
async def removal_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/removal.db")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(conn, _record):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    media = [tmp_path / "one", tmp_path / "two", tmp_path / "unrelated"]
    for root in media:
        root.mkdir()
    original = b"original media fixture, never delete or rewrite" * 100
    for root in media[:2]:
        (root / "movie.mp4").write_bytes(original)
    async with factory() as session:
        storages = [
            Storage(name=root.name, type=StorageType.local, config={"root": str(root)})
            for root in media
        ]
        session.add_all(storages)
        await session.commit()
        storage_ids = [s.id for s in storages]
        for sid in storage_ids[:2]:
            await scan_storage(session, sid, do_probe=False)
        video = await session.scalar(select(Video))
        video.is_favorite = True
        tag = Tag(name="保留标签")
        session.add(tag)
        await session.flush()
        session.add(VideoTag(video_id=video.id, tag_id=tag.id))
        session.add(WatchHistory(video_id=video.id, position_ms=1234))
        await session.commit()
        ids = {"video": video.id, "tag": tag.id, "storages": storage_ids}

    async def dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, factory, ids, media, original
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_remove_only_reappears_after_rescan(removal_env):
    client, factory, ids, media, original = removal_env
    url = f"/api/videos/{ids['video']}"
    assert len((await client.get(url)).json()["sources"]) == 2
    result = await client.delete(url)
    assert result.status_code == 204 and not result.content
    assert (await client.get(url)).status_code == 404
    assert (await client.delete(url)).status_code == 404
    # Fresh sessions and repeated page refreshes never scan/import media.
    for _ in range(2):
        assert (await client.get("/api/videos")).json()["total"] == 0
        assert (await client.get("/api/videos?favorite=true")).json()["total"] == 0
        assert (await client.get("/api/videos?tag=保留标签")).json()["total"] == 0
    async with factory() as session:
        for model in [Video, VideoSource, VideoTag, WatchHistory]:
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        assert await session.get(Tag, ids["tag"]) is not None
        assert await session.scalar(select(func.count()).select_from(Storage)) == 3
    categories = (await client.get("/api/categories")).json()["items"]
    assert all(c["video_count"] == 0 for c in categories)
    assert (await client.get("/api/tags")).json()[0]["video_count"] == 0
    for root in media[:2]:
        assert (root / "movie.mp4").read_bytes() == original
    async with factory() as session:
        await scan_storage(session, ids["storages"][2], do_probe=False)
    assert (await client.get("/api/videos")).json()["total"] == 0
    async with factory() as session:
        stats = await scan_storage(session, ids["storages"][0], do_probe=False)
        assert stats.created == 1
    items = (await client.get("/api/videos")).json()["items"]
    assert len(items) == 1 and items[0]["is_favorite"] is False
    assert items[0]["tags"] == []
    async with factory() as session:
        stats = await scan_storage(session, ids["storages"][1], do_probe=False)
        assert stats.linked == 1
    assert (await client.get("/api/videos")).json()["total"] == 1
    for root in media[:2]:
        assert (root / "movie.mp4").read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [JobState.queued, JobState.running])
@pytest.mark.parametrize("kind", ["convert_format", "scan_library"])
async def test_active_work_blocks_removal(removal_env, state, kind):
    client, factory, ids, media, original = removal_env
    async with factory() as session:
        job = Job(
            kind=kind, state=state,
            video_id=ids["video"] if kind == "convert_format" else None,
            payload={"storage_id": ids["storages"][0]} if kind == "scan_library" else {},
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    result = await client.delete(f"/api/videos/{ids['video']}")
    assert result.status_code == 409
    assert (await client.get("/api/videos")).json()["total"] == 1
    for root in media[:2]:
        assert (root / "movie.mp4").read_bytes() == original
    async with factory() as session:
        job = await session.get(Job, job_id)
        job.state = JobState.canceled
        await session.commit()
    assert (await client.delete(f"/api/videos/{ids['video']}")).status_code == 204


@pytest.mark.asyncio
async def test_concurrent_library_write_blocks_removal(removal_env):
    client, _, ids, _, _ = removal_env
    with library_write_lock():
        response = await client.delete(f"/api/videos/{ids['video']}")
    assert response.status_code == 409
    assert (await client.get("/api/videos")).json()["total"] == 1


@pytest.mark.asyncio
async def test_unrelated_scan_does_not_block(removal_env):
    client, factory, ids, _, _ = removal_env
    async with factory() as session:
        session.add(Job(
            kind="scan_library", state=JobState.queued,
            payload={"storage_id": ids["storages"][2]},
        ))
        await session.commit()
    assert (await client.delete(f"/api/videos/{ids['video']}")).status_code == 204
