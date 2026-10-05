"""Names, category destinations and destructive tag actions stay in their library."""

import httpx
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Base, Storage, StorageType, Video, VideoSource


@pytest_asyncio.fixture
async def scoped_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/scope.db")

    @event.listens_for(engine.sync_engine, "connect")
    def fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ids = {}
    async with sessions() as session:
        for kind in ("video", "photo", "empty"):
            root = tmp_path / kind
            root.mkdir()
            storage = Storage(name=kind, type=StorageType.local, config={"root": str(root)})
            session.add(storage)
            await session.flush()
            ids[kind + "_storage"] = storage.id
            if kind == "empty":
                continue
            child = root / (kind + "-only")
            child.mkdir()
            path = child / ("sample.jpg" if kind == "photo" else "sample.mp4")
            path.write_bytes(b"source kept unchanged")
            media = Video(title=kind, media_type=kind, is_favorite=True)
            session.add(media)
            await session.flush()
            source = VideoSource(
                video_id=media.id,
                storage_id=storage.id,
                source_type="local",
                uri=f"local:{child.name}/{path.name}",
                local_path=str(path),
                is_primary=True,
                available=True,
            )
            session.add(source)
            await session.flush()
            ids[kind] = media.id
            ids[kind + "_source"] = source.id
        await session.commit()

    async def dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, ids, tmp_path
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def test_same_name_tags_have_independent_identity_and_deletion(scoped_env):
    client, ids, _ = scoped_env
    tags = {}
    for kind in ("video", "photo"):
        response = await client.post(f"/api/videos/{ids[kind]}/tags", json={"name": "旅行"})
        assert response.status_code == 200
        tags[kind] = response.json()[0]
        assert tags[kind]["media_type"] == kind
    assert tags["video"]["id"] != tags["photo"]["id"]
    for kind in ("video", "photo"):
        visible = (await client.get(f"/api/tags?media_type={kind}")).json()
        assert len(visible) == 1 and visible[0]["id"] == tags[kind]["id"]
        assert visible[0]["video_count"] == 1
        items = (await client.get(f"/api/videos?media_type={kind}&tag=旅行")).json()["items"]
        assert [v["id"] for v in items] == [ids[kind]]
    # A stale or wrong-library delete URL must never remove another library's tag.
    assert (
        await client.delete(f"/api/tags/{tags['photo']['id']}?media_type=video&expected_count=1")
    ).status_code == 404
    assert (
        await client.delete(f"/api/tags/{tags['video']['id']}?media_type=video&expected_count=1")
    ).status_code == 204
    assert (await client.get("/api/tags?media_type=video")).json() == []
    assert len((await client.get("/api/tags?media_type=photo")).json()) == 1
    photo = (await client.get(f"/api/videos/{ids['photo']}")).json()
    assert photo["tags"][0]["name"] == "旅行" and photo["is_favorite"]


async def test_categories_and_empty_new_categories_stay_scoped(scoped_env):
    client, ids, root = scoped_env
    for kind in ("video", "photo"):
        data = (await client.get(f"/api/categories?media_type={kind}")).json()
        assert {c["storage_id"] for c in data["items"]} == {ids[kind + "_storage"]}
        assert next(c for c in data["items"] if c["path"])["video_count"] == 1
        assert ids["empty_storage"] in {c["storage_id"] for c in data["roots"]}
    response = await client.post(
        "/api/categories",
        json={
            "storage_id": ids["empty_storage"],
            "path": "",
            "name": "相册",
            "media_type": "photo",
        },
    )
    assert response.status_code == 201
    category_id = response.json()["id"]
    assert (root / "empty" / "相册").is_dir()
    # Empty categories remain visible after a fresh request, without borrowing the other catalog.
    for _ in range(2):
        photo_items = (await client.get("/api/categories?media_type=photo")).json()["items"]
        assert category_id in {c["id"] for c in photo_items}
        video_items = (await client.get("/api/categories?media_type=video")).json()["items"]
        assert category_id not in {c["id"] for c in video_items}
    wrong_parent = await client.post(
        "/api/categories",
        json={
            "storage_id": ids["photo_storage"],
            "path": "photo-only",
            "name": "wrong",
            "media_type": "video",
        },
    )
    assert wrong_parent.status_code == 409
    assert not (root / "photo" / "photo-only" / "wrong").exists()


async def test_wrong_library_move_destination_is_rejected_before_queue(scoped_env):
    client, ids, _ = scoped_env
    response = await client.post(
        f"/api/videos/{ids['video']}/move",
        json={
            "source_id": ids["video_source"],
            "storage_id": ids["photo_storage"],
            "path": "photo-only",
        },
    )
    assert response.status_code == 409
    assert (await client.get("/api/jobs")).json()["total"] == 0
    assert (await client.get("/api/categories?media_type=invalid")).status_code == 422
    assert (await client.get("/api/tags?media_type=invalid")).status_code == 422
