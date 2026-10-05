"""Real photo codecs, independent catalogs, safe preview and rescan semantics."""

import hashlib
import shutil

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Base, Storage, StorageType
from app.services.library import scan_storage
from app.services.photos import inspect_photo


@pytest_asyncio.fixture
async def photo_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    root = tmp_path / "media"
    root.mkdir()
    Image.new("RGB", (120, 80), "red").save(root / "holiday.JPG")
    Image.new("RGBA", (50, 100), (20, 100, 80, 150)).save(root / "portrait.png")
    (root / "clip.mp4").write_bytes(b"existing video" * 100)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/photos.db")

    @event.listens_for(engine.sync_engine, "connect")
    def fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        storage = Storage(name="Mixed media", type=StorageType.local, config={"root": str(root)})
        session.add(storage)
        await session.commit()
        sid = storage.id
        await scan_storage(session, sid, do_probe=False)

    async def dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, sessions, sid, root
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_catalogs_are_separate_and_categories_are_scoped(photo_env):
    client, _, sid, _ = photo_env
    videos = (await client.get("/api/videos")).json()
    photos = (await client.get("/api/videos?media_type=photo&page_size=1")).json()
    assert videos["total"] == 1 and videos["items"][0]["media_type"] == "video"
    assert photos["total"] == 2 and len(photos["items"]) == 1
    photo = photos["items"][0]
    assert photo["media_type"] == "photo" and photo["duration_ms"] is None
    assert photo["width"] and photo["height"] and photo["cover_path"]
    assert (await client.get(f"/api/videos/{photo['id']}/cover")).status_code == 200
    for kind, count in [("video", 1), ("photo", 2)]:
        items = (await client.get(f"/api/categories?media_type={kind}")).json()["items"]
        assert next(c for c in items if c["id"] == f"{sid}:")["video_count"] == count
    assert (await client.get("/api/videos?media_type=invalid")).status_code == 422


@pytest.mark.asyncio
async def test_favorites_tags_preview_download_and_removal(photo_env):
    client, sessions, sid, root = photo_env
    photo = (await client.get("/api/videos?media_type=photo&q=holiday")).json()["items"][0]
    pid = photo["id"]
    original = (root / "holiday.JPG").read_bytes()
    assert (await client.patch(f"/api/videos/{pid}", json={"is_favorite": True})).status_code == 200
    assert (await client.post(f"/api/videos/{pid}/tags", json={"name": "trip"})).status_code == 200
    assert (await client.get("/api/videos?favorite=true")).json()["total"] == 0
    assert (await client.get("/api/videos?media_type=photo&favorite=true&tag=trip")).json()[
        "total"
    ] == 1
    image = await client.get(f"/api/photos/{pid}/image")
    assert image.status_code == 200 and image.content == original
    assert image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "private, no-cache"
    download = await client.get(f"/api/photos/{pid}/image?download=true")
    assert download.content == original and "attachment" in download.headers["content-disposition"]
    assert (await client.delete(f"/api/videos/{pid}")).status_code == 204
    assert (root / "holiday.JPG").read_bytes() == original
    assert (await client.get(f"/api/photos/{pid}/image")).status_code == 404
    assert (await client.get("/api/videos?media_type=photo&q=holiday")).json()["total"] == 0
    async with sessions() as session:
        await scan_storage(session, sid, do_probe=False)
    restored = (await client.get("/api/videos?media_type=photo&q=holiday")).json()["items"][0]
    assert not restored["is_favorite"] and not restored["tags"]
    assert restored["cover_path"] != photo["cover_path"]


@pytest.mark.asyncio
async def test_unchanged_rescan_duplicate_and_replaced_photo(photo_env):
    client, sessions, sid, root = photo_env
    photo = (await client.get("/api/videos?media_type=photo&q=holiday")).json()["items"][0]
    async with sessions() as session:
        stats = await scan_storage(session, sid, do_probe=False)
        assert stats.probed == 0 and stats.scanned_photos == 2 and stats.scanned_videos == 1
    shutil.copyfile(root / "holiday.JPG", root / "copy.jpg")
    async with sessions() as session:
        stats = await scan_storage(session, sid, do_probe=False)
        assert stats.linked == 1 and stats.created == 0
    assert (await client.get("/api/videos?media_type=photo")).json()["total"] == 2
    async with sessions() as session:
        stats = await scan_storage(session, sid, do_probe=False)
        assert stats.probed == 0
    (root / "copy.jpg").unlink()
    Image.new("RGB", (120, 80), "blue").save(root / "holiday.JPG")
    async with sessions() as session:
        await scan_storage(session, sid, do_probe=False)
    refreshed = (await client.get(f"/api/videos/{photo['id']}")).json()
    assert refreshed["cover_path"] != photo["cover_path"]
    assert refreshed["width"] == 120


@pytest.mark.asyncio
async def test_corrupt_and_missing_photos_do_not_break_other_entries(photo_env):
    client, sessions, sid, root = photo_env
    (root / "broken.png").write_bytes(b"not an image")
    async with sessions() as session:
        stats = await scan_storage(session, sid, do_probe=False)
        assert stats.probe_failed == 1
    broken = (await client.get("/api/videos?media_type=photo&q=broken")).json()["items"][0]
    assert broken["status"] == "error" and broken["cover_path"] is None
    assert (await client.get(f"/api/photos/{broken['id']}/image")).status_code == 422
    photo = (await client.get("/api/videos?media_type=photo&q=holiday")).json()["items"][0]
    (root / "holiday.JPG").unlink()
    assert (await client.get(f"/api/photos/{photo['id']}/image")).status_code == 409
    assert (await client.get("/api/videos")).json()["total"] == 1


@pytest.mark.asyncio
async def test_video_operations_reject_photos_before_enqueuing(photo_env):
    client, _, _, _ = photo_env
    pid = (await client.get("/api/videos?media_type=photo")).json()["items"][0]["id"]
    for path, body in [
        ("convert", {"format": "mp4"}),
        ("subtitles/generate-saved", {}),
        ("enhance", {}),
        ("progress", {"position_ms": 0}),
    ]:
        method = client.put if path == "progress" else client.post
        response = await method(f"/api/videos/{pid}/{path}", json=body)
        assert response.status_code == 422, response.text
    assert (await client.get(f"/api/videos/{pid}/playback-info")).status_code == 422
    assert (await client.get(f"/api/videos/{pid}/conversion-options")).status_code == 422
    assert (await client.get("/api/jobs")).json()["total"] == 0


@pytest.mark.parametrize(
    "format_name,suffix",
    [
        ("JPEG", ".jpeg"),
        ("PNG", ".png"),
        ("WEBP", ".webp"),
        ("GIF", ".gif"),
        ("BMP", ".bmp"),
        ("TIFF", ".tiff"),
        ("AVIF", ".avif"),
        ("HEIF", ".heic"),
    ],
)
def test_real_codecs_preserve_originals(tmp_path, monkeypatch, format_name, suffix):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    original = tmp_path / ("image" + suffix)
    Image.new("RGB", (120, 80), (160, 30, 80)).save(original, format=format_name)
    before = hashlib.sha256(original.read_bytes()).digest()
    info = inspect_photo(original)
    assert info["width"] == 120 and info["height"] == 80
    assert Image.open(settings.data_dir / info["preview_path"]).format == "JPEG"
    assert hashlib.sha256(original.read_bytes()).digest() == before


def test_exif_orientation_and_animation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    image = Image.new("RGB", (120, 80), "red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(tmp_path / "rotated.jpg", exif=exif)
    info = inspect_photo(tmp_path / "rotated.jpg")
    assert (info["width"], info["height"]) == (80, 120)
    image.save(
        tmp_path / "animated.gif",
        save_all=True,
        append_images=[Image.new("RGB", (120, 80), "blue")],
        duration=100,
        loop=0,
    )
    assert inspect_photo(tmp_path / "animated.gif")["frames"] == 2
