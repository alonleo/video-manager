import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { coverUrl, thumbUrl } from "../api/client";
import type { VideoListItem } from "../api/types";
import { formatDuration, formatResolution, formatSize } from "../lib/format";
import Icon from "./Icon";
import RemoveVideoButton from "./RemoveVideoButton";
import { rememberLibraryPosition } from "../lib/libraryPosition";

export default function VideoCard({ video, onRemoved }: { video: VideoListItem; onRemoved?: () => void }) {
  const isPhoto = video.media_type === "photo";
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const posterUrl = video.cover_path
    ? coverUrl(video.id, `${video.created_at}:${video.cover_path}`)
    : video.thumb_path
      ? thumbUrl(video.id, `${video.created_at}:${video.thumb_path}`)
      : null;
  const location = useLocation();
  const progress =
    video.duration_ms && video.position_ms
      ? Math.min(
          100,
          Math.max(0, (video.position_ms / video.duration_ms) * 100),
        )
      : 0;
  return (
    <article className="video-card-item">
    <Link
      to={`/${isPhoto ? "photos" : "videos"}/${video.id}`}
      state={{ librarySearch: location.search, libraryEntryKey: location.key }}
      data-video-id={video.id}
      onClick={(event) => {
        if (
          event.button === 0 &&
          !event.metaKey &&
          !event.ctrlKey &&
          !event.shiftKey &&
          !event.altKey
        )
          rememberLibraryPosition(location.key, location.search, video.id);
      }}
      className={`video-card${isPhoto ? " photo-card" : ""}`}
    >
      <div className="video-poster">
        <div className="poster-fallback">
          <Icon name={isPhoto ? "photo" : "film"} size={32} />
          <span>FRAME {String(video.id).padStart(3, "0")}</span>
        </div>
        {posterUrl && failedUrl !== posterUrl && (
          <img
            key={posterUrl}
            src={posterUrl}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setFailedUrl(posterUrl)}
          />
        )}
        <span className="poster-play">
          <Icon name={isPhoto ? "search" : "play"} size={21} />
        </span>
        <span className="poster-duration">
          {isPhoto ? "照片" : formatDuration(video.duration_ms)}
        </span>
        {video.is_favorite && (
          <span className="poster-favorite" aria-label="已收藏">
            <Icon name="star" size={14} />
          </span>
        )}
        {video.status !== "indexed" && (
          <span className="poster-status">
            {(
              {
                pending: "待处理",
                probing: "读取中",
                error: "处理异常",
                unavailable: "源不可用",
              } as Record<string, string>
            )[video.status] ?? video.status}
          </span>
        )}
        {!isPhoto && progress > 1 && (
          <div
            className="watch-progress"
            aria-label={`已观看 ${Math.round(progress)}%`}
          >
            <span style={{ width: `${progress}%` }} />
          </div>
        )}
      </div>
      <div className="video-description">
        <div className="video-eyebrow">
          {isPhoto ? "照片收藏" : video.source_kind === "local"
            ? "本地影像"
            : video.source_kind === "openlist"
              ? "网盘影像"
              : "影像档案"}
          <span>
            {video.finished
              ? "已看完"
              : progress > 1
                ? "继续观看"
                : formatResolution(video.width, video.height)}
          </span>
        </div>
        <h3 title={video.title}>{video.title}</h3>
        {!!video.categories?.length && (
          <div
            className="video-directory-label"
            title={video.categories
              .map((c) => `${c.storage_name} / ${c.path || "根目录"}`)
              .join(" · ")}
          >
            <Icon name="folder" size={13} />
            <span>{video.categories.map((c) => c.name).join(" · ")}</span>
          </div>
        )}
        <div className="video-meta">
          <span>{(isPhoto ? video.container : video.video_codec)?.toUpperCase() ?? "媒体文件"}</span>
          <span>·</span>
          <span>{formatSize(video.size_bytes)}</span>
        </div>
        {video.tags.length > 0 && (
          <div className="video-tags">
            {video.tags.slice(0, 3).map((t) => (
              <span key={t.id}>{t.name}</span>
            ))}
          </div>
        )}
      </div>
      <Icon name="arrow" className="video-row-arrow" />
    </Link>
    <RemoveVideoButton video={video} compact onRemoved={onRemoved} />
    </article>
  );
}
