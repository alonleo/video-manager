import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { listJobs, getJob, cancelJob, pollJobNotifications } from "../api/client";
import type { Job } from "../api/types";
import {
  jobLabel,
  jobResultText,
  readNotificationCursor,
  saveNotificationCursor,
  mergeTaskStack,
  isActiveTask,
} from "../lib/jobNotifications";
import Icon from "./Icon";
import TaskProgress, { taskPercent } from "./TaskProgress";
import "./TaskNotifications.css";

function TaskToast({
  job,
  paused,
  dismiss,
}: {
  job: Job;
  paused: boolean;
  dismiss: (id: number) => void;
}) {
  const failed = job.state === "error";
  const active = job.state === "queued" || job.state === "running";
  const [leaving, setLeaving] = useState(false);
  const [cancelError, setCancelError] = useState("");
  const [canceling, setCanceling] = useState(false);
  const remaining = useRef(failed ? 15000 : 6000);
  useEffect(() => { if (!active) remaining.current = failed ? 15000 : 6000; }, [active, failed]);
  const fade = useCallback(() => setLeaving(true), []);
  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => dismiss(job.id), 450);
    return () => window.clearTimeout(timer);
  }, [leaving, dismiss, job.id]);
  useEffect(() => {
    if (paused || active) return;
    const started = performance.now();
    const timer = window.setTimeout(fade, remaining.current);
    return () => {
      window.clearTimeout(timer);
      remaining.current = Math.max(
        0,
        remaining.current - (performance.now() - started),
      );
    };
  }, [paused, active, fade, job.id]);
  const title = `${jobLabel(job)}${active ? (job.state === "queued" ? "排队中" : "处理中") : failed ? "失败" : job.state === "canceled" ? "已取消" : "已完成"}`;
  return (
    <article
      className={`task-toast ${failed ? "task-toast-error" : ""} ${leaving ? "task-toast-leaving" : ""}`}
      aria-labelledby={`task-toast-title-${job.id}`}
      data-task-id={job.id}
    >
      <span className="task-toast-icon">
        <Icon name={active ? "activity" : failed ? "close" : "check"} size={19} />
      </span>
      <div className="task-toast-body">
        <span className="task-toast-eyebrow">
          任务 #{String(job.id).padStart(3, "0")}
          <span>{active ? "后台处理" : failed ? "需要关注" : "任务结束"}</span>
        </span>
        <h2 id={`task-toast-title-${job.id}`}>{title}</h2>
        <p>{active ? job.message || "等待后台处理" : job.state === "canceled" ? "任务已取消" : jobResultText(job)}</p>
        <TaskProgress state={job.state} progress={job.progress} createdAt={job.created_at} startedAt={job.started_at} finishedAt={job.finished_at} label={jobLabel(job)} compact />
        {active && <button className="btn-ghost" disabled={canceling} onClick={async () => { setCanceling(true); try { await cancelJob(job.id); } catch (error) { setCancelError(error instanceof Error ? error.message : "取消失败"); } finally { setCanceling(false); } }}>取消任务</button>}
        {cancelError && <p role="alert">{cancelError}</p>}
        <Link to={`/jobs?job=${job.id}`} onClick={fade}>
          查看任务
          <Icon name="arrow" size={14} />
        </Link>
      </div>
      <button
        type="button"
        className="task-toast-close"
        aria-label={`关闭${title}通知`}
        onClick={fade}
      >
        <Icon name="close" size={16} />
      </button>
    </article>
  );
}

export default function TaskNotifications() {
  const qc = useQueryClient();
  const [queue, setQueue] = useState<{ jobs: Job[]; overflow: number }>({
    jobs: [],
    overflow: 0,
  });
  const dismissed = useRef(new Set<number>());
  const watched = useRef<Job[]>([]);
  const activeJobs = useQuery({
    queryKey: ["floating-task-progress"],
    queryFn: async () => {
      const batches = await Promise.all([listJobs(500, "running"), listJobs(500, "queued")]);
      const current = batches.flatMap(batch => batch.items);
      const missing = watched.current.filter(job => !current.some(item => item.id === job.id));
      const resolved = await Promise.allSettled(missing.map(job => getJob(job.id)));
      const retained = resolved.flatMap((result, index) => result.status === "fulfilled" ? [result.value] : [missing[index]]);
      watched.current = [...current, ...retained.filter(isActiveTask)];
      return [...current, ...retained];
    },
    refetchInterval: 2000,
  });
  useEffect(() => {
    const ended = (activeJobs.data ?? []).filter(job => !isActiveTask(job));
    if (!ended.length) return;
    setQueue(previous => ({ ...previous, jobs: mergeTaskStack(ended, previous.jobs, dismissed.current).slice(0, 100) }));
  }, [activeJobs.data]);
  const region = useRef<HTMLElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 700px)").matches);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 700px)");
    const resize = () => {
      setCompact(media.matches);
      setExpanded(false);
    };
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, []);

  useEffect(() => {
    if (!compact || !expanded) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !region.current?.contains(event.target)) setExpanded(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setExpanded(false);
      if (region.current?.contains(document.activeElement)) launcher.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [compact, expanded]);
  const [announcement, setAnnouncement] = useState("");
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [hidden, setHidden] = useState(document.hidden);
  const dismiss = useCallback((id: number) => {
    dismissed.current.add(id);
    setQueue((previous) => ({
      ...previous,
      jobs: previous.jobs.filter((job) => job.id !== id),
    }));
  }, []);

  useEffect(() => {
    let stopped = false;
    let inFlight = false;
    let timer: number | undefined;
    let failures = 0;
    let controller: AbortController | undefined;
    let cursor = readNotificationCursor();
    const poll = async () => {
      if (stopped || inFlight) return;
      window.clearTimeout(timer);
      inFlight = true;
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller?.abort(), 15000);
      let nextDelay = document.hidden ? 5000 : 2000;
      try {
        const batch = await pollJobNotifications(cursor, controller.signal);
        if (stopped) return;
        failures = 0;
        cursor = { cursor: batch.cursor, pending: batch.pending };
        saveNotificationCursor(batch);
        if (batch.items.length) {
          setQueue((previous) => {
            const jobs = mergeTaskStack(batch.items, previous.jobs, dismissed.current);
            return {
              jobs: jobs.slice(0, 100),
              overflow: previous.overflow + Math.max(0, jobs.length - 100),
            };
          });
          setAnnouncement(
            batch.items.length === 1
              ? `任务 #${batch.items[0].id}：${jobLabel(batch.items[0])}${batch.items[0].state === "error" ? "失败" : "已完成"}。`
              : `${batch.items.length} 项任务已结束，${batch.items.filter((job) => job.state === "error").length} 项失败。可在任务通知中查看。`,
          );
          void qc.invalidateQueries({ queryKey: ["jobs"] });
          void qc.invalidateQueries({ queryKey: ["job"] });
          void qc.invalidateQueries({ queryKey: ["conversions"] });
          void qc.invalidateQueries({ queryKey: ["enhancements"] });
          if (batch.items.some(j => j.kind === "move_video")) {
            for (const key of ["videos", "video", "categories", "playback", "playback-info", "storages"]) void qc.invalidateQueries({ queryKey: [key] });
          }
          if (batch.items.some(j => j.kind === "download_url")) {
            for (const key of ["downloads", "videos", "categories", "storages"]) void qc.invalidateQueries({ queryKey: [key] });
          }
        }
        if (batch.has_more) nextDelay = 100;
      } catch {
        // Preserve the cursor on errors, so reconnecting catches every result.
        nextDelay = Math.min(15000, 2000 * 2 ** Math.min(++failures, 3));
      } finally {
        window.clearTimeout(timeout);
        inFlight = false;
        if (!stopped) timer = window.setTimeout(poll, nextDelay);
      }
    };
    const wake = () => {
      setHidden(document.hidden);
      if (!document.hidden) void poll();
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", wake);
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("online", wake);
    };
  }, [qc]);

  useEffect(() => {
    // Removing a focused close button does not always dispatch blur.
    if (focused && !region.current?.contains(document.activeElement)) setFocused(false);
    if (queue.jobs.length === 0 && !activeJobs.data?.length && queue.overflow === 0) setHovered(false);
  }, [queue, focused, activeJobs.data]);

  const jobs = mergeTaskStack(queue.jobs, activeJobs.data ?? [], dismissed.current);
  const waiting = Math.max(0, jobs.length - 3);
  const active = jobs.filter(isActiveTask);
  const failedCount = jobs.filter(job => job.state === "error").length;
  const lead = active.find(job => job.state === "running") ?? active[0] ?? jobs[0];
  const hasTasks = jobs.length > 0 || queue.overflow > 0;
  const compactLabel = active.length ? `任务 ${active.length}` : `通知 ${jobs.length + queue.overflow}`;
  const compactDetail = failedCount
    ? `${failedCount} 项失败 · 点击查看`
    : lead ? `${jobLabel(lead)}${isActiveTask(lead) ? "" : lead.state === "canceled" ? "已取消" : "已完成"}` : "任务已结束";

  useEffect(() => {
    if (!hasTasks) setExpanded(false);
  }, [hasTasks]);
  return (
    <section
      ref={region}
      className={`task-notifications${compact && expanded ? " task-notifications-expanded" : ""}`}
      aria-label="任务通知"
      onPointerEnter={(event) => { if (event.pointerType === "mouse") setHovered(true); }}
      onPointerLeave={() => setHovered(false)}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          setFocused(false);
      }}
    >
      <div
        className="sr-only"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {announcement}
      </div>
      <div
        id="floating-task-details"
        className="task-notification-list"
        hidden={compact && !expanded}
        onClick={(event) => {
          if (event.target instanceof Element && event.target.closest("a")) setExpanded(false);
        }}
      >
      {jobs.slice(0, 3).map((job) => (
        <TaskToast
          key={job.id}
          job={job}
          dismiss={dismiss}
          paused={hidden || (compact ? expanded : hovered || focused)}
        />
      ))}
      {(waiting > 0 || queue.overflow > 0) && (
        <div className="task-toast-summary">
          <span>
            {waiting > 0
              ? `还有 ${waiting} 条通知`
              : `另有 ${queue.overflow} 项任务已结束`}
          </span>
          <Link to="/jobs" onClick={() => setQueue({ jobs: [], overflow: 0 })}>
            查看全部任务
            <Icon name="arrow" size={13} />
          </Link>
          <button
            type="button"
            aria-label="关闭全部任务通知"
            onClick={() => setQueue({ jobs: [], overflow: 0 })}
          >
            <Icon name="close" size={15} />
          </button>
        </div>
      )}
      {hasTasks && (
        <Link className="task-mobile-all" to="/jobs">查看全部任务<Icon name="arrow" size={15} /></Link>
      )}
      </div>
      {hasTasks && (
        <button
          ref={launcher}
          type="button"
          className={`task-compact-toggle${failedCount ? " task-compact-error" : ""}`}
          aria-expanded={compact && expanded}
          aria-controls="floating-task-details"
          aria-label={`${expanded ? "收起" : "展开"}任务详情，${active.length} 项进行中${failedCount ? `，${failedCount} 项失败` : ""}`}
          onClick={() => setExpanded(value => !value)}
        >
          <span className="task-compact-icon"><Icon name={failedCount ? "activity" : active.length ? "activity" : "check"} size={18} /></span>
          <span className="task-compact-copy">
            <strong>{compactLabel}{lead && isActiveTask(lead) && <span>{lead.state === "queued" ? "排队中" : `${taskPercent(lead.state, lead.progress)}%`}</span>}</strong>
            <span>{compactDetail}</span>
          </span>
          <Icon name="chevron" size={14} className="task-compact-chevron" />
        </button>
      )}
    </section>
  );
}
