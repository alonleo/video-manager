"""Download integration tests use generated media, local HTTP and an isolated database."""

import asyncio
import functools
import http.server
import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import db
from app.api import downloads as api
from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Base, Job, JobState, Storage, Video, VideoSource
from app.services import downloads as service
from app.services.download_config import DownloadConfig, DownloadRequest
from app.workers import queue, tasks


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("download-http")
    sample = root / "sample.mp4"
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-c:a",
            "aac",
            str(sample),
        ],
        check=True,
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(root))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield sample, f"http://127.0.0.1:{server.server_port}/sample.mp4"
    server.shutdown()
    server.server_close()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch, media):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    for module in [db, tasks, service]:
        monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "work_dir", tmp_path / "work")
    monkeypatch.setattr(settings, "downloads_dir", tmp_path / "downloads")
    monkeypatch.setattr(service, "_slots", asyncio.Semaphore(2))
    monkeypatch.setattr(api, "validate_public_host", AsyncMock())
    monkeypatch.setattr(service, "validate_public_host", AsyncMock())
    pool = SimpleNamespace(enqueue_job=AsyncMock(return_value=SimpleNamespace(job_id="test")))
    monkeypatch.setattr(queue, "get_pool", AsyncMock(return_value=pool))
    original = service.command

    def local_command(payload, config, stage):
        args = original(payload, config, stage)
        args[-1] = media[1]
        args[3:3] = ["--proxy", ""]
        return args

    monkeypatch.setattr(service, "command", local_command)
    monkeypatch.setattr(service.probe, "extract_thumbnails", AsyncMock(return_value=(None, None)))

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(
            client=client, sessions=sessions, pool=pool, media=media, root=tmp_path
        )
    app.dependency_overrides.clear()
    await engine.dispose()


async def create(env, **patch):
    response = await env.client.post(
        "/api/downloads", json={"url": "https://example.com/video", **patch}
    )
    assert response.status_code == 202, response.text
    job = response.json()
    async with env.sessions() as session:
        job["payload"] = (await session.get(Job, job["id"])).payload
    return job


async def execute(env, job):
    async with env.sessions() as session:
        current = await session.get(Job, job["id"])
        payload = current.payload
    return await tasks.run_job({}, job["id"], "download_url", payload)


async def status(env, job):
    return (await env.client.get("/api/jobs/" + str(job["id"]))).json()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/video",
        "https://127.0.0.1/a",
        "https://localhost/a",
        "https://192.168.1.1/a",
        "https://user:pass@example.com/a",
        "https://example.com:8443/a",
        "https://example.com/a https://example.org/b",
    ],
)
async def test_invalid_url_rejected(env, url):
    response = await env.client.post("/api/downloads", json={"url": url})
    assert response.status_code == 422
    env.pool.enqueue_job.assert_not_called()


def test_share_text_and_dedicated_options():
    body = DownloadRequest(url="快来看这个视频 https://v.douyin.com/abc123/ 复制此链接打开抖音")
    assert body.url == "https://v.douyin.com/abc123/"
    with pytest.raises(ValueError):
        DownloadRequest(url="https://example.com/a", path="../escape")
    with pytest.raises(ValueError):
        DownloadConfig(proxy="http://user:secret@127.0.0.1:7897")


async def test_real_download_import_and_completed_notification(env):
    cursor = (await env.client.post("/api/jobs/notifications", json={})).json()
    job = await create(env, quality="720")
    result = await execute(env, job)
    item = await status(env, job)
    assert item["state"] == "done" and item["progress"] == 1
    assert result["video_id"] > 0
    async with env.sessions() as session:
        source = await session.scalar(select(VideoSource))
        video = await session.get(Video, result["video_id"])
        assert video.video_codec == "h264" and video.audio_codec == "aac"
        assert video.width == 160 and video.duration_ms > 0
        assert source.origin_url == "https://example.com/video"
        assert source.available and source.quick_hash
        assert source.storage_id == job["payload"]["storage_id"]
        assert open(source.local_path, "rb").read() == env.media[0].read_bytes()
    notification = (await env.client.post("/api/jobs/notifications", json=cursor)).json()
    assert any(j["id"] == job["id"] and j["state"] == "done" for j in notification["items"])
    assert not service.workspace(job["payload"]).exists()


async def test_real_mkv_remux(env):
    job = await create(env, container="mkv")
    result = await execute(env, job)
    assert result["filename"].endswith(".mkv")
    assert (await status(env, job))["state"] == "done"


async def test_duplicate_active_submission(env):
    job = await create(env)
    response = await env.client.post("/api/downloads", json={"url": "https://example.com/video"})
    assert response.status_code == 409 and str(job["id"]) in response.text
    env.pool.enqueue_job.assert_awaited_once()


async def test_queued_cancel_and_explicit_retry(env):
    job = await create(env)
    assert (await env.client.post(f"/api/jobs/{job['id']}/cancel")).status_code == 200
    assert (await execute(env, job))["canceled"]
    response = await env.client.post(f"/api/downloads/{job['id']}/retry")
    assert response.status_code == 202
    retried = response.json()
    async with env.sessions() as session:
        retried["payload"] = (await session.get(Job, retried["id"])).payload
    assert retried["payload"]["download_token"] == job["payload"]["download_token"]
    await execute(env, retried)
    assert (await status(env, retried))["state"] == "done"


async def test_running_cancel_terminates_child(env, monkeypatch):
    def slow(*_):
        return [sys.executable, "-c", "import time; time.sleep(60)"]

    monkeypatch.setattr(service, "command", slow)
    job = await create(env)
    task = asyncio.create_task(execute(env, job))
    for _ in range(100):
        current = await status(env, job)
        if "解析" in (current.get("message") or ""):
            break
        await asyncio.sleep(0.03)
    await env.client.post(f"/api/jobs/{job['id']}/cancel")
    await asyncio.wait_for(task, 4)
    assert (await status(env, job))["state"] == "canceled"
    async with env.sessions() as session:
        assert await session.scalar(select(Video)) is None


async def test_external_failure_is_safe_and_retryable(env, monkeypatch):
    def fail(*_):
        return [
            sys.executable,
            "-c",
            "import sys; print('ERROR: HTTP 403 secret-cookie',file=sys.stderr); sys.exit(1)",
        ]

    monkeypatch.setattr(service, "command", fail)
    job = await create(env)
    with pytest.raises(ValueError):
        await execute(env, job)
    current = await status(env, job)
    assert current["state"] == "error" and "Cookie" in current["error"]
    assert "secret-cookie" not in json.dumps(current)
    assert (await env.client.post(f"/api/downloads/{job['id']}/retry")).status_code == 202


async def test_existing_filename_not_overwritten(env):
    job = await create(env)
    target = env.root / "downloads/online/sample [sample].mp4"
    target.write_bytes(b"keep original")
    with pytest.raises(ValueError, match="同名"):
        await execute(env, job)
    assert target.read_bytes() == b"keep original"
    assert (await status(env, job))["state"] == "error"


async def test_queue_failure_has_durable_error(env):
    env.pool.enqueue_job.side_effect = ConnectionError("queue unavailable")
    response = await env.client.post("/api/downloads", json={"url": "https://example.com/video"})
    assert response.status_code == 503
    async with env.sessions() as session:
        job = await session.scalar(select(Job))
        assert job.state == JobState.error


async def test_invalid_category_and_missing_engine(env, monkeypatch):
    response = await env.client.post(
        "/api/downloads", json={"url": "https://example.com/a", "storage_id": 999}
    )
    assert response.status_code == 404
    monkeypatch.setattr(api, "engine_status", lambda: {"installed": False, "ffmpeg": True})
    response = await env.client.post("/api/downloads", json={"url": "https://example.com/a"})
    assert response.status_code == 503


async def test_cookie_config_is_explicit_and_not_in_job_payload(env):
    response = await env.client.put("/api/downloads/settings", json={"cookies_browser": "firefox"})
    assert response.status_code == 200
    job = await create(env)
    assert "cookies_browser" not in job["payload"]
    response = await env.client.put("/api/downloads/settings", json={"cookie_file": "/missing.txt"})
    assert response.status_code == 422
    assert (await env.client.get("/api/downloads/settings")).json()["cookies_browser"] == "firefox"


async def test_canceled_publication_rolls_back_file_and_library(env, monkeypatch):
    job = await create(env)
    original = service.publish

    async def cancel_before_publish(payload, job_id, file, meta, progress):
        async with env.sessions() as session:
            item = await session.get(Job, job_id)
            item.state = JobState.canceled
            await session.commit()
        # Bypass only the progress cancellation check to reach atomic publication guard.
        return await original(payload, job_id, file, meta, AsyncMock())

    monkeypatch.setattr(service, "publish", cancel_before_publish)
    await execute(env, job)
    assert (await status(env, job))["state"] == "canceled"
    async with env.sessions() as session:
        assert await session.scalar(select(Video)) is None
        storage = await session.scalar(select(Storage))
        from pathlib import Path

        assert not list(Path(storage.config["root"]).glob("*.mp4"))
