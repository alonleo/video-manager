// 与后端 Pydantic schema 对应的类型定义。

export interface Tag {
  media_type: "video" | "photo";
  id: number;
  name: string;
  color: string | null;
}

export interface TagSummary extends Tag {
  video_count: number;
}

export interface VideoSource {
  id: number;
  storage_id: number;
  source_type: "local" | "openlist" | "url";
  uri: string;
  local_path: string | null;
  ol_path: string | null;
  size_bytes: number | null;
  is_primary: boolean;
  available: boolean;
  unavailable_reason: string | null;
}

export interface VideoListItem {
  media_type: "video" | "photo";
  id: number;
  title: string;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  container: string | null;
  size_bytes: number | null;
  cover_path: string | null;
  thumb_path: string | null;
  status: string;
  is_favorite: boolean;
  source_kind: string | null;
  created_at: string;
  tags: Tag[];
  categories: DirectoryCategory[];
  position_ms: number | null;
  finished: boolean | null;
}

export interface VideoDetail extends VideoListItem {
  description: string | null;
  fps: number | null;
  video_profile: string | null;
  pix_fmt: string | null;
  audio_channels: number | null;
  bitrate_bps: number | null;
  language: string | null;
  rating: number | null;
  sources: VideoSource[];
  updated_at: string;
  /** 裁剪后的 ffprobe 原始输出（format/tags/streams 摘要） */
  meta: Record<string, unknown>;
}

export interface VideoListResponse {
  items: VideoListItem[];
  total: number;
  page: number;
  page_size: number;
}

export type PlaybackMode = "wasm" | "direct" | "remux" | "transcode";

export interface PlaybackInfo {
  video_id: number;
  mode: PlaybackMode;
  reason: string;
  duration_ms: number | null;
  stream_url: string | null;
  hls_url: string | null;
  source_extension?: string | null;
  start_position_ms: number;
  container: string | null;
  video_codec: string | null;
  audio_codec: string | null;
  preparation_state?: "none" | "queued" | "preparing" | "ready" | "error";
  preparation_progress?: number;
  preparation_error?: string | null;
  compatibility_available?: boolean;
  compatibility_mode?: boolean;
  needs_refresh: boolean;
}

export interface Progress {
  video_id: number;
  position_ms: number;
  played_ms: number;
  finished: boolean;
  updated_at: string;
}

export interface Storage {
  id: number;
  name: string;
  type: "local" | "openlist" | "url";
  enabled: boolean;
  config: Record<string, unknown>;
  last_scan_at: string | null;
  created_at: string;
}

export interface Job {
  /** Optional task metadata exposed by specialized task endpoints. */
  payload?: Record<string, unknown>;
  id: number;
  kind: string;
  video_id: number | null;
  state: "queued" | "running" | "done" | "error" | "canceled";
  progress: number;
  message: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Health {
  status: string;
  database: boolean;
  redis: boolean;
  openlist: boolean | null;
  vaapi: boolean | null;
  ffmpeg: boolean | null;
  detail: Record<string, string>;
}

export interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
}

export interface DirectoryCategory {
  id: string;
  storage_id: number;
  storage_name: string;
  path: string;
  name: string;
  video_count?: number;
  available?: boolean;
}
