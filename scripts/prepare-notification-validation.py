"""Isolated fixtures/server; never changes production jobs or originals."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".run/notification-validation"
OUT.mkdir(exist_ok=True)
assert not (OUT / "test.db").exists()
with socket.socket() as sock:
    assert sock.connect_ex(("127.0.0.1", 18425)) != 0
os.environ.update(
    DATABASE_URL=f"sqlite+aiosqlite:///{OUT}/test.db",
    DATA_DIR=str(OUT / "data"), CACHE_DIR=str(OUT / "cache"),
    WORK_DIR=str(OUT / "work"), DOWNLOADS_DIR=str(OUT / "downloads"),
)
sys.path.insert(0, str(ROOT / "backend"))
from app.db import engine, SessionLocal
from app.models import Base, Job, JobState, Storage, StorageType, Video, VideoSource

media = OUT / "media"
media.mkdir(exist_ok=True)
sample = media / "sample.mp4"
shutil.copyfile(ROOT / ".run/format-validation/samples/sample.mp4", sample)

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        storage = Storage(name="通知验证", type=StorageType.local, config={"root": str(media)})
        video = Video(title="通知测试视频", duration_ms=12000, width=320, height=180,
                      video_codec="h264", audio_codec="aac", container="mov",
                      video_profile="High", pix_fmt="yuv420p", source_kind="local")
        s.add_all([storage, video])
        await s.flush()
        s.add(VideoSource(video_id=video.id, storage_id=storage.id, source_type="local",
                          uri=str(sample), local_path=str(sample), is_primary=True, available=True))
        old = Job(kind="scan_library", state=JobState.running)
        s.add(old)
        await s.flush()
        s.add_all([Job(kind="scan_library", state=JobState.done) for _ in range(110)])
        await s.commit()
        (OUT / "fixtures.json").write_text(json.dumps({"old": old.id, "video": video.id}))
    await engine.dispose()
asyncio.run(main())
subprocess.run([sys.executable, str(ROOT / "scripts/start-notification-validation.py")], check=True)
