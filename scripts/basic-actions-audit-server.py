"""Isolated, real API/Redis/worker fixture for basic-actions-audit.mjs."""
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".run" / ("basic-actions-audit-" + uuid.uuid4().hex[:10])
WORK.mkdir(parents=True)
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update({
    "DATABASE_URL": f"sqlite+aiosqlite:///{WORK / 'audit.db'}",
    "DATA_DIR": str(WORK / "data"),
    "DOWNLOADS_DIR": str(WORK / "outputs"),
    "CACHE_DIR": str(WORK / "cache"),
    "WORK_DIR": str(WORK / "work"),
})

import uvicorn
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker
from app.api import operations
from app.config import settings
from app.db import engine, SessionLocal
from app.main import app
from app.models import Base, Storage, StorageType, Video, VideoSource
from app.services.probe import probe
from app.workers.conversion import run_conversion

async def main():
    source = WORK / "audit-original.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=640x360:rate=24", "-f", "lavfi", "-i",
        "sine=frequency=440", "-t", "3", "-c:v", "libx264",
        "-threads", "1", "-c:a", "aac", str(source),
    ], check=True)
    info = await probe(str(source))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        storage = Storage(name="操作验证目录", type=StorageType.local, config={"root": str(WORK)})
        video = Video(title="操作验证 · 测试影像", source_kind="local", **{
            key: getattr(info, key) for key in [
                "duration_ms", "width", "height", "fps", "video_codec", "video_profile",
                "pix_fmt", "audio_codec", "audio_channels", "container", "size_bytes",
            ]
        })
        session.add_all([storage, video])
        await session.flush()
        session.add(VideoSource(video_id=video.id, storage_id=storage.id,
            source_type=StorageType.local, uri=f"local:{source}",
            local_path=str(source), is_primary=True, available=True))
        await session.commit()
    queue = "arq:basic-actions-audit:" + uuid.uuid4().hex
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    class IsolatedQueue:
        async def enqueue_job(self, *args, **kwargs):
            kwargs["_job_id"] = queue + ":" + kwargs["_job_id"]
            return await pool.enqueue_job(*args, _queue_name=queue, **kwargs)
    async def get_pool():
        return IsolatedQueue()
    operations.get_pool = get_pool
    worker = Worker(
        [run_conversion], queue_name=queue, redis_pool=pool, handle_signals=False,
        max_jobs=2, max_tries=1, keep_result=30, poll_delay=0.1,
        health_check_key=queue + ":health",
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8431, log_level="warning"))
    print(f"AUDIT_WORK={WORK}", flush=True)
    worker_task = asyncio.create_task(worker.async_run())
    try:
        await server.serve()
    finally:
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)
        await worker.close()
        await engine.dispose()

asyncio.run(main())
