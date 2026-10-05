"""Real filesystem + isolated SQLite tests. Never mutate the personal video library."""

import errno

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import categories, videos
from app.config import settings
from app.db import get_session
from app.models import Base, Job, JobState, Storage, StorageType, Video, VideoSource, WatchHistory
from app.providers.local import LocalProvider
from app.services import directory_categories as dc
from app.services.library import scan_storage


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    root, other = tmp_path / "media", tmp_path / "other"
    for directory in [root / "旅行", root / "课程", other / "旅行"]:
        directory.mkdir(parents=True)
    original = root / "旅行" / "海边.mp4"
    original.write_bytes(b"video-content" * 100)
    async with factory() as session:
        a = Storage(name="本地影像", type=StorageType.local, config={"root": str(root)})
        b = Storage(name="归档盘", type=StorageType.local, config={"root": str(other)})
        v = Video(title="海边", is_favorite=True, source_kind="local", status="indexed", tags=[])
        session.add_all([a, b, v])
        await session.flush()
        source = VideoSource(
            video_id=v.id,
            storage_id=a.id,
            source_type=StorageType.local,
            uri="local:旅行/海边.mp4",
            local_path=str(original),
            is_primary=True,
        )
        history = WatchHistory(video_id=v.id, position_ms=12345, played_ms=1000)
        session.add_all([source, history])
        await session.commit()
        yield session, root, other, a, b, v, source
    await engine.dispose()


async def test_existing_parent_categories_and_empty_directories(env):
    session, root, other, a, b, video, source = env
    result = await dc.list_categories(session)
    lookup = {c["id"]: c for c in result["items"]}
    assert lookup[f"{a.id}:旅行"]["video_count"] == 1
    assert lookup[f"{a.id}:课程"]["video_count"] == 0
    # An unassigned storage is a creation destination, not a shared library category.
    assert f"{b.id}:旅行" not in lookup
    assert f"{b.id}:" in {c["id"] for c in result["roots"]}
    assert await dc.category_video_ids(session, f"{a.id}:旅行") == [video.id]
    assert await dc.category_video_ids(session, f"{a.id}:") == []


async def test_create_nested_and_duplicate_directory(env):
    session, root, _, a, *_ = env
    body = dc.CreateCategoryRequest(storage_id=a.id, path="旅行", name=" 日本 ")
    created = await dc.create_category(session, body)
    assert created["path"] == "旅行/日本"
    assert (root / "旅行/日本").is_dir()
    with pytest.raises(HTTPException, match="同名"):
        await dc.create_category(session, body)
    assert any(c["path"] == "旅行/日本" for c in (await dc.list_categories(session))["items"])


@pytest.mark.parametrize("bad", ["../outside", "/tmp", "旅行/../../bad", "bad\\name", "bad\x00"])
def test_reject_path_traversal(bad):
    with pytest.raises(ValueError):
        dc.DirectoryRequest(storage_id=1, path=bad)


async def test_move_updates_paths_keeps_video_identity_and_rescan(env):
    session, root, _, a, _, video, source = env
    result = await dc.move_video(
        session, video.id, dc.MoveVideoRequest(source_id=source.id, storage_id=a.id, path="课程")
    )
    assert not (root / "旅行/海边.mp4").exists()
    assert (root / "课程/海边.mp4").read_bytes() == b"video-content" * 100
    assert result["warning"] is None
    assert source.uri == "local:课程/海边.mp4"
    assert video.is_favorite
    assert (await session.get(WatchHistory, video.id)).position_ms == 12345
    stats = await scan_storage(session, a.id, do_probe=False)
    assert stats.updated == 1 and stats.created == 0 and stats.linked == 0
    assert len((await session.scalars(select(VideoSource))).all()) == 1
    assert await dc.category_video_ids(session, f"{a.id}:课程") == [video.id]


async def test_move_across_storages(env):
    session, root, other, a, b, video, source = env
    await dc.move_video(
        session, video.id, dc.MoveVideoRequest(source_id=source.id, storage_id=b.id, path="旅行")
    )
    assert (other / "旅行/海边.mp4").is_file()
    assert not (root / "旅行/海边.mp4").exists()
    assert source.storage_id == b.id and source.uri == "local:旅行/海边.mp4"


def force_cross_device(monkeypatch):
    real_move = dc.move_without_replace

    def fake_move(src, dst):
        if src.name == "海边.mp4":
            raise OSError(errno.EXDEV, "Cross-device link")
        return real_move(src, dst)

    monkeypatch.setattr(dc, "move_without_replace", fake_move)


async def test_cross_filesystem_copy_verifies_and_cleans_source(env, monkeypatch):
    session, root, other, a, b, video, source = env
    force_cross_device(monkeypatch)
    await dc.move_video(
        session, video.id, dc.MoveVideoRequest(source_id=source.id, storage_id=b.id, path="旅行")
    )
    assert (other / "旅行/海边.mp4").read_bytes() == b"video-content" * 100
    assert not (root / "旅行/海边.mp4").exists()
    assert not list(other.rglob(".video-move-*"))


@pytest.mark.parametrize("cross_disk", [False, True])
async def test_database_failure_restores_original(env, monkeypatch, cross_disk):
    session, root, other, a, b, video, source = env
    if cross_disk:
        force_cross_device(monkeypatch)

    async def fail():
        raise RuntimeError("database failure")

    monkeypatch.setattr(session, "commit", fail)
    with pytest.raises(RuntimeError, match="database failure"):
        await dc.move_video(
            session,
            video.id,
            dc.MoveVideoRequest(source_id=source.id, storage_id=b.id, path="旅行"),
        )
    assert (root / "旅行/海边.mp4").read_bytes() == b"video-content" * 100
    assert not (other / "旅行/海边.mp4").exists()


async def test_name_collision_leaves_both_files_intact(env):
    session, root, _, a, _, video, source = env
    target = root / "课程/海边.mp4"
    target.write_bytes(b"do-not-replace")
    with pytest.raises(HTTPException, match="同名"):
        await dc.move_video(
            session,
            video.id,
            dc.MoveVideoRequest(source_id=source.id, storage_id=a.id, path="课程"),
        )
    assert target.read_bytes() == b"do-not-replace"
    assert (root / "旅行/海边.mp4").is_file()


async def test_symbolic_link_and_disabled_storage_rejected(env):
    session, root, other, a, b, video, source = env
    (root / "链接").symlink_to(other, target_is_directory=True)
    with pytest.raises(HTTPException):
        await dc.create_category(
            session, dc.CreateCategoryRequest(storage_id=a.id, path="链接", name="拒绝")
        )
    assert not (other / "拒绝").exists()
    b.enabled = False
    await session.commit()
    with pytest.raises(HTTPException, match="禁用"):
        await dc.move_video(
            session,
            video.id,
            dc.MoveVideoRequest(source_id=source.id, storage_id=b.id, path="旅行"),
        )


async def test_busy_job_and_lock_prevent_file_moves(env):
    session, root, _, a, _, video, source = env
    request = dc.MoveVideoRequest(source_id=source.id, storage_id=a.id, path="课程")
    with dc.library_write_lock(), pytest.raises(HTTPException, match="正在扫描或整理"):
        await dc.move_video(session, video.id, request)
    session.add(Job(kind="convert_format", video_id=video.id, state=JobState.running))
    await session.commit()
    with pytest.raises(HTTPException, match="正在处理任务"):
        await dc.move_video(session, video.id, request)
    assert (root / "旅行/海边.mp4").exists()


async def test_partial_scan_does_not_disable_other_categories(env):
    session, root, _, a, _, video, source = env
    (root / "课程/教学.mp4").write_bytes(b"another-video")
    await scan_storage(session, a.id, sub_path="课程", do_probe=False)
    await session.refresh(source)
    assert source.available


async def test_overlapping_storage_refs_are_reconciled(env):
    session, root, other, a, b, video, source = env
    overlapping = Storage(
        name="重叠根目录", type=StorageType.local, config={"root": str(root / "旅行")}
    )
    session.add(overlapping)
    await session.flush()
    session.add(
        VideoSource(
            video_id=video.id,
            storage_id=overlapping.id,
            source_type=StorageType.local,
            uri="local:海边.mp4",
            local_path=source.local_path,
        )
    )
    await session.commit()
    await dc.move_video(
        session, video.id, dc.MoveVideoRequest(source_id=source.id, storage_id=b.id, path="旅行")
    )
    refs = (await session.scalars(select(VideoSource))).all()
    assert len(refs) == 1 and refs[0].is_primary and refs[0].storage_id == b.id


async def test_http_categories_filter_create_and_move(env, monkeypatch):
    from types import SimpleNamespace

    class Queue:
        async def enqueue_job(self, *args, **kwargs):
            return SimpleNamespace(job_id=kwargs["_job_id"])

    async def get_pool():
        return Queue()

    monkeypatch.setattr(categories, "get_pool", get_pool)
    session, root, _, a, _, video, source = env
    app = FastAPI()
    app.include_router(categories.router)
    app.include_router(videos.router)

    async def session_override():
        yield session

    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        detail = (await client.get(f"/api/videos/{video.id}")).json()
        assert detail["categories"][0]["name"] == "旅行"
        assert detail["sources"][0]["storage_id"] == a.id
        response = await client.get("/api/videos", params={"category": f"{a.id}:旅行"})
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["categories"][0]["name"] == "旅行"
        assert (await client.get("/api/videos", params={"category": f"{a.id}:课程"})).json()[
            "total"
        ] == 0
        created = await client.post("/api/categories", json={"storage_id": a.id, "name": "新分类"})
        assert created.status_code == 201
        response = await client.post(
            f"/api/videos/{video.id}/move",
            json={"source_id": source.id, "storage_id": a.id, "path": "新分类"},
        )
        assert response.status_code == 202, response.text
        assert (root / "旅行/海边.mp4").exists()
        queued = response.json()
        payload = (await session.get(Job, queued["id"])).payload
        await dc.move_video(
            session,
            video.id,
            dc.MoveVideoRequest.model_validate(payload["options"]),
            job_id=queued["id"],
            expected=payload,
        )
        assert (root / "新分类/海边.mp4").exists()
        assert (await client.get("/api/videos", params={"category": f"{a.id}:新分类"})).json()[
            "total"
        ] == 1


def test_provider_does_not_accept_sibling_prefix(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    (tmp_path / "media-secret").mkdir()
    with pytest.raises(ValueError, match="越界"):
        LocalProvider({"root": str(root)}).to_abs("../media-secret")
