"""Real media matrix and isolated API tests for every advertised extension."""

import asyncio
import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from test_transport import env as _shared_env
from test_transport import frame_hashes

from app.config import settings
from app.models import Storage, StorageType, Video, VideoSource
from app.services.library import scan_storage
from app.services.playback import decide
from app.services.probe import parse_probe_json, probe


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    async for value in _shared_env.__wrapped__(tmp_path, monkeypatch):
        yield value


# Name, encoder, audio encoder, actual muxer, expected playback path.
CASES = [
    ("sample.mp4", "libx264", "aac", "mp4", "direct"),
    ("sample.mkv", "libx264", "ac3", "matroska", "remux"),
    ("sample.avi", "mpeg4", "libmp3lame", "avi", "transcode"),
    ("sample.mov", "prores_ks", "pcm_s16le", "mov", "transcode"),
    ("sample.wmv", "wmv2", "wmav2", "asf", "transcode"),
    ("sample.flv", "flv", "libmp3lame", "flv", "transcode"),
    ("sample.webm", "libvpx", "libvorbis", "webm", "direct"),
    ("sample.m4v", "libx264", "aac", "mp4", "direct"),
    ("sample.mpg", "mpeg1video", "mp2", "mpeg", "transcode"),
    ("sample.mpeg", "mpeg2video", "mp2", "mpeg", "transcode"),
    ("sample.ts", "libx264", "aac", "mpegts", "remux"),
    ("sample.m2ts", "mpeg2video", "ac3", "mpegts", "transcode"),
    ("sample.mts", "libx264", None, "mpegts", "remux"),
    ("sample.rmvb", "rv20", "ac3", "rm", "transcode"),
    ("sample.3gp", "h263", "aac", "3gp", "transcode"),
    ("sample.vob", "mpeg2video", "ac3", "vob", "transcode"),
    ("sample.ogv", "libtheora", "libvorbis", "ogg", "transcode"),
    ("hevc.mp4", "libx265", "aac", "mp4", "transcode"),
    ("high10.mp4", "libx264", "aac", "mp4", "transcode"),
    ("h264-mp3.avi", "libx264", "libmp3lame", "avi", "remux"),
    ("h264-aac.mov", "libx264", "aac", "mov", "remux"),
    ("vp9-opus.webm", "libvpx-vp9", "libopus", "webm", "direct"),
]


def build_format_samples(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    for name, video, audio, muxer, _ in CASES:
        target = root / name
        if target.is_file() and target.stat().st_size:
            continue
        size = "176x144" if video == "h263" else "320x180"
        cmd = [
            settings.ffmpeg_bin,
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate=25",
        ]
        frequency = 440 + next(i for i, c in enumerate(CASES) if c[0] == name) * 17
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000"]
        pix = (
            "yuv422p10le"
            if video == "prores_ks"
            else "yuv420p10le"
            if name == "high10.mp4"
            else "yuv420p"
        )
        cmd += ["-t", "12", "-c:v", video, "-pix_fmt", pix, "-threads", "2", "-g", "25"]
        if video.startswith("libx"):
            cmd += ["-preset", "ultrafast"]
        if video == "libx265":
            cmd += ["-x265-params", "pools=1:frame-threads=1:log-level=error"]
        if video.startswith("libvpx"):
            cmd += ["-deadline", "realtime", "-cpu-used", "8"]
        cmd += ["-c:a", audio, "-ac", "2"] if audio else ["-an"]
        if muxer in {"mp4", "mov"}:
            cmd += ["-movflags", "+faststart"]
        cmd += ["-f", muxer, str(target)]
        result = subprocess.run(cmd, capture_output=True, timeout=90)
        if result.returncode:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"Fixture {name}: {result.stderr.decode()}")
    return root


@pytest.fixture(scope="session")
def format_samples(tmp_path_factory):
    return build_format_samples(tmp_path_factory.mktemp("all-formats"))


async def insert_source(factory, path):
    p = await probe(str(path))
    async with factory() as s:
        storage = Storage(
            name="isolated format sample", type=StorageType.local, config={"root": str(path.parent)}
        )
        s.add(storage)
        await s.flush()
        video = Video(
            title=path.name,
            duration_ms=p.duration_ms,
            video_codec=p.video_codec,
            video_profile=p.video_profile,
            pix_fmt=p.pix_fmt,
            audio_codec=p.audio_codec,
            container=p.container,
            size_bytes=p.size_bytes,
            width=p.width,
            height=p.height,
        )
        s.add(video)
        await s.flush()
        source = VideoSource(
            video_id=video.id,
            storage_id=storage.id,
            source_type="local",
            uri=str(path),
            local_path=str(path),
            is_primary=True,
            available=True,
        )
        s.add(source)
        await s.commit()
        return video.id


async def wait_info(client, video_id, suffix=""):
    async with asyncio.timeout(60):
        while True:
            r = await client.get(f"/api/videos/{video_id}/playback-info{suffix}")
            assert r.status_code == 200, r.text
            info = r.json()
            assert info["preparation_state"] != "error", info
            if info["stream_url"]:
                return info
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,encoder,audio,muxer,mode", CASES, ids=[c[0] for c in CASES])
async def test_real_format_api(env, format_samples, name, encoder, audio, muxer, mode):
    manager, factory, client = env
    source = format_samples / name
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    video_id = await insert_source(factory, source)
    info = await wait_info(client, video_id)
    assert info["mode"] == mode
    assert info["compatibility_available"] is True
    if mode != "direct":
        path = next(manager.directory.glob("*.mp4"))
        p = await probe(str(path))
        assert p.video_codec == "h264" and p.pix_fmt == "yuv420p"
        assert p.audio_codec == ("aac" if audio else None)
        assert abs(p.duration_ms - info["duration_ms"]) < 1000
        assert path.read_bytes().find(b"moov") < path.read_bytes().find(b"mdat")
        if mode == "remux":
            assert frame_hashes(source) == frame_hashes(path)
    ranged = await client.get(info["stream_url"], headers={"Range": "bytes=0-127"})
    assert ranged.status_code == 206 and len(ranged.content) == 128
    assert ranged.headers["content-type"].startswith("video/")
    r = await client.put(
        f"/api/videos/{video_id}/progress", json={"position_ms": 5000, "finished": False}
    )
    assert r.status_code == 200
    assert (await wait_info(client, video_id))["start_position_ms"] == 5000
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


@pytest.mark.asyncio
async def test_force_compatibility_preserves_direct_and_resume(env, format_samples):
    manager, factory, client = env
    video_id = await insert_source(factory, format_samples / "sample.webm")
    direct = await wait_info(client, video_id)
    await client.put(
        f"/api/videos/{video_id}/progress", json={"position_ms": 6000, "finished": False}
    )
    initial = await client.post(f"/api/videos/{video_id}/playback-retry?compatible=true")
    assert initial.status_code == 200 and initial.json()["mode"] == "transcode"
    converted = await wait_info(client, video_id, "?compatible=true")
    assert converted["compatibility_mode"] is True and converted["start_position_ms"] == 6000
    assert converted["stream_url"] != direct["stream_url"]
    r = await client.get(converted["stream_url"], headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"
    assert (await wait_info(client, video_id))["mode"] == "direct"


@pytest.mark.asyncio
async def test_scan_detects_all_17_extensions(env, format_samples):
    _, factory, _ = env
    async with factory() as s:
        storage = Storage(
            name="17 format scanner", type=StorageType.local, config={"root": str(format_samples)}
        )
        s.add(storage)
        await s.commit()
        stats = await scan_storage(s, storage.id)
        assert stats.scanned == len(CASES) and stats.probed == len(CASES)
        sources = (await s.execute(select(VideoSource))).scalars().all()
        assert {Path(v.local_path).suffix for v in sources} == settings.video_ext_set


def test_probe_ignores_attached_cover():
    p = parse_probe_json(
        {
            "streams": [
                {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                {"codec_type": "video", "codec_name": "h264", "disposition": {"attached_pic": 0}},
            ]
        }
    )
    assert p.video_codec == "h264"


@pytest.mark.parametrize(
    "container,codec,pix,ext",
    [
        ("matroska", "h264", "yuv420p", ".mkv"),
        ("mov", "h264", "yuv422p", ".mp4"),
        ("mov", "hevc", "yuv420p", ".mp4"),
        ("matroska", "vp9", "yuv420p10le", ".webm"),
        ("mov", "mpeg4", "yuv420p", ".3gp"),
        ("m4v", "mpeg4", "yuv420p", ".m4v"),
    ],
)
def test_incompatible_variants_never_direct(container, codec, pix, ext):
    assert decide(
        container=container,
        video_codec=codec,
        video_profile=None,
        audio_codec="aac",
        pix_fmt=pix,
        extension=ext,
    ).needs_ffmpeg


@pytest.mark.asyncio
async def test_real_rv40_if_available(env):
    path = os.environ.get("REAL_RMVB_SAMPLE")
    if not path:
        pytest.skip("Set REAL_RMVB_SAMPLE to an authentic RV40/Cook sample")
    _, factory, client = env
    source = Path(path)
    p = await probe(str(source))
    assert p.video_codec == "rv40"
    video_id = await insert_source(factory, source)
    info = await wait_info(client, video_id)
    assert info["mode"] == "transcode" and info["preparation_state"] == "ready"
    assert (
        await client.get(info["stream_url"], headers={"Range": "bytes=0-127"})
    ).status_code == 206
