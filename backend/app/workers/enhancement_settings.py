"""Independent serial enhancement queue; ASR and scans keep their own workers."""
import logging

from arq.connections import RedisSettings

from app.config import settings
from app.workers.enhancement import run_enhancement

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [enhance] %(message)s")


class WorkerSettings:
    functions = [run_enhancement]
    queue_name = "arq:enhancement"
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 1
    max_tries = 1
    job_timeout = settings.export_timeout_sec + 60
    keep_result = 3600
    health_check_interval = 60
