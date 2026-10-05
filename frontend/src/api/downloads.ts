import type { Job } from "./types";
import { subtitleApi as api } from "./subtitles";

export interface DownloadConfig {
  proxy: string;
  cookies_browser: "" | "chrome" | "chromium" | "firefox" | "edge" | "brave";
  cookie_file: string;
  browser_profile: string;
  browser_keyring: "" | "gnomekeyring" | "kwallet" | "kwallet5" | "kwallet6" | "basictext";
}
export interface DownloadStatus {
  installed: boolean;
  version: string | null;
  ffmpeg: boolean;
  javascript: boolean;
  default_directory: string;
}
export interface DownloadInput {
  url: string;
  quality: string;
  container: string;
  storage_id: number | null;
  path: string;
}
export const downloadStatus = () => api<DownloadStatus>("/downloads/status");
export const downloadSettings = () => api<DownloadConfig>("/downloads/settings");
export const saveDownloadSettings = (body: DownloadConfig) =>
  api<DownloadConfig>("/downloads/settings", "PUT", body);
export const startDownload = (body: DownloadInput) => api<Job>("/downloads", "POST", body);
export const retryDownload = (id: number) => api<Job>("/downloads/" + id + "/retry", "POST");
export const listDownloads = () => api<{ items: Job[]; total: number }>("/downloads");

export interface CookieCheck {
  ok: boolean; message: string; total: number; failed: number; sites: Record<string, number>;
}
export const checkDownloadCookies = (body: DownloadConfig) =>
  api<CookieCheck>("/downloads/cookies/check", "POST", body);
