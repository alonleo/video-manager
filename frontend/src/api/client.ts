import type { NotificationCursor, NotificationBatch } from "../lib/jobNotifications";
import type {
  BrowseEntry,
  DirectoryCategory,
  Health,
  Job,
  PlaybackInfo,
  Progress,
  Storage,
  Tag,
  TagSummary,
  VideoDetail,
  VideoListResponse,
} from "./types";

// 开发期走 Vite 代理（vite.config.ts），生产期同源，因此用相对路径即可。
const BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });

  if (!res.ok) {
    // FastAPI 的错误体是 {detail: "..."}；解析失败则退回状态码文本
    let msg = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) {
        msg =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
      }
    } catch {
      /* 保持 statusText */
    }
    throw new ApiError(res.status, msg);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------- 视频

export interface VideoQuery {
  media_type?: "video" | "photo";
  category?: string;
  q?: string;
  tag?: string;
  source?: string;
  status?: string;
  favorite?: boolean;
  sort?: string;
  page?: number;
  page_size?: number;
}

export function listVideos(q: VideoQuery = {}) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const qs = p.toString();
  return request<VideoListResponse>(`/videos${qs ? `?${qs}` : ""}`);
}

export const getVideo = (id: number) => request<VideoDetail>(`/videos/${id}`);

export const updateVideo = (
  id: number,
  patch: Partial<
    Pick<VideoDetail, "title" | "description" | "is_favorite" | "rating">
  >,
) =>
  request<VideoDetail>(`/videos/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deleteVideo = (id: number) =>
  request<void>(`/videos/${id}`, { method: "DELETE" });

export const addTag = (id: number, name: string) =>
  request<Tag[]>(`/videos/${id}/tags`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });

export const removeTag = (id: number, tagId: number) =>
  request<Tag[]>(`/videos/${id}/tags/${tagId}`, { method: "DELETE" });

export const listTags = (mediaType: "video" | "photo" = "video") => request<TagSummary[]>(`/tags?media_type=${mediaType}`);
export const deleteTag = (id: number, expectedCount: number, mediaType: "video" | "photo" = "video") =>
  request<void>(`/tags/${id}?expected_count=${expectedCount}&media_type=${mediaType}`, {
    method: "DELETE",
  });

export const listCategories = (mediaType: "video" | "photo" = "video") =>
  request<{ items: DirectoryCategory[]; roots: DirectoryCategory[]; warnings: string[] }>(`/categories?media_type=${mediaType}`);

export const createCategory = (body: {
  media_type: "video" | "photo";
  storage_id: number;
  path: string;
  name: string;
}) =>
  request<DirectoryCategory>("/categories", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const moveVideo = (
  id: number,
  body: { source_id: number; storage_id: number; path: string },
) =>
  request<Job>(
    `/videos/${id}/move`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );

// ---------------------------------------------------------------- 播放

export const getPlaybackInfo = (id: number, compatible = false) =>
  request<PlaybackInfo>(`/videos/${id}/playback-info?engine=wasm&compatible=${compatible}`);

export const saveProgress = (
  id: number,
  body: { position_ms: number; played_ms?: number; finished?: boolean },
) =>
  request<Progress>(`/videos/${id}/progress`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const getProgress = (id: number) =>
  request<Progress | null>(`/videos/${id}/progress`);

// ---------------------------------------------------------------- 存储源

export const listStorages = () => request<Storage[]>("/storages");

export const createStorage = (body: {
  name: string;
  type: string;
  config: Record<string, unknown>;
}) =>
  request<Storage>("/storages", { method: "POST", body: JSON.stringify(body) });

export const deleteStorage = (id: number) =>
  request<void>(`/storages/${id}`, { method: "DELETE" });

export const testStorage = (id: number) =>
  request<{ ok: boolean; message: string }>(`/storages/${id}/test`, {
    method: "POST",
  });

export const scanStorage = (id: number, path = "", probe = true) =>
  request<{ job_id: number | null; message: string }>(`/storages/${id}/scan`, {
    method: "POST",
    body: JSON.stringify({ path, probe }),
  });

export const browseStorage = (id: number, path = "") =>
  request<{ path: string; entries: BrowseEntry[] }>(
    `/storages/${id}/browse?path=${encodeURIComponent(path)}`,
    { method: "POST" },
  );

// ---------------------------------------------------------------- 任务 / 健康

export const listJobs = (limit = 50, state?: Job["state"]) =>
  request<{ items: Job[]; total: number }>(`/jobs?limit=${limit}${state ? `&state=${state}` : ""}`);

export const getHealth = () => request<Health>("/health");

// 封面 URL 直接给 <img src>，无需走 fetch
export const coverUrl = (id: number, version: string) =>
  `${BASE}/videos/${id}/cover?v=${encodeURIComponent(version)}`;
export const thumbUrl = (id: number, version: string) =>
  `${BASE}/videos/${id}/thumb?v=${encodeURIComponent(version)}`;

// Retry only on an explicit user action; polling never restarts a failed conversion.
export const retryPlayback = (id: number, compatible = false) =>
  request<PlaybackInfo>(
    `/videos/${id}/playback-retry?compatible=${compatible}`,
    { method: "POST" },
  );

// 基础操作：文件重命名、格式转换、可选字幕烧录与原片替换。
export type ContainerFormat = "mp4" | "mkv" | "mov" | "avi" | "ts" | "m2ts";
export interface ConversionOptions {
  format: ContainerFormat;
  subtitle_mode: "none" | "embed" | "burn";
  subtitle_track_ids: number[];
  source_subtitle_indices: number[];
  bilingual: boolean;
  replace_source: boolean;
}
export interface SourceSubtitle {
  index: number; label: string; codec: string; language: string;
  burn_supported: boolean; embed_formats: ContainerFormat[];
}
export const getConversionOptions = (id: number, sourceId: number) =>
  request<{ source_id: number; subtitles: SourceSubtitle[] }>(`/videos/${id}/conversion-options?source_id=${sourceId}`);
export const renameVideo = (
  id: number,
  body: {
    name: string;
    rename_file: boolean;
    source_id?: number;
  },
) =>
  request<{
    video_id: number;
    title: string;
    renamed_file: boolean;
    filename: string | null;
  }>(`/videos/${id}/rename`, { method: "POST", body: JSON.stringify(body) });
export const convertVideo = (
  id: number,
  format: ContainerFormat,
  source_id?: number,
  options?: Partial<ConversionOptions>,
) =>
  request<Job>(`/videos/${id}/convert`, {
    method: "POST",
    body: JSON.stringify({ ...options, format, source_id }),
  });
export const listVideoConversions = (id: number) =>
  request<{ items: Job[]; total: number }>(
    `/jobs?kind=convert_format&video_id=${id}&limit=5`,
  );
export type TsRepairInspection = {
  source_id: number;
  stream_duration: number | null;
  format_duration: number | null;
  difference: number | null;
  needs_repair: boolean;
  reason: string;
};
export const inspectTsRepair = (id: number, sourceId?: number) =>
  request<TsRepairInspection>(`/videos/${id}/ts-repair${sourceId ? `?source_id=${sourceId}` : ""}`);
export const repairTs = (id: number, sourceId: number | undefined, replaceSource: boolean) =>
  request<Job>(`/videos/${id}/ts-repair`, {
    method: "POST",
    body: JSON.stringify({ source_id: sourceId, replace_source: replaceSource }),
  });
export const cancelJob = (id: number) =>
  request<Job>(`/jobs/${id}/cancel`, { method: "POST" });

// A read-only cursor poll: new jobs plus previously active jobs.
export const pollJobNotifications = (cursor: NotificationCursor, signal?: AbortSignal) =>
  request<NotificationBatch>("/jobs/notifications", { method: "POST", body: JSON.stringify(cursor), signal });
export const getJob = (id: number) => request<Job>(`/jobs/${id}`);

export interface SourceDeletionPreview {
  video_id: number;
  source_id: number;
  path: string;
  filename: string;
  size_bytes: number;
  affected_sources: number;
  remaining_sources: number;
  removes_video: boolean;
  confirmation: string;
}

export interface SourceDeletionResult {
  video_id: number;
  source_id: number;
  path: string;
  video_removed: boolean;
  remaining_sources: number;
  deleted: boolean;
  warning: string | null;
}

export const previewSourceDeletion = (videoId: number, sourceId: number) =>
  request<SourceDeletionPreview>(`/videos/${videoId}/sources/${sourceId}/deletion`);

export const deleteVideoSource = (videoId: number, sourceId: number, confirmation: string) =>
  request<SourceDeletionResult>(`/videos/${videoId}/sources/${sourceId}/deletion`, {
    method: "POST",
    body: JSON.stringify({ confirmed: true, confirmation }),
  });
