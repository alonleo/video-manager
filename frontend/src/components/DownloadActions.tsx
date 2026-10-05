import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cancelJob } from "../api/client";
import { retryDownload } from "../api/downloads";
import type { Job } from "../api/types";
import Icon from "./Icon";

export default function DownloadActions({ job }: { job: Job }) {
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const change = useMutation({
    mutationFn: (retry: boolean) => retry ? retryDownload(job.id) : cancelJob(job.id),
    onSuccess: () => {
      setError("");
      for (const key of ["downloads", "jobs", "job"]) void qc.invalidateQueries({ queryKey: [key] });
    },
    onError: (e) => setError(e.message),
  });
  if (job.kind !== "download_url") return null;
  const videoId = job.result?.video_id;
  return <div className="download-actions">
    {["queued", "running"].includes(job.state) && <button className="btn-outline" disabled={change.isPending}
      onClick={() => change.mutate(false)}>取消下载</button>}
    {["error", "canceled"].includes(job.state) && <button className="btn-outline" disabled={change.isPending}
      onClick={() => change.mutate(true)}><Icon name="refresh" size={15} />{change.isPending ? "正在提交…" : "重新下载"}</button>}
    {job.state === "done" && typeof videoId === "number" &&
      <Link className="btn-outline" to={"/videos/" + videoId}><Icon name="play" size={15} />播放视频</Link>}
    {error && <p className="download-error" role="alert">{error}</p>}
  </div>;
}
