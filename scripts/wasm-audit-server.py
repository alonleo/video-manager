"""Isolated player validation server; never uses the real video database."""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".run/wasm-validation"
os.environ.update(DATABASE_URL=f"sqlite+aiosqlite:///{OUT}/test.db", DATA_DIR=str(OUT/"data"),
                  CACHE_DIR=str(OUT/"cache"), WORK_DIR=str(OUT/"work"), DOWNLOADS_DIR=str(OUT/"downloads"))
sys.path.insert(0, str(ROOT/"backend"))
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api import videos, playback, subtitles, categories, jobs, misc, storages, operations, downloads
DIST = Path(os.environ.get("WASM_FRONTEND_DIST", str(ROOT / "frontend/dist")))
app = FastAPI()
for module in [videos, playback, subtitles, categories, jobs, misc, storages, operations, downloads]:
    app.include_router(module.router)
app.mount("/assets", StaticFiles(directory=DIST/"assets"), name="assets")
app.mount("/player-engine", StaticFiles(directory=DIST/"player-engine"), name="player-engine")
@app.get("/{path:path}")
async def index(path: str):
    return FileResponse(DIST/"index.html")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8427)
