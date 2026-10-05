"""Real ffmpeg and isolated SQLite/ASGI tests; never uses the user's media/database."""

import asyncio
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Base, Storage, StorageType, Video
from app.services import transport
from app.services.library import scan_storage
from app.services.playback import decide
from app.services.probe import probe


def build_samples(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    cases = [
        ("h264-aac.ts", "libx264", "aac"),
        ("h264-mp2.TS", "libx264", "mp2"),
        ("mpeg2-ac3.m2ts", "mpeg2video", "ac3"),
        ("hevc-aac.ts", "libx265", "aac"),
        ("silent.mts", "libx264", None),
    ]
    for name, video, audio in cases:
        cmd = [
            settings.ffmpeg_bin,
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24",
        ]
        if audio:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
        cmd += ["-t", "12", "-c:v", video, "-pix_fmt", "yuv420p", "-g", "24"]
        if video.startswith("libx"):
            cmd += ["-preset", "ultrafast"]
        if video == "libx265":
            cmd += ["-x265-params", "pools=1:frame-threads=1:log-level=error"]
        cmd += ["-c:a", audio] if audio else ["-an"]
        cmd += ["-f", "mpegts", str(root / name)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return root


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    return build_samples(tmp_path_factory.mktemp("ts-samples"))


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "cache_max_gb", 0.5)
    monkeypatch.setattr(settings, "playback_min_free_mb", 0)
    manager = transport.PlaybackCache(tmp_path / "cache")
    monkeypatch.setattr(transport, "cache", manager)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def dependency():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = dependency
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield manager, session_factory, client
    await manager.close()
    app.dependency_overrides.clear()
    await engine.dispose()


async def make_spec(source):
    p = await probe(str(source))
    decision = decide(
        container=p.container,
        video_codec=p.video_codec,
        video_profile=p.video_profile,
        audio_codec=p.audio_codec,
        pix_fmt=p.pix_fmt,
    )
    return transport.MediaSpec.from_path(source, decision.mode.value, p.audio_codec, p.duration_ms)


async def ready(manager, spec):
    state = await manager.ensure(spec)
    if state.task:
        await asyncio.wait_for(asyncio.shield(state.task), 40)
    assert state.state == "ready", state.error
    return state


def frame_hashes(path):
    r = subprocess.run(
        [
            settings.ffmpeg_bin,
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-an",
            "-f",
            "framemd5",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    return [
        line.split(",")[-1].strip()
        for line in r.stdout.decode().splitlines()
        if line and not line.startswith("#")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,mode,audio",
    [
        ("h264-aac.ts", "remux", "aac"),
        ("h264-mp2.TS", "remux", "aac"),
        ("mpeg2-ac3.m2ts", "transcode", "aac"),
        ("hevc-aac.ts", "transcode", "aac"),
        ("silent.mts", "remux", None),
    ],
)
async def test_real_transport_playback(env, samples, name, mode, audio):
    manager, _, _ = env
    source = samples / name
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    spec = await make_spec(source)
    assert spec.mode == mode
    state = await ready(manager, spec)
    output = await probe(str(state.path))
    assert output.video_codec == "h264"
    assert output.audio_codec == audio
    assert abs(output.duration_ms - spec.duration_ms) < 1000
    assert source.read_bytes()[0] == 0x47 or source.read_bytes()[4] == 0x47  # TS / M2TS
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    if mode == "remux":
        assert frame_hashes(source) == frame_hashes(state.path)
    assert state.path.read_bytes().find(b"moov") < state.path.read_bytes().find(b"mdat")


@pytest.mark.asyncio
async def test_cache_reuse_and_concurrent_dedup(env, samples):
    manager, _, _ = env
    spec = await make_spec(samples / "h264-aac.ts")
    one, two = await asyncio.gather(manager.ensure(spec), manager.ensure(spec))
    assert one.task is two.task
    await ready(manager, spec)
    reused = await manager.ensure(spec)
    assert reused.state == "ready" and reused.task is None
    assert len(list(manager.directory.glob("*.mp4"))) == 1


@pytest.mark.asyncio
async def test_corrupt_file_requires_explicit_retry(env, tmp_path):
    manager, _, _ = env
    source = tmp_path / "bad.ts"
    source.write_bytes(b"not a transport stream")
    spec = transport.MediaSpec.from_path(source, "transcode", None, None)
    state = await manager.ensure(spec)
    await state.task
    assert state.state == "error" and state.error
    again = await manager.ensure(spec)
    assert again.task is state.task
    retried = await manager.ensure(spec, retry=True)
    assert retried.task is not state.task
    await retried.task
    assert not list(manager.directory.glob("*.mp4"))


@pytest.mark.asyncio
async def test_cache_capacity_guard_preserves_original(env, samples, monkeypatch):
    manager, _, _ = env
    monkeypatch.setattr(settings, "cache_max_gb", 0)
    spec = await make_spec(samples / "h264-aac.ts")
    state = await manager.ensure(spec)
    await state.task
    assert state.state == "error" and "空间不足" in state.error
    assert spec.source.is_file()
    assert not list(manager.directory.glob("*.mp4"))


@pytest.mark.asyncio
async def test_size_limit_rejects_truncated_output(env, samples, monkeypatch):
    manager, _, _ = env
    spec = await make_spec(samples / "h264-aac.ts")
    monkeypatch.setattr(manager, "_make_room", lambda _: 20000)
    state = await manager.ensure(spec)
    await state.task
    assert state.state == "error"
    assert not list(manager.directory.glob("*.mp4"))


@pytest.mark.asyncio
async def test_source_changes_invalidate_cache(env, samples, tmp_path):
    manager, _, _ = env
    source = tmp_path / "changing.ts"
    source.write_bytes((samples / "silent.mts").read_bytes())
    spec = await make_spec(source)
    old = await ready(manager, spec)
    st = source.stat()
    os.utime(source, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    new_spec = await make_spec(source)
    assert spec.key != new_spec.key
    new = await ready(manager, new_spec)
    assert old.path != new.path


@pytest.mark.asyncio
async def test_timeout_and_shutdown_reap_ffmpeg(env, samples, monkeypatch, tmp_path):
    manager, _, _ = env
    pid_file = tmp_path / "child.pid"

    def command(spec, output, limit):
        return [
            sys.executable,
            "-c",
            "import os,time,pathlib,sys;"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)",
            str(pid_file),
        ]

    monkeypatch.setattr(transport, "ffmpeg_command", command)
    monkeypatch.setattr(settings, "playback_prepare_timeout_sec", 0.25)
    spec = await make_spec(samples / "h264-aac.ts")
    job = await manager.ensure(spec)
    await job.task
    assert job.state == "error" and "超时" in job.error
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    monkeypatch.setattr(settings, "playback_prepare_timeout_sec", 60)
    job = await manager.ensure(spec, retry=True)
    await asyncio.sleep(0.15)
    await manager.close()
    assert job.task.done()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert not list(manager.directory.glob("*.part.mp4"))


@pytest.mark.asyncio
async def test_lru_does_not_evict_active_transfer(env, samples, monkeypatch):
    manager, _, _ = env
    monkeypatch.setattr(settings, "cache_max_gb", 0.002)
    manager.directory.mkdir(parents=True)
    old = manager.directory / "old.mp4"
    old.write_bytes(b"0" * 1_500_000)
    os.utime(old, (1, 1))
    manager.hold("old")
    spec = await make_spec(samples / "h264-aac.ts")
    with pytest.raises(RuntimeError, match="空间不足"):
        manager._make_room(spec)
    assert old.exists()
    manager.release("old")
    manager._make_room(spec)
    assert not old.exists()


@pytest.mark.asyncio
async def test_scan_to_api_range_and_resume(env, samples):
    manager, factory, client = env
    async with factory() as s:
        storage = Storage(
            name="test TS library", type=StorageType.local, config={"root": str(samples)}
        )
        s.add(storage)
        await s.commit()
        stats = await scan_storage(s, storage.id)
        assert stats.scanned == 5 and stats.probed == 5
        videos = (await s.execute(select(Video))).scalars().all()
        assert all(v.container == "mpegts" and v.duration_ms for v in videos)
        video = next(v for v in videos if v.title == "h264-aac")
        video_id = video.id
    response = await client.get(f"/api/videos/{video_id}/stream")
    assert response.status_code == 425 and response.headers["retry-after"] == "2"
    while True:
        info = (await client.get(f"/api/videos/{video_id}/playback-info")).json()
        assert info["preparation_state"] != "error", info
        if info["preparation_state"] == "ready":
            break
        await asyncio.sleep(0.05)
    ranged = await client.get(info["stream_url"], headers={"Range": "bytes=0-127"})
    assert ranged.status_code == 206 and len(ranged.content) == 128
    assert ranged.headers["content-type"] == "video/mp4"
    assert ranged.headers["content-range"].startswith("bytes 0-127/")
    assert not manager._leases
    stable = await client.get(
        info["stream_url"], headers={"Range": "bytes=128-255", "If-Range": ranged.headers["etag"]}
    )
    assert stable.status_code == 206 and stable.headers["etag"] == ranged.headers["etag"]
    invalid = await client.get(info["stream_url"], headers={"Range": "bytes=9999999999-"})
    assert invalid.status_code == 416
    stale = await client.get(f"/api/videos/{video_id}/stream?version=old")
    assert stale.status_code == 409
    await client.put(
        f"/api/videos/{video_id}/progress", json={"position_ms": 4000, "finished": False}
    )
    resumed = (await client.get(f"/api/videos/{video_id}/playback-info")).json()
    assert resumed["start_position_ms"] == 4000
    assert resumed["stream_url"] == info["stream_url"]
    assert (await client.get("/api/videos/9999/stream")).status_code == 404


@pytest.mark.parametrize(
    "codec,profile,pix,mode",
    [
        ("h264", "High", "yuv420p", "remux"),
        ("h264", "High 10", "yuv420p10le", "transcode"),
        ("h264", "High 4:2:2", "yuv422p", "transcode"),
        ("mpeg2video", None, "yuv420p", "transcode"),
        ("hevc", None, "yuv420p10le", "transcode"),
    ],
)
def test_ts_codec_decision(codec, profile, pix, mode):
    assert (
        decide(
            container="mpegts",
            video_codec=codec,
            video_profile=profile,
            audio_codec="ac3",
            pix_fmt=pix,
        ).mode.value
        == mode
    )


@pytest.mark.asyncio
async def test_evicted_cache_is_prepared_again(env, samples):
    manager, _, _ = env
    spec = await make_spec(samples / "silent.mts")
    first = await ready(manager, spec)
    first.path.unlink()
    second = await manager.ensure(spec)
    assert second.state == "queued" and second.task is not first.task
    await second.task
    assert second.state == "ready" and second.path.is_file()
