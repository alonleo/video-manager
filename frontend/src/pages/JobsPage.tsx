import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getJob, listJobs } from "../api/client";
import type { Job } from "../api/types";
import Icon from "../components/Icon";
import ConversionActions from "../components/ConversionActions";
import MoveJobActions from "../components/MoveJobActions";
import DownloadActions from "../components/DownloadActions";
import "../components/downloads.css";
import TaskProgress from "../components/TaskProgress";
import { cn } from "../lib/cn";
import { formatRelative } from "../lib/format";
import { JOB_KIND_LABEL as KIND_LABEL } from "../lib/jobNotifications";

const STATE_STYLE: Record<Job["state"], string> = {
  queued: "bg-slate-500/15 text-slate-300",
  running: "bg-sky-500/15 text-sky-400",
  done: "bg-emerald-500/15 text-emerald-400",
  error: "bg-red-500/15 text-red-400",
  canceled: "bg-amber-500/15 text-amber-400",
};
const STATE_LABEL: Record<Job["state"], string> = {
  queued: "排队中",
  running: "执行中",
  done: "已完成",
  error: "失败",
  canceled: "已取消",
};
const RESULT_LABEL: Record<string, string> = {
  scanned: "已扫描",
  created: "新增影像",
  linked: "关联来源",
  updated: "已更新",
  probed: "读取信息",
  probe_failed: "读取失败",
  marked_unavailable: "来源不可用",
};

function JobRow({ job }: { job: Job }) {
  const result = Object.entries(
    ["move_video", "convert_format", "download_url", "enhance_video", "enhance_preview", "dub_prepare", "dub_render"].includes(job.kind) ? {} : (job.result ?? {}),
  ).filter(([, value]) => typeof value === "number" && value > 0);
  return (
    <article className="card job-card flex flex-wrap items-center gap-3 text-sm">
      <span className="text-xs text-muted">
        #{String(job.id).padStart(3, "0")}
      </span>
      <Icon
        name={job.kind === "scan_library" ? "folder" : "activity"}
        size={18}
      />
      <h3 className="font-medium">{KIND_LABEL[job.kind] ?? job.kind}</h3>
      {job.video_id && (
        <span className="text-xs text-muted">视频 #{job.video_id}</span>
      )}
      <span className={cn("chip", STATE_STYLE[job.state])}>
        {STATE_LABEL[job.state]}
      </span>
      {job.message && !["queued", "running"].includes(job.state) && <span className="text-xs text-muted">{job.message}</span>}
      <span className="ml-auto text-xs text-muted">
        {formatRelative(job.created_at)}
      </span>

      {["queued", "running"].includes(job.state) && (
        <div className="w-full">
          {job.message && <p className="mb-1 text-xs text-muted">{job.message}</p>}
          <TaskProgress
            state={job.state}
            progress={job.progress}
            createdAt={job.created_at}
            startedAt={job.started_at}
            label={KIND_LABEL[job.kind] ?? job.kind}
            compact
          />
        </div>
      )}

      {result.length > 0 && (
        <div className="job-results w-full">
          {result.map(([key, value]) => (
            <span key={key}>
              {RESULT_LABEL[key] ?? key} {String(value)}
            </span>
          ))}
        </div>
      )}
      <ConversionActions job={job} />
      <DownloadActions job={job} />
      <MoveJobActions job={job} />
      {["dub_prepare", "dub_render"].includes(job.kind) && job.video_id && <Link className="btn-outline" to={"/videos/" + job.video_id + "/dubbing"}>打开译配工作台</Link>}
      {job.kind === "dub_reference_transcribe" && <Link className="btn-outline" to="/settings/dubbing#reference-voices">查看参考音频原文</Link>}
      {job.kind === "dub_model_download" && <Link className="btn-outline" to="/settings/dubbing">打开本地语音模型</Link>}
      {job.kind === "dub_generate" && job.video_id && <Link className="btn-outline" to={"/videos/" + job.video_id}>在播放器中选择配音</Link>}
      {job.kind === "dub_render" && job.state === "done" && <a className="btn-outline" href={"/api/jobs/" + job.id + "/dubbing-file?download=true"}>下载配音视频</a>}
      {job.error && (
        <div className="w-full break-words text-xs text-red-400">
          {job.error}
        </div>
      )}
    </article>
  );
}

export default function JobsPage() {
  const [filter, setFilter] = useState("all");
  const [params] = useSearchParams();
  const selectedId = Number(params.get("job"));
  const validSelectedId = Number.isSafeInteger(selectedId) && selectedId > 0;
  const detailRef = useRef<HTMLDivElement>(null);
  const selected = useQuery({
    queryKey: ["job", selectedId],
    queryFn: () => getJob(selectedId),
    enabled: validSelectedId,
    refetchInterval: (q) => q.state.data && ["queued", "running"].includes(q.state.data.state) ? 1500 : false,
  });
  useEffect(() => {
    if (!selected.data || !validSelectedId) return;
    const frame = requestAnimationFrame(() => {
      detailRef.current?.scrollIntoView({
        block: "center",
        behavior: "instant",
      });
      detailRef.current?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [selected.data?.id, validSelectedId]);
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => listJobs(100),
    refetchInterval: (q) =>
      q.state.data?.items.some((j) => ["running", "queued"].includes(j.state))
        ? 1500
        : 10000,
  });
  const items = jobs.data?.items ?? [];
  const active = items.filter((j) =>
    ["running", "queued"].includes(j.state),
  ).length;
  const done = items.filter((j) => j.state === "done").length;
  const failed = items.filter((j) => j.state === "error").length;
  const visible = items.filter(
    (j) =>
      filter === "all" ||
      (filter === "active"
        ? ["running", "queued"].includes(j.state)
        : j.state === filter),
  );
  return (
    <div className="page content-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            <span /> BEHIND THE SCENES
          </p>
          <h1>每一步，都有迹可循。</h1>
          <p className="page-intro">
            扫描进度、处理结果，让后台的工作清晰可见。
          </p>
        </div>
        <button
          className="btn-outline"
          onClick={() => jobs.refetch()}
          disabled={jobs.isFetching}
        >
          <Icon name="refresh" size={16} />
          刷新
        </button>
      </div>
      <div className="jobs-stats" aria-label="最近 100 条任务统计">
        <div>
          <strong>{jobs.data ? String(active).padStart(2, "0") : "—"}</strong>
          <span>进行中的任务</span>
        </div>
        <div>
          <strong>{jobs.data ? String(done).padStart(2, "0") : "—"}</strong>
          <span>已完成的任务</span>
        </div>
        <div>
          <strong>{jobs.data ? String(failed).padStart(2, "0") : "—"}</strong>
          <span>需要关注</span>
        </div>
      </div>
      <div className="collection-heading">
        <div>
          <h2>任务记录</h2>
          <span className="collection-count">最近 {items.length} 条</span>
        </div>
        <span className="job-refresh">
          <span className="status-dot online" />
          自动更新
        </span>
      </div>
      <div className="tag-filters" aria-label="任务状态筛选">
        {[
          ["all", "全部"],
          ["active", "进行中"],
          ["done", "已完成"],
          ["error", "失败"],
        ].map(([value, label]) => (
          <button
            key={value}
            className={filter === value ? "selected" : ""}
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {jobs.isLoading && (
        <div className="loading-panel" role="status">
          正在读取任务记录…
        </div>
      )}
      {jobs.isError && (
        <div className="empty-state" role="alert">
          <h3>暂时无法读取任务</h3>
          <p>{(jobs.error as Error).message}</p>
          <button className="btn-outline" onClick={() => jobs.refetch()}>
            重新加载
          </button>
        </div>
      )}

      {validSelectedId && (
        <div
          className="job-notification-detail"
          ref={detailRef}
          tabIndex={-1}
          aria-label="通知对应任务"
        >
          <p>通知对应任务 · #{selectedId}</p>
          {selected.isLoading && <p role="status">正在读取任务…</p>}
          {selected.isError && <p role="alert">{selected.error.message}</p>}
          {selected.data && <JobRow job={selected.data} />}
        </div>
      )}
      <div className="flex flex-col gap-3">
        {!jobs.isError &&
          visible
            .filter((j) => j.id !== selected.data?.id)
            .map((j) => <JobRow key={j.id} job={j} />)}
      </div>
      {!jobs.isLoading && !jobs.isError && visible.length === 0 && (
        <div className="empty-state">
          <Icon name="check" size={32} />
          <h3>
            {filter === "active" ? "此刻，一切就绪。" : "这里还没有任务记录"}
          </h3>
          <p>
            {filter === "active"
              ? "没有等待处理的任务。可以回到影像库，挑选一段喜欢的视频。"
              : "从存储源发起扫描后，任务进度会显示在这里。"}
          </p>
        </div>
      )}
    </div>
  );
}
