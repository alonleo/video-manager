"""Global tag deletion: associations disappear, videos/files/history remain."""

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_session
from app.main import app
from app.models import Base, Storage, StorageType, Tag, Video, VideoSource, VideoTag, WatchHistory


async def seed_tags(factory, root: Path):
    root.mkdir(parents=True, exist_ok=True)
    async with factory() as session:
        storage = Storage(name="tag validation", type=StorageType.local, config={"root": str(root)})
        one, two = Video(title="验证视频一"), Video(title="验证视频二")
        target, keep, unused = Tag(name="测试"), Tag(name="保留"), Tag(name="未使用")
        session.add_all([storage, one, two, target, keep, unused])
        await session.flush()
        for video in [one, two]:
            path = root / f"{video.id}.mp4"
            path.write_bytes(b"unchanged original fixture")
            session.add(
                VideoSource(
                    video_id=video.id,
                    storage_id=storage.id,
                    source_type="local",
                    uri=str(path),
                    local_path=str(path),
                    is_primary=True,
                    available=True,
                )
            )
            session.add(VideoTag(video_id=video.id, tag_id=target.id))
        session.add(VideoTag(video_id=one.id, tag_id=keep.id))
        session.add(WatchHistory(video_id=one.id, position_ms=4000, played_ms=5000))
        await session.commit()
        return {
            "videos": [one.id, two.id],
            "target": target.id,
            "keep": keep.id,
            "unused": unused.id,
        }


@pytest_asyncio.fixture
async def tag_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(conn, _record):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed_tags(factory, tmp_path / "media")

    async def dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, factory, ids, tmp_path / "media"
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_counts_including_unused(tag_env):
    client, _, ids, _ = tag_env
    response = await client.get("/api/tags")
    assert response.status_code == 200
    counts = {t["id"]: t["video_count"] for t in response.json()}
    assert counts == {ids["target"]: 2, ids["keep"]: 1, ids["unused"]: 0}


@pytest.mark.asyncio
async def test_global_delete_preserves_everything_else(tag_env):
    client, factory, ids, media = tag_env
    response = await client.delete(f"/api/tags/{ids['target']}?expected_count=2")
    assert response.status_code == 204 and not response.content
    assert ids["target"] not in {t["id"] for t in (await client.get("/api/tags")).json()}
    async with factory() as session:
        assert await session.get(Tag, ids["target"]) is None
        assert (
            list(
                (
                    await session.execute(select(VideoTag).where(VideoTag.tag_id == ids["target"]))
                ).scalars()
            )
            == []
        )
        assert len(list((await session.execute(select(Video))).scalars())) == 2
        assert len(list((await session.execute(select(VideoSource))).scalars())) == 2
        assert await session.get(VideoTag, (ids["videos"][0], ids["keep"])) is not None
        assert (await session.get(WatchHistory, ids["videos"][0])).position_ms == 4000
    for video_id in ids["videos"]:
        assert (media / f"{video_id}.mp4").read_bytes() == b"unchanged original fixture"
        detail = (await client.get(f"/api/videos/{video_id}")).json()
        assert all(t["id"] != ids["target"] for t in detail["tags"])
    assert (await client.delete(f"/api/tags/{ids['target']}")).status_code == 404


@pytest.mark.asyncio
async def test_delete_unused_tag(tag_env):
    client, _, ids, _ = tag_env
    assert (await client.delete(f"/api/tags/{ids['unused']}?expected_count=0")).status_code == 204
    assert len((await client.get("/api/tags")).json()) == 2


@pytest.mark.asyncio
async def test_stale_confirmation_does_not_delete(tag_env):
    client, factory, ids, _ = tag_env
    response = await client.delete(f"/api/tags/{ids['target']}?expected_count=1")
    assert response.status_code == 409 and "数量已变化" in response.json()["detail"]
    async with factory() as session:
        assert await session.get(Tag, ids["target"]) is not None
        assert (
            len(
                list(
                    (
                        await session.execute(
                            select(VideoTag).where(VideoTag.tag_id == ids["target"])
                        )
                    ).scalars()
                )
            )
            == 2
        )


@pytest.mark.asyncio
async def test_invalid_and_missing_tag(tag_env):
    client, _, ids, _ = tag_env
    assert (await client.delete("/api/tags/99999")).status_code == 404
    assert (await client.delete(f"/api/tags/{ids['target']}?expected_count=-1")).status_code == 422


@pytest.mark.asyncio
async def test_video_unassign_keeps_global_tag(tag_env):
    client, _, ids, _ = tag_env
    response = await client.delete(f"/api/videos/{ids['videos'][0]}/tags/{ids['target']}")
    assert response.status_code == 200
    tags = (await client.get("/api/tags")).json()
    assert next(t for t in tags if t["id"] == ids["target"])["video_count"] == 1
