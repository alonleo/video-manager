"""arq worker 配置。

启动：uv run arq app.workers.settings.WorkerSettings

并发说明：单 worker 进程内用 asyncio 并发 job，max_jobs 控制同时在跑的
任务数。扫描/探测是 IO 与子进程混合，可以适度并发；转录必须串行，
Phase 4 会把它拆到独立的 WorkerSettings（不同队列）。
"""

from __future__ import annotations

import logging

from arq.connections import RedisSettings

from app.config import settings
from app.workers.conversion import run_conversion
from app.workers.tasks import run_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [worker] %(name)s: %(message)s",
)


async def startup(ctx: dict) -> None:
    ctx["settings"] = settings
    logging.getLogger(__name__).info("worker 启动，redis=%s", settings.redis_url)


async def shutdown(ctx: dict) -> None:
    from app.workers.queue import close_pool

    await close_pool()
    logging.getLogger(__name__).info("worker 关闭")


class WorkerSettings:
    functions = [run_job, run_conversion]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # 扫描会并发跑 ffprobe 子进程，2~4 比较合适；转码另有自己的队列
    max_jobs = 4
    job_timeout = 6 * 3600  # 大目录扫描可能很久
    keep_result = 3600
    max_tries = 1  # 失败不自动重试，避免半途重复扫描
    health_check_interval = 60
