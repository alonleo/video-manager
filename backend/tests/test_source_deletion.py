"""Destructive-operation tests use only temporary media and an isolated database."""

import os

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import source_deletion as sd
from app.api.videos import router
from app.config import settings
from app.db import get_session
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
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/delete.db")

    @event.listens_for(engine.sync_engine, "connect")
    def fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    root = tmp_path / "media"
    root.mkdir()
    paths = [root / "one.mp4", root / "two.mp4"]
    for path in paths:
        path.write_bytes(b"fixture video bytes" * 100)
    (root / "one.srt").write_text("Keep this subtitle")
    async with factory() as session:
        storage = Storage(name="test", type=StorageType.local, config={"root": str(root)})
        video = Video(title="临时视频", source_kind="local", status="indexed", is_favorite=True)
        tag = Tag(name="tag")
        session.add_all([storage, video, tag])
        await session.flush()
        sources = [
            VideoSource(
                video_id=video.id,
                storage_id=storage.id,
                source_type=StorageType.local,
                local_path=str(path),
                uri=f"local:{path.name}",
                is_primary=index == 0,
            )
            for index, path in enumerate(paths)
        ]
        session.add_all(sources)
        session.add(WatchHistory(video_id=video.id, position_ms=1234))
        session.add(VideoTag(video_id=video.id, tag_id=tag.id))
        await session.commit()
        ids = {"video": video.id, "storage": storage.id, "sources": [s.id for s in sources]}

    async def dependency():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = dependency
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client, factory, ids, paths
    await engine.dispose()


def url(ids, index=0):
    return f"/api/videos/{ids['video']}/sources/{ids['sources'][index]}/deletion"


async def body(client, target):
    response = await client.get(target)
    assert response.status_code == 200, response.text
    return {"confirmed": True, "confirmation": response.json()["confirmation"]}


async def test_preview_and_explicit_confirmation(env):
    client, _, ids, paths = env
    response = await client.get(url(ids))
    assert response.json()["path"] == str(paths[0])
    assert response.json()["remaining_sources"] == 1
    for payload in [{}, {"confirmed": False, "confirmation": response.json()["confirmation"]}]:
        assert (await client.post(url(ids), json=payload)).status_code == 422
    assert (
        await client.post(url(ids), json={"confirmed": True, "confirmation": "0" * 64})
    ).status_code == 409
    assert paths[0].exists() and paths[1].exists()


async def test_delete_selected_then_last_and_rescan(env):
    client, factory, ids, paths = env
    target = url(ids)
    result = await client.post(target, json=await body(client, target))
    assert result.status_code == 200 and result.json()["deleted"]
    assert not result.json()["video_removed"]
    assert not paths[0].exists() and paths[1].exists()
    detail = (await client.get(f"/api/videos/{ids['video']}")).json()
    assert detail["is_favorite"] and detail["position_ms"] == 1234
    assert len(detail["tags"]) == 1 and len(detail["sources"]) == 1
    assert detail["sources"][0]["is_primary"]
    target = url(ids, 1)
    result = await client.post(target, json=await body(client, target))
    assert result.json()["video_removed"] and result.json()["deleted"]
    assert (await client.get(f"/api/videos/{ids['video']}")).status_code == 404
    assert paths[0].parent.is_dir()
    assert (paths[0].parent / "one.srt").read_text() == "Keep this subtitle"
    assert not list(paths[0].parent.glob(".video-manage-delete-*"))
    async with factory() as session:
        assert await session.get(WatchHistory, ids["video"]) is None
        assert await session.scalar(select(Tag)) is not None
        await scan_storage(session, ids["storage"], do_probe=False)
        assert await session.scalar(select(Video)) is None


@pytest.mark.parametrize(
    "kind", ["convert_format", "move_video", "subtitle_generate", "enhance_video", "scan_library"]
)
@pytest.mark.parametrize("state", [JobState.queued, JobState.running])
async def test_active_jobs_block_deletion(env, kind, state):
    client, factory, ids, paths = env
    confirmation = await body(client, url(ids))
    async with factory() as session:
        session.add(
            Job(
                kind=kind,
                state=state,
                video_id=None if kind == "scan_library" else ids["video"],
                payload={"storage_id": ids["storage"]},
            )
        )
        await session.commit()
    assert (await client.post(url(ids), json=confirmation)).status_code == 409
    assert paths[0].exists()


@pytest.mark.parametrize("name", [".directory-write.lock", ".move-submission.lock"])
async def test_file_operation_locks(env, name):
    client, _, ids, paths = env
    confirmation = await body(client, url(ids))
    with library_write_lock(name):
        assert (await client.post(url(ids), json=confirmation)).status_code == 409
    assert paths[0].exists()


@pytest.mark.parametrize("change", ["contents", "replacement", "remaining"])
async def test_stale_confirmation(env, change):
    client, factory, ids, paths = env
    confirmation = await body(client, url(ids))
    if change == "contents":
        paths[0].write_bytes(b"changed")
    elif change == "replacement":
        paths[0].rename(paths[0].with_suffix(".saved"))
        paths[0].write_bytes(b"fixture video bytes" * 100)
    else:
        async with factory() as session:
            await session.delete(await session.get(VideoSource, ids["sources"][1]))
            await session.commit()
    assert (await client.post(url(ids), json=confirmation)).status_code == 409
    assert paths[0].exists()


@pytest.mark.parametrize(
    "unsafe", ["symlink", "directory_symlink", "outside", "directory", "remote"]
)
async def test_reject_unsafe_sources(env, unsafe):
    client, factory, ids, paths = env
    external = paths[0].parent.parent / "outside.mp4"
    external.write_bytes(b"outside")
    async with factory() as session:
        source = await session.get(VideoSource, ids["sources"][0])
        if unsafe == "symlink":
            paths[0].unlink()
            paths[0].symlink_to(external)
        elif unsafe == "directory_symlink":
            alias = paths[0].parent / "alias"
            alias.symlink_to(paths[0].parent, target_is_directory=True)
            source.local_path = str(alias / paths[0].name)
        elif unsafe == "outside":
            source.local_path = str(external)
        elif unsafe == "directory":
            source.local_path = str(paths[0].parent)
        else:
            source.source_type = StorageType.url
        await session.commit()
    assert (await client.get(url(ids))).status_code in (400, 409)
    assert external.read_bytes() == b"outside"


async def test_overlapping_storage_refs(env):
    client, factory, ids, paths = env
    async with factory() as session:
        storage = Storage(
            name="overlap", type=StorageType.local, config={"root": str(paths[0].parent.parent)}
        )
        session.add(storage)
        await session.flush()
        session.add(
            VideoSource(
                video_id=ids["video"],
                storage_id=storage.id,
                source_type=StorageType.local,
                local_path=str(paths[0]),
                uri="local:media/one.mp4",
            )
        )
        await session.commit()
    assert (await client.get(url(ids))).json()["affected_sources"] == 2
    result = await client.post(url(ids), json=await body(client, url(ids)))
    assert result.json()["remaining_sources"] == 1
    assert not paths[0].exists() and paths[1].exists()


async def test_other_video_reference_is_not_deleted(env):
    client, factory, ids, paths = env
    async with factory() as session:
        other = Video(title="Other")
        session.add(other)
        await session.flush()
        session.add(
            VideoSource(
                video_id=other.id,
                storage_id=ids["storage"],
                source_type=StorageType.local,
                local_path=str(paths[0]),
                uri="local:duplicate",
            )
        )
        await session.commit()
    assert (await client.get(url(ids))).status_code == 409
    assert paths[0].exists()


async def test_database_failure_restores_file(env, monkeypatch):
    client, _, ids, paths = env
    confirmation = await body(client, url(ids))
    original = paths[0].read_bytes()

    async def fail_commit(self):
        raise RuntimeError("test database failure")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    assert (await client.post(url(ids), json=confirmation)).status_code == 500
    assert paths[0].read_bytes() == original
    assert len((await client.get(f"/api/videos/{ids['video']}")).json()["sources"]) == 2
    assert not list(paths[0].parent.glob(".video-manage-delete-*"))


async def test_permission_failure_keeps_file(env, monkeypatch):
    client, _, ids, paths = env
    confirmation = await body(client, url(ids))

    def denied(*args, **kwargs):
        raise PermissionError("test permission denied")

    monkeypatch.setattr(sd.os, "rename", denied)
    assert (await client.post(url(ids), json=confirmation)).status_code == 409
    assert paths[0].exists()
    assert not list(paths[0].parent.glob(".video-manage-delete-*"))


async def test_late_file_swap_is_restored(env, monkeypatch):
    client, _, ids, paths = env
    confirmation = await body(client, url(ids))
    rename = os.rename

    def swap_then_rename(src, dst, **kwargs):
        rename(paths[0], paths[0].with_suffix(".saved"))
        paths[0].write_bytes(b"replacement must not be deleted")
        rename(src, dst, **kwargs)

    monkeypatch.setattr(sd.os, "rename", swap_then_rename)
    assert (await client.post(url(ids), json=confirmation)).status_code == 409
    assert paths[0].read_bytes() == b"replacement must not be deleted"
    assert paths[0].with_suffix(".saved").exists()


async def test_purge_failure_is_explicit_and_recoverable(env, monkeypatch):
    client, _, ids, paths = env
    confirmation = await body(client, url(ids))

    def failed_purge(self):
        raise PermissionError("test purge error")

    monkeypatch.setattr(sd.StagedFile, "purge", failed_purge)
    result = (await client.post(url(ids), json=confirmation)).json()
    assert result["deleted"] is False and "清理未完成" in result["warning"]
    retained = list(paths[0].parent.glob(".video-manage-delete-*/original"))
    assert len(retained) == 1 and retained[0].read_bytes() == b"fixture video bytes" * 100
    assert not paths[0].exists()


async def test_cross_origin_and_wrong_source(env):
    client, _, ids, paths = env
    confirmation = await body(client, url(ids))
    response = await client.post(
        url(ids), json=confirmation, headers={"Origin": "https://other.test"}
    )
    assert response.status_code == 403
    assert (
        await client.get(f"/api/videos/99999/sources/{ids['sources'][0]}/deletion")
    ).status_code == 404
    assert paths[0].exists()
