import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cancelJob } from "../api/client";
import type { Job } from "../api/types";
import { jobResultText } from "../lib/jobNotifications";

export default function MoveJobActions({ job }: { job: Job }) {
  const qc = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => cancelJob(job.id),
    onSuccess: () => {
      for (const key of ["jobs", "job"]) void qc.invalidateQueries({ queryKey: [key] });
    },
    onError: () => {
      for (const key of ["jobs", "job"]) void qc.invalidateQueries({ queryKey: [key] });
    },
  });
  if (job.kind !== "move_video") return null;
  return <div className="w-full flex flex-wrap items-center gap-3">
    {job.state === "queued" && <button className="btn-outline" disabled={cancel.isPending}
      onClick={() => cancel.mutate()}>{cancel.isPending ? "取消中…" : "取消排队"}</button>}
    {job.state === "running" && <span className="text-xs text-muted">正在后台处理，可离开此页面。</span>}
    {job.state === "done" && <p className="w-full text-xs">{jobResultText(job)}</p>}
    {job.video_id && <Link className="text-link" to={`/videos/${job.video_id}`}>查看视频</Link>}
    {cancel.isError && <p className="w-full inline-alert" role="alert">{cancel.error.message}</p>}
  </div>;
}
