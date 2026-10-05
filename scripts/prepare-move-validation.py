"""Isolated API, queue and fixtures; target uses another real filesystem."""
import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".run/background-move-validation"
OUT.mkdir(exist_ok=True)
assert not (OUT / "test.db").exists()
for port in (18427, 16427):
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", port)) != 0
os.environ.update(
    DATABASE_URL=f"sqlite+aiosqlite:///{OUT}/test.db",
    DATA_DIR=str(OUT/"data"), CACHE_DIR=str(OUT/"cache"),
    WORK_DIR=str(OUT/"work"), DOWNLOADS_DIR=str(OUT/"downloads"),
    REDIS_URL="redis://127.0.0.1:16427/0",
)
sys.path.insert(0, str(ROOT/"backend"))
from app.db import engine, SessionLocal
from app.models import Base, Storage, StorageType, Video, VideoSource
from app.services.probe import _run_ffprobe, parse_probe_json

origin = OUT / "media"
target = Path("/dev/shm/video-manage-move-validation-20261005")
assert not target.exists()
for p in (origin/"来源", origin/"本地分类", target/"归档", OUT/"data"):
    p.mkdir(parents=True, exist_ok=True)

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        a = Storage(name="移动验证源", type=StorageType.local, config={"root": str(origin)})
        b = Storage(name="跨盘归档", type=StorageType.local, config={"root": str(target)})
        s.add_all([a,b])
        await s.flush()
        fixtures=[]
        for number in range(1,4):
            path=origin/"来源"/f"背景移动测试-{number}.mp4"
            subprocess.run(["ffmpeg","-nostdin","-v","error","-f","lavfi","-i",
                            "testsrc2=size=320x180:rate=24","-t","2","-c:v","libx264",
                            "-threads","2",str(path)],check=True)
            raw=await _run_ffprobe(str(path))
            info=parse_probe_json(raw)
            v=Video(title=f"后台移动验证 {number}",source_kind="local",status="ready",meta=raw,tags=[],
                    **{k:getattr(info,k) for k in ["width","height","fps","duration_ms","size_bytes","container","video_codec"]})
            s.add(v); await s.flush()
            source=VideoSource(video_id=v.id,storage_id=a.id,source_type=StorageType.local,
                               uri=f"local:来源/{path.name}",local_path=str(path),is_primary=True)
            s.add(source); await s.flush()
            fixtures.append({"video":v.id,"source":source.id,"path":str(path)})
        await s.commit()
        (OUT/"fixtures.json").write_text(json.dumps({"videos":fixtures,"source_storage":a.id,"target_storage":b.id,"target":str(target)},ensure_ascii=False))
    await engine.dispose()
asyncio.run(main())

# Hold the directory lock until the browser test has submitted and left the page.
code = "import fcntl,time,pathlib; p=pathlib.Path("+repr(str(OUT))+"); f=(p/'data/.directory-write.lock').open('a'); fcntl.flock(f,fcntl.LOCK_EX); (p/'locked').touch();\nwhile not (p/'release').exists(): time.sleep(.1)"
commands=[
 ("lock", [sys.executable,"-c",code]),
 ("redis",["redis-server","--bind","127.0.0.1","--port","16427","--save","","--appendonly","no","--dir",str(OUT)]),
 ("worker",[str(ROOT/"backend/.venv/bin/arq"),"app.workers.move_settings.WorkerSettings"]),
 ("api",[str(ROOT/"backend/.venv/bin/uvicorn"),"app.main:app","--host","127.0.0.1","--port","18427"]),
]
for name,args in commands:
    with (OUT/f"{name}.log").open("a") as log:
        p=subprocess.Popen(args,cwd=ROOT/"backend",env=os.environ.copy(),stdout=log,stderr=log,start_new_session=True)
    (OUT/f"{name}.pid").write_text(str(p.pid))
    print(name,p.pid)
