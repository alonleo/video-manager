"""Isolated scroll-navigation library; originals and production history are untouched."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".run/scroll-validation"
OUT.mkdir(parents=True, exist_ok=True)
assert not (OUT / "test.db").exists(), "Validation database already exists"
with socket.socket() as sock:
    assert sock.connect_ex(("127.0.0.1", 18424)) != 0
os.environ.update(DATABASE_URL=f"sqlite+aiosqlite:///{OUT}/test.db",
                  DATA_DIR=str(OUT / "data"), CACHE_DIR=str(OUT / "cache"))
sys.path.insert(0, str(ROOT / "backend"))
from app.db import engine, SessionLocal
from app.models import Base, Storage, StorageType, Tag, Video, VideoSource, VideoTag

media = OUT / "media"
media.mkdir(exist_ok=True)
sample = media / "sample.mp4"
original = ROOT / ".run/format-validation/samples/sample.mp4"
if original.is_file():
    shutil.copyfile(original, sample)
else:
    subprocess.run(["ffmpeg","-nostdin","-v","error","-y","-f","lavfi","-i",
                    "testsrc2=size=320x180:rate=25","-t","12","-c:v","libx264",
                    "-preset","ultrafast","-pix_fmt","yuv420p","-movflags","+faststart",
                    str(sample)], check=True)
async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        storage=Storage(name="scroll validation",type=StorageType.local,config={"root":str(media)})
        tag=Tag(name="位置测试")
        session.add_all([storage,tag])
        await session.flush()
        ids=[]
        for index in range(1,73):
            video=Video(title=f"Scroll video {index:03}",duration_ms=12000,width=320,height=180,
                        video_codec="h264",video_profile="High",pix_fmt="yuv420p",audio_codec="aac",
                        container="mov",size_bytes=sample.stat().st_size,is_favorite=True,source_kind="local")
            session.add(video)
            await session.flush()
            session.add(VideoSource(video_id=video.id,storage_id=storage.id,source_type="local",
                                    uri=f"local:sample-{index}.mp4",local_path=str(sample),
                                    is_primary=True,available=True))
            session.add(VideoTag(video_id=video.id,tag_id=tag.id))
            ids.append(video.id)
        await session.commit()
        (OUT / "fixtures.json").write_text(json.dumps({"videos":ids,"storage":storage.id,"tag":tag.id}))
    await engine.dispose()
asyncio.run(main())
with (OUT / "api.log").open("a") as log:
    process=subprocess.Popen([str(ROOT / "backend/.venv/bin/uvicorn"),"app.main:app","--host","127.0.0.1","--port","18424"],
                             cwd=ROOT / "backend",env=os.environ.copy(),stdout=log,stderr=log,start_new_session=True)
(OUT / "api.pid").write_text(str(process.pid))
print("Scroll validation API:",process.pid)
