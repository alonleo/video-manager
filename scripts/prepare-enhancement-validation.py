"""Fully isolated API/Redis/worker and real media for enhancement UI validation."""
import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".run/enhancement-validation"
OUT.mkdir(exist_ok=True)
assert not (OUT / "test.db").exists(), "Validation DB already exists"
for port in (18426, 16426):
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", port)) != 0
os.environ.update(
    DATABASE_URL=f"sqlite+aiosqlite:///{OUT}/test.db",
    DATA_DIR=str(OUT / "data"), CACHE_DIR=str(OUT / "cache"),
    WORK_DIR=str(OUT / "work"), DOWNLOADS_DIR=str(OUT / "downloads"),
    REDIS_URL="redis://127.0.0.1:16426/0",
)
sys.path.insert(0, str(ROOT / "backend"))
from app.db import engine, SessionLocal
from app.models import Base, Storage, StorageType, Video, VideoSource
from app.services.probe import _run_ffprobe, parse_probe_json

media = OUT / "media"
media.mkdir(exist_ok=True)

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        storage = Storage(name="增强验证", type=StorageType.local, config={"root": str(media)})
        s.add(storage)
        await s.flush()
        ids = []
        for name, size, rate, span in [
            ("motion", "160x90", "24", "6"), ("portrait", "90x160", "12", "1"),
            ("ai-small", "64x48", "4", "0.5"),
        ]:
            path = media / (name + ".mp4")
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size={size}:rate={rate}", "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", span, "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(path),
            ], check=True)
            raw = await _run_ffprobe(str(path))
            info = parse_probe_json(raw)
            video = Video(title={"motion":"运动画面测试","portrait":"竖屏比例测试","ai-small":"AI 短片测试"}[name],
                          source_kind="local", status="ready", meta=raw,
                          **{k: getattr(info,k) for k in ["width","height","duration_ms","fps","video_codec",
                             "audio_codec","video_profile","pix_fmt","container","size_bytes","bitrate_bps"]})
            s.add(video)
            await s.flush()
            s.add(VideoSource(video_id=video.id, storage_id=storage.id, source_type=StorageType.local,
                              uri=f"local:{path}", local_path=str(path), is_primary=True, available=True,
                              size_bytes=path.stat().st_size))
            ids.append(video.id)
        await s.commit()
        (OUT / "fixtures.json").write_text(json.dumps({"videos":ids}))
    await engine.dispose()
asyncio.run(main())
for name, args in [
    ("redis", ["redis-server","--bind","127.0.0.1","--port","16426","--save","","--appendonly","no","--dir",str(OUT)]),
    ("worker", [str(ROOT/"backend/.venv/bin/arq"),"app.workers.enhancement_settings.WorkerSettings"]),
    ("api", [str(ROOT/"backend/.venv/bin/uvicorn"),"app.main:app","--host","127.0.0.1","--port","18426"]),
]:
    with (OUT / f"{name}.log").open("a") as log:
        p = subprocess.Popen(args,cwd=ROOT/"backend",env=os.environ.copy(),stdout=log,stderr=log,start_new_session=True)
    (OUT/f"{name}.pid").write_text(str(p.pid))
    print(name,p.pid)
