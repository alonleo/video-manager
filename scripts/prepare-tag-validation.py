"""Create a separate test library; only this validation database is used."""
import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".run/tag-validation"
OUT.mkdir(parents=True, exist_ok=True)
assert not (OUT / "test.db").exists(), "Validation database already exists; do not overwrite it"
with socket.socket() as sock:
    assert sock.connect_ex(("127.0.0.1", 18423)) != 0, "Validation port is busy"
os.environ.update(
    DATABASE_URL=f"sqlite+aiosqlite:///{OUT}/test.db",
    DATA_DIR=str(OUT / "data"), CACHE_DIR=str(OUT / "cache"),
)
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from app.db import engine, SessionLocal
from app.models import Base
from test_tags import seed_tags

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ids = await seed_tags(SessionLocal, OUT / "media")
    (OUT / "fixtures.json").write_text(json.dumps(ids))
    await engine.dispose()

asyncio.run(main())
with (OUT / "api.log").open("a") as log:
    process = subprocess.Popen(
        [str(ROOT / "backend/.venv/bin/uvicorn"), "app.main:app", "--host", "127.0.0.1", "--port", "18423"],
        cwd=ROOT / "backend", env=os.environ.copy(), stdout=log, stderr=log, start_new_session=True,
    )
(OUT / "api.pid").write_text(str(process.pid))
print("Tag validation API:", process.pid)
