"""Serve an isolated database and generated media for directory UI verification."""
import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".run" / ("directory-audit-" + uuid.uuid4().hex[:10])
WORK.mkdir(parents=True)
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update({
    "DATABASE_URL": f"sqlite+aiosqlite:///{WORK / 'audit.db'}",
    "DATA_DIR": str(WORK / "data"), "CACHE_DIR": str(WORK / "cache"),
    "WORK_DIR": str(WORK / "work"), "DOWNLOADS_DIR": str(WORK / "outputs"),
})

import uvicorn
from app.db import engine, SessionLocal
from app.main import app
from app.models import Base, Storage, StorageType, Video, VideoSource, WatchHistory


async def main():
    media = WORK / "media"
    (media / "旅行").mkdir(parents=True)
    source = media / "旅行" / "海边.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
        "-t", "2", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(source),
    ], check=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        storage = Storage(name="分类验收", type=StorageType.local, config={"root": str(media)})
        video = Video(title="海边影像", source_kind="local", is_favorite=True,
                      duration_ms=2000, width=320, height=180, container="mp4",
                      video_codec="h264", pix_fmt="yuv420p", size_bytes=source.stat().st_size)
        session.add_all([storage, video])
        await session.flush()
        session.add(VideoSource(video_id=video.id, storage_id=storage.id,
            source_type=StorageType.local, uri="local:旅行/海边.mp4",
            local_path=str(source), is_primary=True, available=True))
        session.add(WatchHistory(video_id=video.id, position_ms=500, played_ms=500))
        await session.commit()
    marker = {"work": str(WORK), "root": str(media), "port": 8433}
    (ROOT / ".run/directory-audit-latest.json").write_text(json.dumps(marker))
    print(json.dumps(marker), flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8433, log_level="warning"))
    await server.serve()


asyncio.run(main())
