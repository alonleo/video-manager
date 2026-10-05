import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { pollJobNotifications } from "../api/client";
import type { Job } from "../api/types";
import {
  jobLabel,
  jobResultText,
  readNotificationCursor,
  saveNotificationCursor,
} from "../lib/jobNotifications";
import Icon from "./Icon";
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
  const remaining = useRef(failed ? 15000 : 10000);
  useEffect(() => {
    if (paused) return;
    const started = performance.now();
    const timer = window.setTimeout(() => dismiss(job.id), remaining.current);
    return () => {
      window.clearTimeout(timer);
      remaining.current = Math.max(
        0,
        remaining.current - (performance.now() - started),
      );
    };
  }, [paused, dismiss, job.id]);
  const title = `${jobLabel(job)}${failed ? "失败" : "已完成"}`;
  return (
    <article
      className={`task-toast ${failed ? "task-toast-error" : ""}`}
      aria-labelledby={`task-toast-title-${job.id}`}
      data-task-id={job.id}
    >
      <span className="task-toast-icon">
        <Icon name={failed ? "close" : "check"} size={19} />
      </span>
      <div className="task-toast-body">
        <span className="task-toast-eyebrow">
          任务 #{String(job.id).padStart(3, "0")}
          <span>{failed ? "需要关注" : "处理完成"}</span>
        </span>
        <h2 id={`task-toast-title-${job.id}`}>{title}</h2>
        <p>{jobResultText(job)}</p>
        <Link to={`/jobs?job=${job.id}`} onClick={() => dismiss(job.id)}>
          查看任务
          <Icon name="arrow" size={14} />
        </Link>
      </div>
      <button
        type="button"
        className="task-toast-close"
        aria-label={`关闭${title}通知`}
        onClick={() => dismiss(job.id)}
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
  const region = useRef<HTMLElement>(null);
  const [announcement, setAnnouncement] = useState("");
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [hidden, setHidden] = useState(document.hidden);
  const dismiss = useCallback((id: number) => {
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
            const jobs = [...previous.jobs, ...batch.items];
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
    if (queue.jobs.length === 0 && queue.overflow === 0) setHovered(false);
  }, [queue, focused]);

  const waiting = Math.max(0, queue.jobs.length - 3);
  return (
    <section
      ref={region}
      className="task-notifications"
      aria-label="任务通知"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
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
      {queue.jobs.slice(0, 3).map((job) => (
        <TaskToast
          key={job.id}
          job={job}
          dismiss={dismiss}
          paused={hovered || focused || hidden}
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
    </section>
  );
}
