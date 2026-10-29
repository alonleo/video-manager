import type { Job } from "../api/types";

export interface NotificationCursor {
  cursor: number | null;
  pending: number[];
}
export interface NotificationBatch extends NotificationCursor {
  cursor: number;
  items: Job[];
  has_more: boolean;
}
const KEY = "frame-archive:task-notifications:v1";
let memory: NotificationCursor = { cursor: null, pending: [] };

export function readNotificationCursor(): NotificationCursor {
  try {
    const value = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    if (
      value &&
      Number.isSafeInteger(value.cursor) &&
      value.cursor >= 0 &&
      Array.isArray(value.pending) &&
      value.pending.length <= 10000 &&
      value.pending.every(
        (id: unknown) => Number.isSafeInteger(id) && Number(id) > 0,
      )
    )
      memory = { cursor: value.cursor, pending: value.pending };
  } catch {
    /* Storage may be unavailable; retain the in-memory cursor. */
  }
  return memory;
}
export function saveNotificationCursor(batch: NotificationBatch) {
  memory = { cursor: batch.cursor, pending: batch.pending };
  try {
    sessionStorage.setItem(KEY, JSON.stringify(memory));
  } catch {
    /* Memory fallback. */
  }
}

export const JOB_KIND_LABEL: Record<string, string> = {
  scan_library: "扫描媒体库",
  dub_prepare: "准备译配稿",
  dub_render: "生成配音视频",
  dub_generate: "生成配音音轨",
  dub_model_download: "下载本地语音模型",
  dub_reference_transcribe: "提取参考音频原文",
  subtitle_generate: "生成字幕",
  subtitle_translate: "翻译字幕",
  subtitle_model_download: "下载 AI 模型",
  convert_format: "格式转换",
  move_video: "移动视频",
  enhance_video: "视频画质增强",
  enhance_preview: "增强预览",
  transcribe: "语音转录",
  summarize: "内容总结",
  download_url: "下载视频",
  transcode: "转码",
};
export function jobLabel(job: Job) {
  if (job.kind === "subtitle_generate" && job.payload?.translation) return "生成并翻译字幕";
  return JOB_KIND_LABEL[job.kind] ?? "后台任务";
}

export function jobResultText(job: Job): string {
  if (job.state === "error")
    return job.error || "任务未能完成，请查看任务详情。";
  const result = job.result ?? {};
  if (job.kind === "move_video") {
    if (typeof result.warning === "string" && result.warning) return result.warning;
    const category = result.category as { name?: string } | undefined;
    return `${typeof result.filename === "string" ? result.filename + " · " : ""}已移动到「${category?.name ?? "目标目录"}」，索引已更新。`;
  }
  if (job.kind === "enhance_preview") return "原片与增强对比已就绪，可以预览了。";
  if (job.kind === "enhance_video") return `${String(result.width)} × ${String(result.height)} · ${String(result.fps)} fps · 可以下载或加入影像库`;
  if (job.kind === "download_url") return (typeof result.filename === "string" ? result.filename + " · " : "") + "已加入影像库，可以播放了。";
  if (job.kind === "convert_format" && typeof result.filename === "string")
    return result.ts_repair === true
      ? `${result.filename} · TS 时长已修复${result.original_removed === true ? "，原片已移除" : ""}`
      : `${result.filename}${result.replaced === true ? " · 已替换原视频" : " · 可以下载了"}`;
  if (job.kind === "scan_library") {
    const parts: string[] = [];
    if (typeof result.scanned === "number")
      parts.push(`扫描 ${result.scanned} 项`);
    if (typeof result.created === "number")
      parts.push(`新增 ${result.created} 个视频`);
    if (typeof result.updated === "number" && result.updated > 0)
      parts.push(`更新 ${result.updated} 项`);
    if (parts.length) return parts.join(" · ");
  }
  if (job.kind === "dub_reference_transcribe") return result.applied === false ? "已保留手动校对的原文。" : (result.gender === "female" ? "女声" : "男声") + "「" + String(result.name ?? "参考音频") + "」原文已提取，可校对并设为默认音色。";
  if (job.kind === "dub_prepare") return "译配稿已就绪，可检查译文与男女声分配。";
  if (job.kind === "dub_generate") return "配音已生成，可在播放器底部「配音」中选择，与原声自由切换。";
  if (job.kind === "dub_render") return "配音视频已生成，可试听或下载；原片已保留。";
  if (job.kind === "subtitle_generate") return job.payload?.translation ? "原字幕与译文已生成，可在播放器中选择或双语显示。" : "字幕已生成，可在播放器中选择。";
  if (job.kind === "subtitle_translate")
    return "字幕翻译已完成，可在播放器中查看。";
  if (job.kind === "dub_model_download")
    return "本地语音模型已下载并检查完成" + (typeof result.size_bytes === "number" ? " · " + (result.size_bytes / 1024 ** 3).toFixed(2) + " GiB" : "") + "，可以开始配音。";
  if (job.kind === "subtitle_model_download")
    return "模型已下载，可以开始生成字幕。";
  return "任务已顺利完成。";
}

export function isActiveTask(job: Job) {
  return job.state === "queued" || job.state === "running";
}

// Completed updates replace stale running snapshots of the same task.
export function mergeTaskStack(updates: Job[], previous: Job[], dismissed: ReadonlySet<number>): Job[] {
  const byId = new Map<number, Job>();
  for (const job of [...updates, ...previous]) {
    if (!dismissed.has(job.id) && !byId.has(job.id)) byId.set(job.id, job);
  }
  return [...byId.values()].sort((a, b) => b.id - a.id);
}
