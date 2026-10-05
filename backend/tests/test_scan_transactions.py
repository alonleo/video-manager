"""Worker progress must not compete with the scanner's own SQLite writer lock."""

import shutil

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base, Job, JobState, Storage, StorageType, VideoSource
from app.services import library
from app.workers import tasks


@pytest_asyncio.fixture
async def scan_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/scan.db", connect_args={"timeout": 0.05}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def configure(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=50")
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(tasks, "SessionLocal", sessions)
    root = tmp_path / "media"
    root.mkdir()
    async with sessions() as session:
        storage = Storage(name="scan test", type=StorageType.local, config={"root": str(root)})
        session.add(storage)
        await session.flush()
        payload = {"storage_id": storage.id, "do_probe": False}
        job = Job(kind="scan_library", state=JobState.queued, payload=payload)
        session.add(job)
        await session.commit()
        job_id = job.id
    try:
        yield sessions, root, job_id, payload
    finally:
        await engine.dispose()


@pytest.mark.parametrize("kind", ["video", "duplicate_photos"])
async def test_real_worker_progress_with_skipped_probe(scan_env, kind):
    sessions, root, job_id, payload = scan_env
    if kind == "video":
        for i in range(12):
            (root / f"movie-{i:02}.mp4").write_bytes(bytes([i]) * 2000)
    else:
        Image.new("RGB", (100, 80), "red").save(root / "photo-00.jpg")
        for i in range(1, 12):
            shutil.copyfile(root / "photo-00.jpg", root / f"photo-{i:02}.jpg")
    result = await tasks.run_job({}, job_id, "scan_library", payload)
    assert result["scanned"] == 12
    assert result["linked"] == (11 if kind == "duplicate_photos" else 0)
    async with sessions() as session:
        job = await session.get(Job, job_id)
        assert job.state == JobState.done and job.progress == 1
        assert job.error is None
        assert await session.scalar(select(func.count()).select_from(VideoSource)) == 12
    # An unchanged rescan also exercises the metadata-skipped path.
    async with sessions() as session:
        retry = Job(kind="scan_library", state=JobState.queued, payload=payload)
        session.add(retry)
        await session.commit()
        retry_id = retry.id
    result = await tasks.run_job({}, retry_id, "scan_library", payload)
    assert result["updated"] == 12 and result["probed"] == 0


async def test_scanner_releases_writer_before_expensive_photo_probe(scan_env, monkeypatch):
    sessions, root, job_id, payload = scan_env
    Image.new("RGB", (100, 80), "blue").save(root / "photo.jpg")
    original_probe = library._probe_video
    checked = False

    async def probe_with_concurrent_writer(session, video_id, uri):
        nonlocal checked
        async with sessions() as other:
            # Equivalent to an independent worker/API write while image decoding runs.
            assert await other.scalar(select(func.count()).select_from(VideoSource)) == 1
            await other.execute(update(Job).where(Job.id == job_id).values(message="other writer"))
            await other.commit()
        checked = True
        return await original_probe(session, video_id, uri)

    monkeypatch.setattr(library, "_probe_video", probe_with_concurrent_writer)
    result = await tasks.run_job({}, job_id, "scan_library", payload)
    assert checked and result["probed"] == 1


async def test_progress_observes_durable_sources_and_cancellation(scan_env):
    sessions, root, job_id, payload = scan_env
    for i in range(12):
        (root / f"movie-{i:02}.mp4").write_bytes(bytes([i]) * 2000)
    reports = []

    async def progress(done, total, message):
        async with sessions() as other:
            assert await other.scalar(select(func.count()).select_from(VideoSource)) == done
            await other.execute(update(Job).where(Job.id == job_id).values(progress=done / total))
            await other.commit()
        reports.append(done)
        if done >= 6:
            raise tasks.JobCanceled()

    async with sessions() as session:
        with pytest.raises(tasks.JobCanceled):
            await library.scan_storage(
                session, payload["storage_id"], do_probe=False, progress_cb=progress
            )
    assert reports == [1, 6]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(VideoSource)) == 6
    assert len(list(root.glob("*.mp4"))) == 12
