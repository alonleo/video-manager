"""Integration tests operate only on generated media and an isolated SQLite database."""

import asyncio
import hashlib
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import operations as api
from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Base, Job, JobState, Storage, StorageType, Video, VideoSource
from app.services.operations import RenameRequest, rename_video
from app.services.probe import _run_ffprobe, parse_probe_json
from app.workers import conversion


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    directory = tmp_path_factory.mktemp("basic-actions-media")
    path = directory / "original.mp4"
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    return path


@pytest_asyncio.fixture
async def env(tmp_path, sample, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    path = tmp_path / "片段 original.mp4"
    shutil.copyfile(sample, path)
    async with sessions() as session:
        storage = Storage(name="isolated", type=StorageType.local, config={"root": str(tmp_path)})
        video = Video(title="Original", source_kind="local", duration_ms=2000)
        session.add_all([storage, video])
        await session.flush()
        source = VideoSource(
            video_id=video.id,
            storage_id=storage.id,
            source_type=StorageType.local,
            local_path=str(path),
            uri=f"local:{path}",
            is_primary=True,
            available=True,
        )
        session.add(source)
        await session.commit()
        vid, sid = video.id, source.id

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    monkeypatch.setattr(conversion, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "downloads_dir", tmp_path / "outputs")
    monkeypatch.setattr(conversion, "_slots", asyncio.Semaphore(2))
    pool = SimpleNamespace(enqueue_job=AsyncMock(return_value=SimpleNamespace(job_id="test-queue")))
    monkeypatch.setattr(api, "get_pool", AsyncMock(return_value=pool))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(
            client=client, sessions=sessions, path=path, vid=vid, sid=sid, pool=pool
        )
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_rename_file_and_index(env):
    before = hashlib.sha256(env.path.read_bytes()).hexdigest()
    response = await env.client.post(f"/api/videos/{env.vid}/rename", json={"name": "新名称"})
    assert response.status_code == 200, response.text
    renamed = env.path.with_name("新名称.mp4")
    assert not env.path.exists()
    assert hashlib.sha256(renamed.read_bytes()).hexdigest() == before
    async with env.sessions() as session:
        assert (await session.get(Video, env.vid)).title == "新名称"
        source = await session.get(VideoSource, env.sid)
        assert source.local_path == str(renamed)
        assert source.uri == f"local:{renamed}"


async def test_title_only_does_not_move_file(env):
    response = await env.client.post(
        f"/api/videos/{env.vid}/rename", json={"name": "显示标题", "rename_file": False}
    )
    assert response.status_code == 200
    assert env.path.is_file()
    assert not response.json()["renamed_file"]


async def test_collision_does_not_overwrite(env):
    other = env.path.with_name("已存在.mp4")
    other.write_bytes(b"keep-me")
    response = await env.client.post(f"/api/videos/{env.vid}/rename", json={"name": "已存在"})
    assert response.status_code == 409
    assert other.read_bytes() == b"keep-me"
    assert env.path.is_file()


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", ".", " ", "bad\u0000name"])
async def test_invalid_filename_rejected(env, name):
    response = await env.client.post(f"/api/videos/{env.vid}/rename", json={"name": name})
    assert response.status_code == 422
    assert env.path.is_file()


async def test_database_failure_restores_original_filename(env, monkeypatch):
    async with env.sessions() as session:
        video = await session.get(Video, env.vid)
        monkeypatch.setattr(
            session, "commit", AsyncMock(side_effect=RuntimeError("simulated failure"))
        )
        with pytest.raises(RuntimeError, match="simulated"):
            await rename_video(session, video, RenameRequest(name="rollback"))
    assert env.path.is_file()
    assert not env.path.with_name("rollback.mp4").exists()


async def test_source_must_be_inside_storage(env):
    async with env.sessions() as session:
        storage = await session.scalar(select(Storage))
        storage.config = {"root": str(env.path.parent / "unrelated")}
        await session.commit()
    response = await env.client.post(f"/api/videos/{env.vid}/convert", json={"format": "mkv"})
    assert response.status_code == 409


@pytest.mark.parametrize("extension", ["mp4", "mkv", "mov", "ts"])
async def test_real_remux_and_download(env, extension):
    original = env.path.read_bytes()
    response = await env.client.post(f"/api/videos/{env.vid}/convert", json={"format": extension})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    blocked = await env.client.post(f"/api/videos/{env.vid}/rename", json={"name": "blocked"})
    assert blocked.status_code == 409
    result = await conversion.run_conversion({}, job_id)
    assert result.get("format") == extension, (await env.client.get(f"/api/jobs/{job_id}")).text
    status = (await env.client.get(f"/api/jobs/{job_id}")).json()
    assert status["state"] == "done" and status["progress"] == 1
    output = conversion.export_path(job_id, extension)
    source_info = parse_probe_json(await _run_ffprobe(str(env.path)))
    output_info = parse_probe_json(await _run_ffprobe(str(output)))
    assert output_info.video_codec == source_info.video_codec == "h264"
    assert output_info.audio_codec == source_info.audio_codec == "aac"
    assert abs(output_info.duration_ms - source_info.duration_ms) < 200
    downloaded = await env.client.get(f"/api/jobs/{job_id}/file")
    assert downloaded.status_code == 200
    assert downloaded.content == output.read_bytes()
    assert "attachment" in downloaded.headers["content-disposition"]
    ranged = await env.client.get(f"/api/jobs/{job_id}/file", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    assert env.path.read_bytes() == original


async def test_queued_cancel_is_not_started(env):
    response = await env.client.post(f"/api/videos/{env.vid}/convert", json={"format": "mkv"})
    job_id = response.json()["id"]
    canceled = await env.client.post(f"/api/jobs/{job_id}/cancel")
    assert canceled.json()["state"] == "canceled"
    await conversion.run_conversion({}, job_id)
    assert (await env.client.get(f"/api/jobs/{job_id}")).json()["state"] == "canceled"
    assert not conversion.export_path(job_id, "mkv").exists()
    assert (await env.client.get(f"/api/jobs/{job_id}/file")).status_code == 409


async def test_running_cancel_stops_process_and_cleans_partial(env, monkeypatch):
    original_command = conversion.remux_command

    def slow_command(source, output, binary):
        command = original_command(source, output, binary)
        command.insert(command.index("-i"), "-re")
        return command

    monkeypatch.setattr(conversion, "remux_command", slow_command)
    response = await env.client.post(f"/api/videos/{env.vid}/convert", json={"format": "mkv"})
    job_id = response.json()["id"]
    task = asyncio.create_task(conversion.run_conversion({}, job_id))
    partial = conversion.export_path(job_id, "mkv", partial=True)
    for _ in range(100):
        if partial.exists():
            break
        await asyncio.sleep(0.02)
    assert partial.exists()
    canceled = await env.client.post(f"/api/jobs/{job_id}/cancel")
    assert canceled.status_code == 200
    await asyncio.wait_for(task, timeout=3)
    assert not partial.exists()
    assert not conversion.export_path(job_id, "mkv").exists()
    assert (await env.client.get(f"/api/jobs/{job_id}")).json()["state"] == "canceled"


async def test_queue_failure_is_visible(env):
    env.pool.enqueue_job.side_effect = ConnectionError("test queue unavailable")
    response = await env.client.post(f"/api/videos/{env.vid}/convert", json={"format": "mkv"})
    assert response.status_code == 503
    jobs = (await env.client.get("/api/jobs?kind=convert_format")).json()["items"]
    assert jobs[0]["state"] == "error"


async def test_missing_result_has_clear_error(env):
    response = await env.client.post(f"/api/videos/{env.vid}/convert", json={"format": "mkv"})
    job_id = response.json()["id"]
    await conversion.run_conversion({}, job_id)
    conversion.export_path(job_id, "mkv").unlink()
    assert (await env.client.get(f"/api/jobs/{job_id}/file")).status_code == 410


async def test_disk_limit_cleans_output(env, monkeypatch):
    response = await env.client.post(f"/api/videos/{env.vid}/convert", json={"format": "mkv"})
    job_id = response.json()["id"]
    monkeypatch.setattr(settings, "export_max_file_mb", 0)
    await conversion.run_conversion({}, job_id)
    assert (await env.client.get(f"/api/jobs/{job_id}")).json()["state"] == "error"
    assert not conversion.export_path(job_id, "mkv", partial=True).exists()


async def test_incompatible_codec_does_not_transcode(env):
    incompatible = env.path.with_name("vp8.webm")
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10",
            "-t",
            "0.5",
            "-c:v",
            "libvpx",
            "-threads",
            "1",
            str(incompatible),
        ],
        check=True,
    )
    async with env.sessions() as session:
        source = await session.get(VideoSource, env.sid)
        source.local_path = str(incompatible)
        source.uri = f"local:{incompatible}"
        await session.commit()
    response = await env.client.post(f"/api/videos/{env.vid}/convert", json={"format": "mp4"})
    job_id = response.json()["id"]
    await conversion.run_conversion({}, job_id)
    status = (await env.client.get(f"/api/jobs/{job_id}")).json()
    assert status["state"] == "error"
    assert "保留原编码" in status["error"]
    assert not conversion.export_path(job_id, "mp4", partial=True).exists()


async def test_bad_job_filter_is_validation_error(env):
    assert (await env.client.get("/api/jobs?state=unknown")).status_code == 422


async def test_rename_waits_for_other_video_processing(env):
    async with env.sessions() as session:
        session.add(Job(kind="transcribe", video_id=env.vid, state=JobState.running, payload={}))
        await session.commit()
    response = await env.client.post(f"/api/videos/{env.vid}/rename", json={"name": "blocked"})
    assert response.status_code == 409
    assert env.path.is_file()
