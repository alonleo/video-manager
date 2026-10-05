"""Start a fully isolated notification validation API, queue and worker."""
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root / ".run/notification-validation"
env = os.environ | {
    "DATABASE_URL": f"sqlite+aiosqlite:///{out}/test.db",
    "DATA_DIR": str(out / "data"), "CACHE_DIR": str(out / "cache"),
    "WORK_DIR": str(out / "work"), "DOWNLOADS_DIR": str(out / "downloads"),
    "REDIS_URL": "redis://127.0.0.1:16425/0",
}
old_pid_file = out / "api.pid"
if old_pid_file.exists():
    pid = int(old_pid_file.read_text())
    proc = Path(f"/proc/{pid}/cmdline")
    if proc.exists() and b"18425" in proc.read_bytes():
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", 18425)) != 0: break
            time.sleep(.1)
for port in [16425, 18425]:
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", port)) != 0, f"Port occupied: {port}"
def start(name, args):
    with (out / f"{name}.log").open("a") as log:
        p = subprocess.Popen(args, cwd=root / "backend", env=env,
                             stdout=log, stderr=log, start_new_session=True)
    (out / f"{name}.pid").write_text(str(p.pid))
    print(name, p.pid)
start("redis", ["redis-server", "--bind", "127.0.0.1", "--port", "16425",
                "--save", "", "--appendonly", "no", "--dir", str(out)])
start("worker", [str(root / "backend/.venv/bin/arq"), "app.workers.settings.WorkerSettings"])
start("api", [str(root / "backend/.venv/bin/uvicorn"), "app.main:app", "--host", "127.0.0.1", "--port", "18425"])
