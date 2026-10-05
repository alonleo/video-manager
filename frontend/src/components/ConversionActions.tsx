import EnhancementResult from "./EnhancementResult";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cancelJob } from "../api/client";
import type { Job } from "../api/types";
import { formatSize } from "../lib/format";

export default function ConversionActions({ job }: { job: Job }) {
  const qc = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => cancelJob(job.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["conversions", job.video_id] });
    },
  });
  if (job.kind === "enhance_video" || job.kind === "enhance_preview") return <EnhancementResult job={job} showProgress={false} />;
  if (job.kind !== "convert_format") return null;
  return (
    <div className="conversion-actions">
      {job.state === "done" && job.result && (
        <>
          <span className="text-xs text-muted">
            {String(job.result.filename ?? "")}
            {typeof job.result.size_bytes === "number" && ` · ${formatSize(job.result.size_bytes)}`}
          </span>
          <a className="btn-outline" href={`/api/jobs/${job.id}/file`} download={String(job.result.filename ?? "video") }>
            下载文件
          </a>
          {job.result.replaced === true && <details className="conversion-backup">
            <summary>已替换原视频 · 原片备份位置</summary>
            <p>{String(job.result.backup_path ?? "")}</p>
          </details>}
        </>
      )}
      {["queued", "running"].includes(job.state) && (
        <button type="button" className="btn-outline" disabled={cancel.isPending}
          onClick={() => cancel.mutate()}>
          {cancel.isPending ? "正在取消…" : "取消转换"}
        </button>
      )}
      {cancel.isError && <p className="inline-alert" role="alert">{cancel.error.message}</p>}
    </div>
  );
}
