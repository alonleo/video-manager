"""Original-byte streaming must never enqueue MP4 preparation."""
import pytest
import pytest_asyncio
from app.models import Storage, StorageType, Video, VideoSource
from test_transport import env as shared_env

@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    async for value in shared_env.__wrapped__(tmp_path, monkeypatch):
        yield value

@pytest.mark.asyncio
async def test_wasm_original_ranges_and_no_conversion(env, tmp_path, monkeypatch):
    manager, factory, client = env
    source = tmp_path / "independent.mkv"
    content = bytes(range(256)) * 8
    source.write_bytes(content)
    async with factory() as session:
        storage = Storage(name="Wasm test", type=StorageType.local, config={"root":str(tmp_path)})
        session.add(storage)
        await session.flush()
        video = Video(title="Independent playback", video_codec="hevc", audio_codec="dts",
                      container="matroska", duration_ms=12000)
        session.add(video)
        await session.flush()
        session.add(VideoSource(video_id=video.id, storage_id=storage.id, source_type="local",
                                uri=str(source), local_path=str(source), is_primary=True, available=True))
        await session.commit()
        video_id = video.id
    async def forbidden(*args, **kwargs):
        pytest.fail("Wasm playback must not prepare an MP4")
    monkeypatch.setattr(manager, "ensure", forbidden)
    result = await client.get(f"/api/videos/{video_id}/playback-info?engine=wasm&compatible=true")
    assert result.status_code == 200
    info = result.json()
    assert info["mode"] == "wasm"
    assert info["source_extension"] == "mkv"
    assert info["preparation_state"] == "none"
    assert info["compatibility_available"] is False
    assert info["stream_url"].startswith("/api/videos/")
    response = await client.get(info["stream_url"], headers={"Range":"bytes=128-255"})
    assert response.status_code == 206
    assert response.content == content[128:256]
    head = await client.head(info["stream_url"])
    assert head.status_code == 200
    assert head.content == b""
    assert int(head.headers["content-length"]) == len(content)
    missing_range = await client.get(info["stream_url"], headers={"Range":"bytes=9000-"})
    assert missing_range.status_code == 416
    assert not manager.jobs
    source.unlink()
    assert (await client.get(info["stream_url"])).status_code == 410

@pytest.mark.asyncio
async def test_wasm_unknown_video_and_invalid_engine(env):
    _, _, client = env
    assert (await client.get("/api/videos/999999/stream?engine=wasm")).status_code == 404
    assert (await client.get("/api/videos/999999/playback-info?engine=arbitrary")).status_code == 422
