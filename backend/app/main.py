"""FastAPI 应用入口。

启动：uv run uvicorn app.main:app --reload --port 8420
生产：前端构建产物由本服务静态托管，只需跑 api + worker 两个进程。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import jobs, misc, operations, playback, storages, videos, photos
from app.api import categories, downloads, enhancement, subtitles
from app.api import enhancement
from app.config import PROJECT_ROOT, settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [api] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 确保运行时目录存在
    for d in (settings.data_dir, settings.cache_dir, settings.work_dir, settings.downloads_dir):
        d.mkdir(parents=True, exist_ok=True)
    log.info("数据目录: %s", settings.data_dir)
    log.info("数据库: %s", settings.database_url)
    try:
        yield
    finally:
        from app.services.transport import cache as playback_cache
        from app.workers.queue import close_pool

        await playback_cache.close()
        await close_pool()


app = FastAPI(
    title="视频管理平台",
    version="0.1.0",
    description="个人视频库：多来源索引、在线播放、AI 转录与总结",
    lifespan=lifespan,
)

# 仅本机使用；开发期 Vite 跑在 5173，需要放行
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        f"http://{settings.api_host}:{settings.api_port}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

app.include_router(videos.router)
app.include_router(photos.router)
app.include_router(categories.router)
app.include_router(playback.router)
app.include_router(storages.router)
app.include_router(jobs.router)
app.include_router(operations.router)
app.include_router(enhancement.router)
app.include_router(misc.router)
app.include_router(subtitles.router)
app.include_router(downloads.router)


# ---------------------------------------------------------------- 前端静态托管
# 生产模式：frontend/dist 存在时由后端直接服务，无需 Node 常驻。
# 开发模式：Vite dev server 代理，这里自然跳过。

_dist = PROJECT_ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """SPA 路由回退：非 /api 路径一律返回 index.html。"""
        candidate = _dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # 入口文件引用带内容哈希的静态资源，每次都应向服务端确认最新版；
        # 否则浏览器的启发式缓存会让新构建在刷新后仍显示旧界面。
        return FileResponse(
            _dist / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
else:

    @app.get("/", include_in_schema=False)
    async def dev_root():
        return {
            "message": "前端尚未构建。开发模式请访问 Vite (默认 http://127.0.0.1:5173)",
            "api_docs": "/docs",
        }


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )


if __name__ == "__main__":
    run()
