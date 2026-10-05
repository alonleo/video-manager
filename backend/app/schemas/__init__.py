"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- 存储源


class StorageCreate(BaseModel):
    name: str
    type: str = Field(pattern="^(local|openlist|url)$")
    enabled: bool = True
    config: dict = Field(default_factory=dict)


class StorageUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    config: dict | None = None


class StorageOut(ORMModel):
    id: int
    name: str
    type: str
    enabled: bool
    config: dict
    last_scan_at: datetime | None = None
    created_at: datetime


class StorageTestResult(BaseModel):
    ok: bool
    message: str


# ---------------------------------------------------------------- 标签


class TagOut(ORMModel):
    media_type: str = "video"
    id: int
    name: str
    color: str | None = None


class TagSummary(TagOut):
    video_count: int = Field(ge=0)


# ---------------------------------------------------------------- 来源


class VideoSourceOut(ORMModel):
    id: int
    storage_id: int
    source_type: str
    uri: str
    local_path: str | None = None
    ol_path: str | None = None
    size_bytes: int | None = None
    is_primary: bool
    available: bool
    unavailable_reason: str | None = None


# ---------------------------------------------------------------- 视频


class DirectoryCategory(BaseModel):
    id: str
    storage_id: int
    storage_name: str
    path: str
    name: str


class VideoListItem(ORMModel):
    media_type: str = "video"
    id: int
    title: str
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    container: str | None = None
    size_bytes: int | None = None
    cover_path: str | None = None
    thumb_path: str | None = None
    status: str
    is_favorite: bool
    source_kind: str | None = None
    created_at: datetime
    tags: list[TagOut] = []
    categories: list[DirectoryCategory] = Field(default_factory=list)
    # 观看进度（联表带出，便于"继续观看"）
    position_ms: int | None = None
    finished: bool | None = None


class VideoDetail(VideoListItem):
    description: str | None = None
    fps: float | None = None
    video_profile: str | None = None
    pix_fmt: str | None = None
    audio_channels: int | None = None
    bitrate_bps: int | None = None
    language: str | None = None
    rating: int | None = None
    sources: list[VideoSourceOut] = []
    # 裁剪后的 ffprobe 原始输出，详情页可折叠展示
    meta: dict = Field(default_factory=dict)
    updated_at: datetime


class VideoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    is_favorite: bool | None = None
    rating: int | None = Field(default=None, ge=0, le=10)


class VideoListResponse(BaseModel):
    items: list[VideoListItem]
    total: int
    page: int
    page_size: int


class TagAssign(BaseModel):
    name: str


# ---------------------------------------------------------------- 播放


class PlaybackInfo(BaseModel):
    video_id: int
    mode: str  # direct | remux | transcode
    reason: str
    duration_ms: int | None = None
    stream_url: str | None = None  # direct / prepared MP4 (including compatible transcode)
    source_extension: str | None = None
    hls_url: str | None = None  # transcode
    start_position_ms: int = 0
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    # 浏览器是否需要重新拉一次 playback-info（网盘直链过期时）
    needs_refresh: bool = False
    preparation_state: str = "none"  # queued | preparing | ready | error
    preparation_progress: float = Field(default=0, ge=0, le=1)
    preparation_error: str | None = None
    compatibility_available: bool = False
    compatibility_mode: bool = False


class ProgressUpdate(BaseModel):
    position_ms: int = Field(ge=0)
    played_ms: int | None = Field(default=None, ge=0)
    finished: bool | None = None


class ProgressOut(ORMModel):
    video_id: int
    position_ms: int
    played_ms: int
    finished: bool
    updated_at: datetime


# ---------------------------------------------------------------- 任务


class JobOut(ORMModel):
    id: int
    kind: str
    video_id: int | None = None
    state: str
    progress: float
    message: str | None = None
    error: str | None = None
    result: dict | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobListResponse(BaseModel):
    items: list[JobOut]
    total: int


# ---------------------------------------------------------------- 通用


class ScanRequest(BaseModel):
    path: str = ""  # 相对 storage 根目录的子路径，空 = 全量
    probe: bool = True  # 是否同时做 ffprobe 探测


class ScanResponse(BaseModel):
    job_id: int | None = None
    message: str


class HealthOut(BaseModel):
    status: str
    database: bool
    redis: bool
    openlist: bool | None = None
    vaapi: bool | None = None
    ffmpeg: bool | None = None
    detail: dict = Field(default_factory=dict)
