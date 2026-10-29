import type { SubtitleTaskStatus, VideoSubtitleStatus } from "../../api/subtitles";
import { languageLabel } from "./PlayerCaptions";
import "./subtitle-status.css";

function TaskBadge({ kind, task }: { kind: "generation" | "translation"; task: SubtitleTaskStatus }) {
  const generated = kind === "generation";
  const label = generated ? "生成字幕" : "翻译字幕";
  const text = {
    idle: generated ? "未生成" : "未翻译",
    imported: "已导入",
    queued: "排队中",
    running: generated ? "生成中" : "翻译中",
    done: generated ? "已生成" : "已翻译",
    error: "失败",
    canceled: "已取消",
    missing: "结果已删除",
  }[task.state];
  const details = task.error || (["queued", "running"].includes(task.state) ? task.message : null);
  return <span className="subtitle-status-task" data-kind={kind}>
    <span className="subtitle-status-line">
      <span className="subtitle-status-label">{label}</span>
      <span className="subtitle-status-pill" data-state={task.state}>{text}</span>
      {task.active_count > 1 && <span className="subtitle-status-extra">共 {task.active_count} 项处理中</span>}
    </span>

    {task.target_language && ["queued", "running"].includes(task.state) &&
      <span className="subtitle-status-note">目标：{languageLabel(task.target_language)}</span>}
    {details && <span className="subtitle-status-note" title={details}>{details}</span>}
  </span>;
}
export default function SubtitleStatusBadges({ status, pending, failed }: {
  status?: VideoSubtitleStatus; pending?: boolean; failed?: boolean;
}) {
  if (!status) return <span className="subtitle-status-placeholder">
    {failed ? "字幕状态读取失败" : pending ? "正在读取字幕状态…" : "字幕状态暂不可用"}
  </span>;
  const originals = status.generated_count + status.imported_count;
  const summary = [
    originals ? `原字幕 ${originals} 轨${status.imported_count ? `（导入 ${status.imported_count} 轨）` : ""}` : "",
    status.translated_count ? `译文 ${status.translated_count} 轨 · ${status.translated_languages.map(languageLabel).join("、")}` : "",
  ].filter(Boolean).join("  /  ");
  return <span className="subtitle-status-summary" aria-label="字幕处理状态">
    <span className="subtitle-status-pair">
      <TaskBadge kind="generation" task={status.generation} />
      <TaskBadge kind="translation" task={status.translation} />
    </span>
    {summary && <span className="subtitle-status-counts">{summary}</span>}
  </span>;
}
