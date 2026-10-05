import { request } from "./client";
import type { Job } from "./types";

export interface EnhanceOptions {
  source_id?: number;
  resolution: "source" | "1080" | "1440" | "2160" | "2x";
  method: "standard" | "ai";
  ai_model: "general" | "anime";
  fps: 24 | 25 | 30 | 48 | 50 | 60 | 120 | null;
  frame_method: "motion" | "duplicate";
  quality: "quality" | "bitrate";
  bitrate_mbps: number;
  encoder: "auto" | "software";
  keep_subtitles: boolean;
  preview: boolean;
  preview_start: number;
}
export interface EnhanceCapabilities {
  hardware: boolean;
  models: {
    id: "general" | "anime";
    name: string;
    available: boolean;
    size_bytes: number;
  }[];
}
export const enhancementCapabilities = () =>
  request<EnhanceCapabilities>("/enhancement/capabilities");
export const enhanceVideo = (videoId: number, options: EnhanceOptions) =>
  request<Job>(`/videos/${videoId}/enhance`, {
    method: "POST",
    body: JSON.stringify(options),
  });
export const listEnhancements = async (videoId: number) => {
  const [full, preview] = await Promise.all(
    ["enhance_video", "enhance_preview"].map((kind) =>
      request<{ items: Job[] }>(
        `/jobs?kind=${kind}&video_id=${videoId}&limit=5`,
      ),
    ),
  );
  return {
    items: [...full.items, ...preview.items]
      .sort((a, b) => b.id - a.id)
      .slice(0, 8),
  };
};
export const importEnhancement = (jobId: number) =>
  request<{ video_id: number }>(`/jobs/${jobId}/enhancement-import`, {
    method: "POST",
  });
export const enhancementFile = (
  jobId: number,
  original = false,
  download = false,
) =>
  `/api/jobs/${jobId}/enhancement-file?original=${original}&download=${download}`;
