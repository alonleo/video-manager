"""Real output and lifecycle tests for resolution/bitrate/frame interpolation."""

import asyncio
import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import enhancement as api
from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Base, Job, JobState, Storage, StorageType, Video, VideoSource
from app.services.enhancement import EnhanceRequest, ai_available, output_path, plan_enhancement
from app.services.probe import _run_ffprobe, parse_probe_json
from app.workers import enhancement as worker


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    for key in ["downloads_dir", "data_dir", "cache_dir", "work_dir"]:
        folder = tmp_path / key
        folder.mkdir()
        monkeypatch.setattr(settings, key, folder)
    monkeypatch.setattr(settings, "playback_min_free_mb", 0)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "_slots", asyncio.Semaphore(1))
    media = tmp_path / "media"
    media.mkdir()
    source = media / "source.mp4"
    subtitles = media / "input.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,400\nEnhancement subtitle\n")
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-i",
            str(subtitles),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:s",
            "-t",
            "1.5",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-c:s",
            "mov_text",
            str(source),
        ],
        check=True,
    )
    raw = await _run_ffprobe(str(source))
    info = parse_probe_json(raw)
    async with factory() as session:
        storage = Storage(name="enhance test", type=StorageType.local, config={"root": str(media)})
        video = Video(
            title="增强验证",
            source_kind="local",
            duration_ms=info.duration_ms,
            width=info.width,
            height=info.height,
            fps=info.fps,
        )
        session.add_all([storage, video])
        await session.flush()
        ref = VideoSource(
            video_id=video.id,
            storage_id=storage.id,
            source_type=StorageType.local,
            uri=str(source),
            local_path=str(source),
            is_primary=True,
            available=True,
        )
        session.add(ref)
        await session.commit()
        ids = video.id, ref.id

    async def dependency():
        async with factory() as session:
            yield session

    class Queue:
        async def enqueue_job(self, *args, **kwargs):
            return SimpleNamespace(job_id="isolated-test")

    async def pool():
        return Queue()

    monkeypatch.setattr(api, "get_pool", pool)
    app.dependency_overrides[get_session] = dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield SimpleNamespace(
                client=client,
                factory=factory,
                source=source,
                raw=raw,
                video_id=ids[0],
                source_id=ids[1],
                monkeypatch=monkeypatch,
            )
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def create(env, **options):
    response = await env.client.post(
        f"/api/videos/{env.video_id}/enhance", json={"encoder": "software", **options}
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def finish(env, job_id):
    result = await worker.run_enhancement({}, job_id)
    async with env.factory() as s:
        job = await s.get(Job, job_id)
        assert job.state == JobState.done, (job.error, result)
        return job


@pytest.mark.asyncio
async def test_real_resolution_fps_audio_subtitles_duration_and_download(env):
    before = hashlib.sha256(env.source.read_bytes()).hexdigest()
    job_id = await create(env, resolution="2x", fps=60, quality="bitrate", bitrate_mbps=3)
    job = await finish(env, job_id)
    result = job.result
    assert (result["width"], result["height"], result["fps"]) == (320, 180, 60)
    assert abs(result["duration_ms"] - 1500) < 150
    assert result["target_bitrate_mbps"] == 3 and result["bitrate_bps"] > 0
    raw = await _run_ffprobe(str(output_path(job_id)))
    assert len([s for s in raw["streams"] if s["codec_type"] == "audio"]) == 1
    assert len([s for s in raw["streams"] if s["codec_type"] == "subtitle"]) == 1
    response = await env.client.get(
        f"/api/jobs/{job_id}/enhancement-file", headers={"Range": "bytes=0-99"}
    )
    assert response.status_code == 206 and len(response.content) == 100
    download = await env.client.get(f"/api/jobs/{job_id}/enhancement-file?download=true")
    assert "attachment" in download.headers["Content-Disposition"]
    assert hashlib.sha256(env.source.read_bytes()).hexdigest() == before
    # A duplicate worker delivery must never erase an already finished result.
    await worker.run_enhancement({}, job_id)
    assert output_path(job_id).exists()


def frame_hashes(path):
    text = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v",
            "-f",
            "framemd5",
            "-",
        ],
        text=True,
    )
    return [
        line.rsplit(",", 1)[-1].strip() for line in text.splitlines() if not line.startswith("#")
    ]


@pytest.mark.asyncio
async def test_motion_interpolation_creates_more_distinct_frames_than_duplication(env):
    motion = await create(env, fps=60, frame_method="motion", keep_subtitles=False)
    duplicate = await create(env, fps=60, frame_method="duplicate", keep_subtitles=False)
    await finish(env, motion)
    await finish(env, duplicate)
    one, two = frame_hashes(output_path(motion)), frame_hashes(output_path(duplicate))
    assert 88 <= len(one) <= 92 and len(one) == len(two)
    # Lossy encoding can vary duplicate-frame pixels slightly, so also compare
    # motion changes by coarse grayscale frame averages in the integration audit.
    assert len(set(one)) > 25
    assert len(set(one)) > len(set(two))


@pytest.mark.asyncio
async def test_preview_both_clips_and_not_importable(env):
    job_id = await create(env, fps=30, preview=True, preview_start=0.3)
    job = await finish(env, job_id)
    assert job.result["preview"] and job.result["preview_start"] == 0.3
    original = parse_probe_json(await _run_ffprobe(str(output_path(job_id, "original.mp4"))))
    assert abs(original.duration_ms - job.result["duration_ms"]) < 150
    assert (
        await env.client.get(f"/api/jobs/{job_id}/enhancement-file?original=true")
    ).status_code == 200
    assert (await env.client.post(f"/api/jobs/{job_id}/enhancement-import")).status_code == 422


@pytest.mark.asyncio
async def test_import_is_idempotent_and_preserves_original(env):
    job_id = await create(env, fps=24)
    await finish(env, job_id)
    first = await env.client.post(f"/api/jobs/{job_id}/enhancement-import")
    second = await env.client.post(f"/api/jobs/{job_id}/enhancement-import")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["video_id"] != env.video_id
    async with env.factory() as s:
        videos = list((await s.scalars(select(Video))).all())
        assert len(videos) == 2
        imported_source = await s.scalar(
            select(VideoSource).where(VideoSource.video_id == first.json()["video_id"])
        )
        assert Path(imported_source.local_path).parent.name == "enhanced-library"
        assert Path(imported_source.local_path).samefile(output_path(job_id))
        assert len(list(Path(imported_source.local_path).parent.glob("*.mp4"))) == 1

    assert env.source.exists()


@pytest.mark.asyncio
async def test_cancel_stops_processing_and_cleans_outputs(env):
    job_id = await create(env, fps=120, resolution="2160")
    task = asyncio.create_task(worker.run_enhancement({}, job_id))
    for _ in range(100):
        async with env.factory() as s:
            state = (await s.get(Job, job_id)).state
        if state == JobState.running:
            break
        await asyncio.sleep(0.01)
    response = await env.client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200
    await asyncio.wait_for(task, 15)
    async with env.factory() as s:
        assert (await s.get(Job, job_id)).state == JobState.canceled
    assert not output_path(job_id).exists() and not output_path(job_id, "partial.mp4").exists()


@pytest.mark.asyncio
async def test_changed_source_fails_without_publishing(env):
    job_id = await create(env, fps=30)
    env.source.touch()
    result = await worker.run_enhancement({}, job_id)
    assert "error" in result
    assert not output_path(job_id).exists()


@pytest.mark.asyncio
async def test_invalid_options_and_queue_failure(env):
    for body in [
        {"fps": 240},
        {"bitrate_mbps": 0},
        {"preview_start": -1},
        {"preview": True, "preview_start": 30},
        {"method": "ai", "resolution": "source"},
    ]:
        response = await env.client.post(f"/api/videos/{env.video_id}/enhance", json=body)
        assert response.status_code == 422, response.text

    async def unavailable():
        raise OSError("isolated queue unavailable")

    env.monkeypatch.setattr(api, "get_pool", unavailable)
    response = await env.client.post(f"/api/videos/{env.video_id}/enhance", json={"fps": 48})
    assert response.status_code == 503
    async with env.factory() as s:
        job = await s.scalar(select(Job).order_by(Job.id.desc()))
        assert job.state == JobState.error and job.finished_at


@pytest.mark.asyncio
async def test_ai_pipeline_runs_real_model_and_cleans_batches(env):
    if not ai_available("anime"):
        pytest.skip("Local Real-ESRGAN model not installed")
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x48:rate=4",
            "-t",
            "0.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(env.source),
        ],
        check=True,
    )
    job_id = await create(env, method="ai", ai_model="anime", resolution="2x")
    job = await finish(env, job_id)
    assert (job.result["width"], job.result["height"], job.result["fps"]) == (128, 96, 4)
    assert not (output_path(job_id).parent / "ai-in").exists()
    assert not (output_path(job_id).parent / "ai-out").exists()


def test_dimensions_rotation_sar_hdr_and_output_limits():
    raw = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 720,
                "height": 576,
                "sample_aspect_ratio": "16:15",
                "avg_frame_rate": "25",
                "side_data_list": [{"rotation": 90}],
            }
        ],
        "format": {"duration": "10"},
    }
    plan = plan_enhancement(EnhanceRequest(resolution="1080"), raw)
    assert (plan["source_width"], plan["source_height"]) == (576, 768)
    assert (plan["width"], plan["height"]) == (1080, 1440)
    raw["streams"][0]["color_transfer"] = "smpte2084"
    with pytest.raises(ValueError, match="HDR"):
        plan_enhancement(EnhanceRequest(), raw)
    with pytest.raises(ValidationError):
        EnhanceRequest(bitrate_mbps=float("nan"))
