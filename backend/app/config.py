"""全局配置：从 .env 读取，全部可用环境变量覆盖。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 数据库 ----------
    # SQLite：零运维、单机自用足够。API 与 worker 双进程靠 WAL + busy_timeout
    # 协调并发写；数据文件放在 data/ 下，随项目走。
    database_url: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'video_manage.db'}"

    # SQLite 忙等超时（毫秒）。双进程写竞争时让事务排队而不是立刻报
    # "database is locked"。
    sqlite_busy_timeout_ms: int = 15000

    # ---------- Redis / 任务队列 ----------
    # 用 docker compose 起的项目专属实例（6399），避让系统那个带密码的 6379
    redis_url: str = "redis://127.0.0.1:6399/0"

    # 并发限制：内存仅约 15GB 可用，转录必须串行
    transcode_max_jobs: int = 2
    transcribe_max_jobs: int = 1
    download_max_jobs: int = 2

    # ---------- 路径 ----------
    data_dir: Path = PROJECT_ROOT / "data"
    cache_dir: Path = PROJECT_ROOT / "cache"
    work_dir: Path = PROJECT_ROOT / "work"
    downloads_dir: Path = PROJECT_ROOT / "downloads"

    # 转码缓存硬上限（GB）——447GB 可用空间的保护阈值
    cache_max_gb: float = 100.0
    # 转码会话空闲多久后回收（分钟）
    hls_session_ttl_min: int = 30
    playback_min_free_mb: int = Field(default=512, ge=0)
    playback_prepare_timeout_sec: int = Field(default=7200, ge=10)

    # 基础格式转换只换封装；结果单独保存在 downloads/exports。
    export_max_file_mb: int = Field(default=20480, ge=1)
    export_timeout_sec: int = Field(default=21600, ge=10)

    # ---------- ffmpeg ----------
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    vaapi_device: str = "/dev/dri/renderD128"
    # VAAPI 硬解是否可用（Phase 2 实测后调整）；False 时走软解 + 硬编
    vaapi_hwaccel_decode: bool = False

    # ---------- 支持的视频扩展名 ----------
    video_extensions: str = (
        ".mp4,.mkv,.avi,.mov,.wmv,.flv,.webm,.m4v,.mpg,.mpeg,.ts,.m2ts,.mts,.rmvb,.3gp,.vob,.ogv"
    )

    # ---------- OpenList 网盘网关 ----------
    openlist_base_url: str = "http://127.0.0.1:5244"

    # ---------- LLM（OpenAI 兼容协议） ----------
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    # ---------- ASR ----------
    asr_default_engine: str = "sensevoice"
    asr_default_model: str = "iic/SenseVoiceSmall"
    asr_whisper_model: str = "large-v3"

    # ---------- 服务 ----------
    api_host: str = "127.0.0.1"
    api_port: int = 8420

    @property
    def video_ext_set(self) -> set[str]:
        return {e.strip().lower() for e in self.video_extensions.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
