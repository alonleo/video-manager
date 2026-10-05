import { request } from "./client";

export interface Cue {
  start_ms: number;
  end_ms: number;
  text: string;
}
export interface SubtitleTrack {
  id: number;
  video_id: number;
  label: string;
  language: string;
  engine: string;
  model: string | null;
  source_track_id: number | null;
  bilingual: boolean;
  cue_count: number;
  version: number;
  cues?: Cue[];
}
export interface Provider {
  base_url: string;
  model: string;
  api_key?: string | null;
  has_api_key?: boolean;
}
export interface SubtitleSettings {
  model_download_proxy: string;
  asr_provider: "local" | "api";
  asr_model: string;
  asr_api: Provider;
  translation_provider: "local" | "ollama" | "api";
  local_translation_model: string;
  translation_api: Provider;
  ollama_url: string;
  ollama_model: string;
}
export interface ModelCatalog {
  asr_runtime: boolean;
  translation_runtime: boolean;
  translation: {
    id: string;
    name: string;
    size_mb: number;
    size_bytes: number | null;
    description: string;
    installed: boolean;
  }[];
  whisper: {
    id: string;
    name: string;
    size_mb: number;
    size_bytes: number | null;
    description: string;
    installed: boolean;
  }[];
  ollama: { id: string; installed: boolean }[];
  ollama_error: string | null;
}
export async function subtitleApi<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : Array.isArray(data.detail)
        ? data.detail.map((d: { msg: string }) => d.msg).join("；")
        : `请求失败 (${response.status})`,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const subtitleUrl = (
  id: number,
  format = "vtt",
  bilingual = false,
  download = false,
  version = 1,
) =>
  `/api/subtitles/${id}/export?format=${format}&bilingual=${bilingual}&download=${download}&version=${version}`;

/** Saved per-video overrides; an empty model follows the global AI setting. */
export interface SubtitleTaskOptions {
  asr_provider: "local" | "api" | null;
  asr_model: string;
  language: string;
  translation_provider: "local" | "ollama" | "api" | null;
  translation_model: string;
  source_language: string;
  target_language: string;
}
export const defaultSubtitleOptions: SubtitleTaskOptions = {
  asr_provider: null, asr_model: "", language: "auto",
  translation_provider: null, translation_model: "",
  source_language: "auto", target_language: "zh-CN",
};

export interface SubtitleTaskStatus {
  state: "idle" | "imported" | "queued" | "running" | "done" | "error" | "canceled" | "missing";
  job_id: number | null;
  progress: number;
  active_count: number;
  message: string | null;
  error: string | null;
  target_language: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}
export interface VideoSubtitleStatus {
  video_id: number;
  generation: SubtitleTaskStatus;
  translation: SubtitleTaskStatus;
  generated_count: number;
  imported_count: number;
  translated_count: number;
  original_languages: string[];
  translated_languages: string[];
}
export interface SubtitleStatusBatch {
  items: VideoSubtitleStatus[];
  missing_ids: number[];
}
export function getSubtitleStatuses(videoIds: number[], signal?: AbortSignal) {
  const params = new URLSearchParams();
  for (const id of videoIds) params.append("video_ids", String(id));
  return request<SubtitleStatusBatch>("/subtitle-status?" + params, { signal });
}
