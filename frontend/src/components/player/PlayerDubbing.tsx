import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dubApi, type DubbingSettings } from "../../api/dubbing";
import type { Job } from "../../api/types";
import Icon from "../Icon";
import { languageLabel } from "./PlayerCaptions";

export interface DubbingTrack {
  id: number; language: string; label: string; created_at: string;
  stream_url: string; project_id: number;
  source_extension?: string; container?: string; video_codec?: string; audio_codec?: string; video_copied?: boolean;
}
const active = (job: Job) => ["queued", "running"].includes(job.state);
const isDubbing = (job: Job) => ["dub_generate", "dub_render"].includes(job.kind);
const preferenceKey = (videoId: number) => "frame-dubbing-track:" + videoId;
export function usePlayerDubbing(videoId: number) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState(() => {
    try { return Number(localStorage.getItem(preferenceKey(videoId))) || 0; } catch { return 0; }
  });
  const [message, setMessage] = useState("");
  const query = useQuery({
    queryKey: ["dubbing-tracks", videoId],
    queryFn: () => dubApi<DubbingTrack[]>("/videos/" + videoId + "/dubbing/tracks"),
    refetchInterval: 5000,
  });
  const config = useQuery({ queryKey: ["dubbing-settings"], queryFn: () => dubApi<DubbingSettings>("/dubbing/settings") });
  const jobs = useQuery({
    queryKey: ["player-dubbing-jobs", videoId],
    queryFn: () => dubApi<{ items: Job[] }>("/jobs?video_id=" + videoId + "&limit=100"),
    refetchInterval: q => q.state.data?.items.some(j => isDubbing(j) && active(j)) ? 2000 : 5000,
  });
  const latest = jobs.data?.items.find(isDubbing);
  const job = jobs.data?.items.find(j => isDubbing(j) && active(j));
  const finished = jobs.data?.items.find(j => isDubbing(j) && j.state === "done")?.id;
  useEffect(() => {
    if (finished) void qc.invalidateQueries({ queryKey: ["dubbing-tracks", videoId] });
  }, [finished, videoId, qc]);
  const tracks = query.data ?? [];
  const track = tracks.find(t => t.id === selected);
  const select = (id: number) => {
    setSelected(id);
    try { localStorage.setItem(preferenceKey(videoId), String(id)); } catch { /* optional preference */ }
  };
  const submit = useMutation({
    mutationFn: (trackId?: number) => dubApi<{ job_id: number }>("/videos/" + videoId + "/dubbing/generate", "POST", { track_id: trackId ?? null }),
    onSuccess: () => {
      setMessage("配音任务已提交，完成后可在下方选择音轨。");
      void jobs.refetch();
      void qc.invalidateQueries({ queryKey: ["dubbing-projects", videoId] });
    },
  });
  const cancel = useMutation({
    mutationFn: (id: number) => dubApi("/jobs/" + id + "/cancel", "POST"),
    onSuccess: () => { setMessage("配音任务已取消。"); void jobs.refetch(); },
  });
  return {
    videoId, query, config, jobs, tracks, track, select, submit, cancel, job, latest, message,
    busy: submit.isPending || !!job,
    ready: query.isSuccess && config.isSuccess && jobs.isSuccess,
    refresh: () => { void query.refetch(); void config.refetch(); void jobs.refetch(); },
  };
}
export type PlayerDubbingState = ReturnType<typeof usePlayerDubbing>;

export default function PlayerDubbing({ dubbing, subtitleId, onSelect }: { dubbing: PlayerDubbingState; subtitleId?: number; onSelect: () => void }) {
  const location = useLocation();
  const { tracks, track, config, query, job, latest, submit, cancel } = dubbing;
  const settings = config.data;
  const model = settings?.provider === "local" ? "本地 Qwen3-TTS" : settings?.provider === "edge" ? "在线神经语音" : settings?.provider === "reference" ? settings.reference_api.model : settings?.api.model;
  const error = submit.error?.message || cancel.error?.message || config.error?.message || dubbing.jobs.error?.message;
  return <div className="player-dubbing-options">
    <div className="player-menu-title"><strong>语音配音</strong><span>{tracks.length} 个配音音轨</span></div>
    <div className="player-subtitle-actions">
      <div className="player-subtitle-action-buttons">
        <button type="button" disabled={!dubbing.ready || dubbing.busy} onClick={() => { if (!dubbing.busy) submit.mutate(subtitleId); }}><Icon name="plus" size={15} />生成配音</button>
      </div>
      <p className="player-task-parameters">{model || "读取设置…"} → {languageLabel(settings?.target_language || "zh-CN")}<br />{subtitleId ? "使用当前字幕准备对白" : "自动识别原视频对白"} · 男女声沿用配音设置</p>
      <p className="player-task-hint">只处理配音音轨，原画面直接复用。完成后选择音轨替换原声，可随时切回。</p>
      {settings?.provider === "local" && <p className="player-task-hint">本地 CPU 首次合成语音可能较慢；已完成语句会缓存，重试可复用。</p>}

      {!job && <p role="status" className="player-task-hint">{submit.isPending ? "正在提交配音任务…" : latest?.state === "done" ? "配音已生成，请在下方选择要播放的音轨。" : dubbing.message}</p>}
      {(error || (!job && latest?.state === "error")) && <div className="player-task-error" role="alert">{error || latest?.error}<button type="button" onClick={dubbing.refresh}>重新读取配置与任务</button></div>}
    </div>
    <div className="player-track-list" role="group" aria-label="配音音轨">
      <button type="button" className="player-menu-option player-track-option" aria-pressed={!track} onClick={() => { dubbing.select(0); onSelect(); }}>
        <span><strong>原声</strong><small>播放原视频声音</small></span>{!track && <Icon name="check" size={16} />}
      </button>
      {tracks.map(t => <button type="button" className="player-menu-option player-track-option" key={t.id} aria-pressed={track?.id === t.id} onClick={() => { dubbing.select(t.id); onSelect(); }}>
        <span><strong>{t.label}</strong><small>AI 配音 · #{t.id} · {new Date(t.created_at).toLocaleString()}</small></span>{track?.id === t.id && <Icon name="check" size={16} />}
      </button>)}
    </div>
    {query.isLoading && <p className="player-menu-hint" role="status">正在读取配音音轨…</p>}
    {query.isError && <p className="player-menu-hint" role="alert">{query.error.message}<button type="button" onClick={() => query.refetch()}>重试</button></p>}
    {query.isSuccess && !tracks.length && <p className="player-menu-hint">暂无配音，可使用上方按钮生成。</p>}
    <Link className="player-menu-link" to={"/videos/" + dubbing.videoId + "/dubbing"} state={location.state}>检查译文与男女声 <Icon name="arrow" size={15} /></Link>
    <Link className="player-menu-link" to="/settings/dubbing">配音设置 <Icon name="settings" size={15} /></Link>
  </div>;
}
