import { subtitleApi, type Provider, type Cue } from "./subtitles";
export const dubApi = subtitleApi;
export interface DubbingSettings {
  provider: "edge" | "api" | "reference" | "local";
  local_model: "qwen3-tts-0.6b-custom" | "qwen3-tts-0.6b-base"; local_male_voice: string; local_female_voice: string; target_language: string; male_voice: string; female_voice: string;
  fallback_gender: "male" | "female"; api: Provider; proxy: string; keep_original: boolean;
  reference_api: Provider; male_reference: string; female_reference: string;
}
export interface DubCue extends Cue {
  gender: "male" | "female"; source_text: string; uncertain: boolean; pitch_hz: number | null;
}
export interface DubProject {
  id: number; video_id: number; language: string; version: number;
  cue_count: number; cues: DubCue[]; created_at: string;
}
export interface Voice { id: string; name: string; gender: "male" | "female"; language: string }
export interface Language { id: string; name: string }

export interface ReferenceVoice { id: string; name: string; gender: "male" | "female"; text: string; version: number; duration_ms: number; created_at: string }

export interface LocalDubbingModel { id: DubbingSettings["local_model"]; name: string; kind: "builtin" | "reference"; installed: boolean; download_bytes: number; size_bytes: number | null }
export interface LocalDubbingCatalog { runtime_ready: boolean; device: string; languages: string[]; voices: Voice[]; models: LocalDubbingModel[] }
export const usesReferenceVoices = (settings: DubbingSettings) => settings.provider === "reference" || (settings.provider === "local" && settings.local_model === "qwen3-tts-0.6b-base");
