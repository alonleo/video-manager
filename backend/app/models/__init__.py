"""SQLAlchemy ORM 模型（SQLite）。

设计原则（计划书 §4）：引用式模型 —— videos 只存逻辑条目与元数据，
视频本体位置全部放在 video_sources，本地不拷贝视频字节。

SQLite 适配说明：
- 主键一律用 Integer（SQLite 只对 INTEGER PRIMARY KEY 自增，BigInteger 不会）
- JSONB → JSON（SQLite 用 TEXT 存，SQLAlchemy JSON 类型自动序列化）
- 时间统一存 naive UTC（SQLite 无时区概念），由 UTCDateTime 负责转换
- 中文全文检索改用 FTS5 + trigram 分词器（见 migration），替代 pg_trgm
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    TypeDecorator,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """存 naive UTC，读出时补回 tzinfo=UTC。

    理由：SQLite 不保存时区，直接塞 tz-aware datetime 会丢失偏移量，
    导致跨时区比较出错。统一在存取边界做转换。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class StorageType(enum.StrEnum):
    local = "local"
    openlist = "openlist"
    url = "url"


class JobState(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    error = "error"
    canceled = "canceled"


def _enum_col(enum_cls, name: str):
    """枚举列：SQLite 无原生 ENUM，存 VARCHAR + CHECK 约束。"""
    return SAEnum(enum_cls, name=name, native_enum=False, length=32, validate_strings=True)


# ---------------------------------------------------------------- 存储源


class Storage(Base):
    __tablename__ = "storages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    type: Mapped[StorageType] = mapped_column(_enum_col(StorageType, "storage_type"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    last_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    sources: Mapped[list[VideoSource]] = relationship(back_populates="storage")


# ---------------------------------------------------------------- 视频


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_type: Mapped[str] = mapped_column(Text, default="video", server_default="video", index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    duration_ms: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    video_codec: Mapped[str | None] = mapped_column(Text)
    video_profile: Mapped[str | None] = mapped_column(Text)
    pix_fmt: Mapped[str | None] = mapped_column(Text)
    audio_codec: Mapped[str | None] = mapped_column(Text)
    audio_channels: Mapped[int | None] = mapped_column(SmallInteger)
    container: Mapped[str | None] = mapped_column(Text)
    bitrate_bps: Mapped[int | None] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    language: Mapped[str | None] = mapped_column(Text)
    cover_path: Mapped[str | None] = mapped_column(Text)
    thumb_path: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(Text, default="indexed")
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    source_kind: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    sources: Mapped[list[VideoSource]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(
        secondary="video_tags", back_populates="videos", lazy="selectin"
    )
    history: Mapped[WatchHistory | None] = relationship(
        back_populates="video", cascade="all, delete-orphan", uselist=False
    )


class VideoSource(Base):
    __tablename__ = "video_sources"
    __table_args__ = (
        UniqueConstraint("storage_id", "uri", name="uq_video_sources_storage_uri"),
        Index("ix_video_sources_video_id", "video_id"),
        Index("ix_video_sources_quick_hash", "quick_hash"),
        Index("ix_video_sources_extractor_provider", "extractor", "provider_file_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"))
    storage_id: Mapped[int] = mapped_column(Integer, ForeignKey("storages.id"))
    source_type: Mapped[StorageType] = mapped_column(_enum_col(StorageType, "storage_type_src"))

    # local:/abs/path | openlist:/mount/path | url:https://...
    uri: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    ol_path: Mapped[str | None] = mapped_column(Text)
    provider_file_id: Mapped[str | None] = mapped_column(Text)
    origin_url: Mapped[str | None] = mapped_column(Text)
    extractor: Mapped[str | None] = mapped_column(Text)

    quick_hash: Mapped[str | None] = mapped_column(Text)
    file_hash: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    unavailable_reason: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    video: Mapped[Video] = relationship(back_populates="sources")
    storage: Mapped[Storage] = relationship(back_populates="sources")


# ---------------------------------------------------------------- 标签


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("media_type", "name", name="uq_tags_media_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text, default="video", server_default="video")
    color: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    videos: Mapped[list[Video]] = relationship(secondary="video_tags", back_populates="tags")


class CategoryScope(Base):
    """Membership of a physical directory in one library, including empty categories."""
    __tablename__ = "category_scopes"
    storage_id: Mapped[int] = mapped_column(Integer, ForeignKey("storages.id", ondelete="CASCADE"), primary_key=True)
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    media_type: Mapped[str] = mapped_column(Text, primary_key=True)


class VideoTag(Base):
    __tablename__ = "video_tags"

    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


# ---------------------------------------------------------------- 观看进度


class WatchHistory(Base):
    __tablename__ = "watch_history"

    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    position_ms: Mapped[int] = mapped_column(Integer, default=0)
    played_ms: Mapped[int] = mapped_column(Integer, default=0)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="history")


# ---------------------------------------------------------------- 转录


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("video_id", "engine", "model", name="uq_transcripts_video_engine_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"))
    language: Mapped[str | None] = mapped_column(Text)
    language_prob: Mapped[float | None] = mapped_column(Float)
    engine: Mapped[str] = mapped_column(Text)  # sensevoice | faster-whisper
    model: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    full_text: Mapped[str | None] = mapped_column(Text)
    segment_count: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcript_id", "idx", name="uq_transcript_segments_tid_idx"),
        Index("ix_transcript_segments_tid_start", "transcript_id", "start_ms"),
        # 中文检索由 FTS5 虚拟表 transcript_fts 承担，见 migration
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transcript_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transcripts.id", ondelete="CASCADE")
    )
    idx: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    speaker: Mapped[str | None] = mapped_column(Text)

    transcript: Mapped[Transcript] = relationship(back_populates="segments")


# ---------------------------------------------------------------- AI 总结


class AISummary(Base):
    __tablename__ = "ai_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"))
    transcript_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("transcripts.id", ondelete="SET NULL")
    )
    provider: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    summary_md: Mapped[str | None] = mapped_column(Text)
    highlights: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(Text, default="pending")
    map_chunks: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    keypoints: Mapped[list[AIKeypoint]] = relationship(
        back_populates="summary", cascade="all, delete-orphan"
    )


class AIKeypoint(Base):
    __tablename__ = "ai_keypoints"
    __table_args__ = (Index("ix_ai_keypoints_video_order", "video_id", "order_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"))
    summary_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_summaries.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text, default="keypoint")
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    summary: Mapped[AISummary | None] = relationship(back_populates="keypoints")


# ---------------------------------------------------------------- 任务


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_state_created", "state", "created_at"),
        Index("ix_jobs_video_kind", "video_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    video_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE")
    )
    state: Mapped[JobState] = mapped_column(
        _enum_col(JobState, "job_state"), default=JobState.queued
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    arq_job_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------- 设置


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )


__all__ = [
    "Base",
    "UTCDateTime",
    "StorageType",
    "JobState",
    "Storage",
    "Video",
    "VideoSource",
    "Tag",
    "VideoTag",
    "WatchHistory",
    "Transcript",
    "TranscriptSegment",
    "AISummary",
    "AIKeypoint",
    "Job",
    "Setting",
]


class SubtitleTrack(Base):
    """Editable display subtitles; translations retain their original timed-text snapshot."""
    __tablename__ = "subtitle_tracks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, default="und")
    engine: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    source_track_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("subtitle_tracks.id", ondelete="SET NULL"))
    source_cues: Mapped[list | None] = mapped_column(JSON)
    cues: Mapped[list] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
