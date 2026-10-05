"""Isolated browser fixture: never uses the personal media library."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".run" / ("source-deletion-audit-" + uuid.uuid4().hex[:10])
WORK.mkdir(parents=True)
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update({
    "DATABASE_URL": f"sqlite+aiosqlite:///{WORK / 'audit.db'}",
    "DATA_DIR": str(WORK / "data"), "DOWNLOADS_DIR": str(WORK / "downloads"),
    "CACHE_DIR": str(WORK / "cache"), "WORK_DIR": str(WORK / "work"),
})

import uvicorn
from app.db import engine, SessionLocal
from app.main import app
from app.models import Base, Storage, StorageType, Video, VideoSource, WatchHistory
from app.services.probe import probe

async def main():
    media = WORK / "media"
    media.mkdir()
    paths = [media / name for name in ["first.mp4", "second.mp4", "keep.mp4"]]
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=24", "-t", "2", "-c:v", "libx264",
                    "-threads", "1", str(paths[0])], check=True)
    for path in paths[1:]:
        shutil.copyfile(paths[0], path)
    (media / "first.srt").write_text("Keep this subtitle file")
    info = await probe(str(paths[0]))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        storage = Storage(name="删除验证临时目录", type=StorageType.local, config={"root": str(media)})
        session.add(storage)
        await session.flush()
        for index, sources in enumerate([paths[:2], paths[2:]], 1):
            video = Video(title=f"删除验证视频 {index}", source_kind="local", is_favorite=True, **{
                key: getattr(info, key) for key in [
                    "duration_ms", "width", "height", "fps", "video_codec", "video_profile",
                    "pix_fmt", "audio_codec", "audio_channels", "container", "size_bytes",
                ]
            })
            session.add(video)
            await session.flush()
            for source_index, path in enumerate(sources):
                session.add(VideoSource(video_id=video.id, storage_id=storage.id,
                            source_type=StorageType.local, uri=f"local:{path.name}",
                            local_path=str(path), is_primary=source_index == 0, size_bytes=path.stat().st_size))
            session.add(WatchHistory(video_id=video.id, position_ms=700))
        await session.commit()
    (ROOT / ".run/source-deletion-audit-latest.json").write_text(json.dumps({
        "work": str(WORK), "paths": [str(p) for p in paths], "port": 18438,
    }))
    print(f"AUDIT_WORK={WORK}", flush=True)
    try:
        await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18438, log_level="warning")).serve()
    finally:
        await engine.dispose()

asyncio.run(main())
