import { useSyncExternalStore } from "react";
import type { Job } from "../api/types";

type TaskState = Job["state"] | "idle" | "imported" | "missing";

interface TaskProgressProps {
  state: TaskState;
  progress: number;
  createdAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  label?: string;
  compact?: boolean;
}

const listeners = new Set<() => void>();
let clock: ReturnType<typeof setInterval> | null = null;
let currentTime = Date.now();

function subscribeClock(listener: () => void) {
  currentTime = Date.now();
  listeners.add(listener);
  if (!clock) {
    clock = setInterval(() => {
      currentTime = Date.now();
      for (const notify of listeners) notify();
    }, 1000);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size && clock) {
      clearInterval(clock);
      clock = null;
    }
  };
}

const getNow = () => currentTime;
const getServerNow = () => 0;
const subscribeStatic = () => () => {};

function safeTime(value?: string | null) {
  if (!value) return null;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}

export function taskPercent(state: TaskState, progress: number) {
  if (state === "done" || state === "imported") return 100;
  const safe = Number.isFinite(progress) ? progress : 0;
  return Math.round(Math.min(1, Math.max(0, safe)) * 100);
}

export function formatTaskElapsed(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const restSeconds = seconds % 60;
  if (minutes < 60) return `${minutes} 分 ${String(restSeconds).padStart(2, "0")} 秒`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  if (hours < 24) return `${hours} 小时 ${String(restMinutes).padStart(2, "0")} 分`;
  const days = Math.floor(hours / 24);
  return `${days} 天 ${hours % 24} 小时`;
}

export default function TaskProgress({
  state,
  progress,
  createdAt,
  startedAt,
  finishedAt,
  label = "任务",
  compact = false,
}: TaskProgressProps) {
  const active = state === "queued" || state === "running";
  const now = useSyncExternalStore(active ? subscribeClock : subscribeStatic, getNow, getServerNow);
  const percent = taskPercent(state, progress);
  const start = safeTime(startedAt) ?? safeTime(createdAt);
  const end = safeTime(finishedAt) ?? (active ? now : null);
  const elapsed = start !== null && end !== null ? formatTaskElapsed(Math.max(0, end - start)) : "—";
  const timeLabel = state === "queued" ? "已等待" : "耗时";

  return (
    <div className={`task-progress${compact ? " task-progress-compact" : ""}`} data-state={state}>
      <div className="task-progress-meta">
        <strong>{percent}%</strong>
        <span>{timeLabel} {elapsed}</span>
      </div>
      <div
        className="task-progress-track"
        role="progressbar"
        aria-label={`${label}进度`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${percent}%，${timeLabel}${elapsed}`}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
