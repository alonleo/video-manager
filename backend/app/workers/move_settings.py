"""Run with: arq app.workers.move_settings.WorkerSettings"""

import logging

from arq.connections import RedisSettings

from app.config import settings
from app.workers.move import QUEUE, run_move, shutdown, startup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [move] %(message)s")


class WorkerSettings:
    functions = [run_move]
    queue_name = QUEUE
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1
    job_timeout = 7 * 24 * 3600
    job_completion_wait = 30
    max_tries = 1
    keep_result = 3600
    health_check_interval = 60
