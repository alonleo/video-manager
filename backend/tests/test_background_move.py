"""Real file/DB lifecycle tests; queue dispatch is captured, never production Redis."""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_directory_categories import env, force_cross_device  # noqa: F401

from app.api import categories, jobs, videos
from app.db import get_session
from app.models import Job, JobState, Video, VideoSource
from app.services import directory_categories as dc
from app.workers import move as worker


@pytest_asyncio.fixture
async def bg(env, monkeypatch):  # noqa: F811
    session, root, other, a, b, video, source = env
    factory = async_sessionmaker(session.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    dispatched = []

    class Queue:
        async def enqueue_job(self, *args, **kwargs):
            dispatched.append((args, kwargs))
            return SimpleNamespace(job_id=kwargs["_job_id"])

    async def pool():
        return Queue()

    monkeypatch.setattr(categories, "get_pool", pool)
    app = FastAPI()
    for router in [categories.router, jobs.router, videos.router]:
        app.include_router(router)

    async def dependency():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = dependency
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory, dispatched, env


async def submit(bg, path="课程"):
    client, _, dispatched, (_, _, _, a, _, video, source) = bg
    response = await client.post(
        f"/api/videos/{video.id}/move",
        json={"source_id": source.id, "storage_id": a.id, "path": path},
    )
    assert response.status_code == 202, response.text
    assert dispatched[-1][1]["_queue_name"] == "arq:file-moves"
    return response.json()


async def test_submit_returns_before_move_and_notification_after_completion(bg):
    client, factory, _, (_, root, _, _, _, video, _) = bg
    cursor = (await client.post("/api/jobs/notifications", json={})).json()
    job = await submit(bg)
    assert job["state"] == "queued" and (root / "旅行/海边.mp4").is_file()
    duplicate = await submit(bg)
    assert duplicate["id"] == job["id"]
    await worker.run_move({}, job["id"])
    result = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert result["state"] == "done" and result["progress"] == 1
    assert (root / "课程/海边.mp4").read_bytes() == b"video-content" * 100
    assert not (root / "旅行/海边.mp4").exists()
    detail = (await client.get(f"/api/videos/{video.id}")).json()
    assert detail["is_favorite"] and detail["categories"][0]["name"] == "课程"
    notice = (
        await client.post(
            "/api/jobs/notifications",
            json={
                "cursor": cursor["cursor"],
                "pending": cursor["pending"],
            },
        )
    ).json()
    assert notice["items"][0]["kind"] == "move_video"
    await worker.run_move({}, job["id"])
    assert (root / "课程/海边.mp4").is_file()  # duplicate delivery does nothing


async def test_queued_cancel_leaves_file_and_index_unchanged(bg):
    client, _, _, (_, root, _, _, _, _, _) = bg
    job = await submit(bg)
    assert (await client.post(f"/api/jobs/{job['id']}/cancel")).status_code == 200
    await worker.run_move({}, job["id"])
    assert (root / "旅行/海边.mp4").exists()
    assert not (root / "课程/海边.mp4").exists()


async def test_cross_disk_progress_running_cancel_and_graceful_shutdown(bg, monkeypatch):
    client, _, _, (_, root, _, _, _, _, _) = bg
    force_cross_device(monkeypatch)
    real = dc.transfer_file

    def slow(original, destination, progress):
        progress(0.35, "正在复制文件 · 55%")
        time.sleep(1)
        return real(original, destination, progress)

    monkeypatch.setattr(dc, "transfer_file", slow)
    job = await submit(bg)
    task = asyncio.create_task(worker.run_move({}, job["id"]))
    await asyncio.sleep(0.65)
    running = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert running["state"] == "running" and running["progress"] == 0.35
    assert (await client.post(f"/api/jobs/{job['id']}/cancel")).status_code == 409
    task.cancel()  # graceful shutdown must finish copy/index instead of orphaning the thread
    await task
    done = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert done["state"] == "done"
    assert (root / "课程/海边.mp4").read_bytes() == b"video-content" * 100
    assert not list(root.rglob(".video-move-*"))


async def test_changed_source_rejected_after_queue(bg):
    client, _, _, (_, root, *_rest) = bg
    job = await submit(bg)
    (root / "旅行/海边.mp4").write_bytes(b"changed externally")
    await worker.run_move({}, job["id"])
    result = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert result["state"] == "error" and "发生变化" in result["error"]
    assert (root / "旅行/海边.mp4").read_bytes() == b"changed externally"


async def test_target_appearing_after_queue_is_never_overwritten(bg):
    client, _, _, (_, root, *_rest) = bg
    job = await submit(bg)
    (root / "课程/海边.mp4").write_bytes(b"keep target")
    await worker.run_move({}, job["id"])
    result = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert result["state"] == "error" and "同名" in result["error"]
    assert (root / "课程/海边.mp4").read_bytes() == b"keep target"
    assert (root / "旅行/海边.mp4").exists()


async def test_queue_failure_is_recorded_without_moving(bg, monkeypatch):
    client, factory, _, (_, root, _, a, _, video, source) = bg

    async def unavailable():
        raise RuntimeError("unavailable")

    monkeypatch.setattr(categories, "get_pool", unavailable)
    response = await client.post(
        f"/api/videos/{video.id}/move",
        json={"source_id": source.id, "storage_id": a.id, "path": "课程"},
    )
    assert response.status_code == 503
    async with factory() as s:
        job = await s.get(Job, 1)
        assert job.state == JobState.error and job.finished_at
    assert (root / "旅行/海边.mp4").exists()


async def test_other_videos_can_queue_while_library_is_locked_and_wait(bg):
    client, factory, _, (_, root, _, a, _, _, _) = bg
    first = await submit(bg)
    async with factory() as s:
        video = Video(title="另一部", tags=[])
        s.add(video)
        await s.flush()
        path = root / "旅行/另一部.mp4"
        path.write_bytes(b"second")
        source = VideoSource(
            video_id=video.id,
            storage_id=a.id,
            source_type="local",
            local_path=str(path),
            uri="local:旅行/另一部.mp4",
        )
        s.add(source)
        await s.commit()
        body = {"source_id": source.id, "storage_id": a.id, "path": "课程"}
        video_id = video.id
    with dc.library_write_lock():
        response = await client.post(f"/api/videos/{video_id}/move", json=body)
        assert response.status_code == 202
        task = asyncio.create_task(worker.run_move({}, first["id"]))
        await asyncio.sleep(0.15)
        assert (await client.get(f"/api/jobs/{first['id']}")).json()["state"] == "queued"
    await task
    await worker.run_move({}, response.json()["id"])
    assert (root / "课程/另一部.mp4").read_bytes() == b"second"


def test_copy_and_verify_progress(monkeypatch, tmp_path):
    source = tmp_path / "海边.mp4"
    source.write_bytes(b"unique bytes" * 900000)
    force_cross_device(monkeypatch)
    events = []
    copied, _ = dc.transfer_file(
        source, tmp_path / "target.mp4", lambda p, msg: events.append((p, msg))
    )
    assert copied
    assert any("复制" in msg for _, msg in events) and any("校验" in msg for _, msg in events)
    assert all(a[0] <= b[0] for a, b in zip(events, events[1:], strict=False))
    assert source.read_bytes() == (tmp_path / "target.mp4").read_bytes()
