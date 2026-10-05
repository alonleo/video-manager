import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  subtitleApi, type SubtitleSettings, type SubtitleTaskOptions, type SubtitleTrack,
} from "../../api/subtitles";
import type { Job } from "../../api/types";

const activeKey = (videoId: number) => "frame-caption-job:" + videoId;
const running = (job?: Job) => !!job && ["queued", "running"].includes(job.state);
function readActive(videoId: number) {
  try { return Number(sessionStorage.getItem(activeKey(videoId))) || null; } catch { return null; }
}
export default function useSubtitleQuickActions(videoId: number, onReady: (track: SubtitleTrack) => void) {
  const qc = useQueryClient();
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const submitting = useRef(false);
  const [activeId, setActiveId] = useState<number | null>(() => readActive(videoId));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const config = useQuery({
    queryKey: ["subtitle-settings"],
    queryFn: () => subtitleApi<SubtitleSettings>("/subtitle-settings"),
    refetchOnWindowFocus: true,
  });
  const options = useQuery({
    queryKey: ["subtitle-options", videoId],
    queryFn: () => subtitleApi<SubtitleTaskOptions>(`/videos/${videoId}/subtitle-options`),
    refetchOnWindowFocus: true,
  });
  const jobs = useQuery({
    queryKey: ["subtitle-quick-jobs", videoId],
    queryFn: () => subtitleApi<{ items: Job[] }>(`/jobs?video_id=${videoId}&limit=30`),
    refetchInterval: activeId ? 2000 : 5000,
  });
  const active = useQuery({
    queryKey: ["subtitle-quick-job", activeId],
    queryFn: () => subtitleApi<Job>(`/jobs/${activeId}`),
    enabled: !!activeId,
    refetchInterval: q => running(q.state.data) || !q.state.data ? 1000 : false,
  });
  const pending = (jobs.data?.items || []).find(j =>
    ["subtitle_generate", "subtitle_translate"].includes(j.kind) && running(j));
  const finish = () => {
    setActiveId(null);
    try { sessionStorage.removeItem(activeKey(videoId)); } catch { /* optional persistence */ }
    void qc.invalidateQueries({ queryKey: ["subtitle-quick-jobs", videoId] });
    void qc.invalidateQueries({ queryKey: ["subtitle-jobs", videoId] });
  };
  useEffect(() => {
    const job = active.data;
    if (!activeId || !job || running(job)) return;
    let canceled = false;
    if (job.state === "done" && typeof job.result?.track_id === "number") {
      subtitleApi<SubtitleTrack>(`/subtitles/${job.result.track_id}`).then(async track => {
        if (canceled) return;
        qc.setQueryData<SubtitleTrack[]>(["subtitles", videoId], old => [
          track, ...(old || []).filter(t => t.id !== track.id),
        ]);
        onReadyRef.current(track);
        setMessage("处理完成，已切换到新字幕。");
        setError("");
        finish();
      }).catch(e => {
        if (canceled) return;
        setError("任务已完成，但新字幕读取失败：" + e.message);
        void qc.invalidateQueries({ queryKey: ["subtitles", videoId] });
        finish();
      });
    } else {
      setError(job.state === "error" ? job.error || "字幕任务失败，请重试。" : "");
      setMessage(job.state === "canceled" ? "字幕任务已取消。" : job.state === "done" ? "任务已完成。" : "");
      finish();
    }
    return () => { canceled = true; };
  }, [activeId, active.data?.state, active.data?.result?.track_id, videoId, qc]);

  const submit = useMutation({
    mutationFn: ({ kind, trackId }: { kind: "generate" | "translate"; trackId?: number }) =>
      subtitleApi<{ job_id: number }>(kind === "generate"
        ? `/videos/${videoId}/subtitles/generate-saved`
        : `/subtitles/${trackId}/translate-saved`, "POST"),
    onSuccess: data => {
      setActiveId(data.job_id);
      try { sessionStorage.setItem(activeKey(videoId), String(data.job_id)); } catch { /* optional persistence */ }
      setMessage(`任务 #${data.job_id} 已提交，使用设置中保存的参数。`);
      setError("");
      void qc.invalidateQueries({ queryKey: ["subtitle-settings"] });
      void qc.invalidateQueries({ queryKey: ["subtitle-options", videoId] });
      void qc.invalidateQueries({ queryKey: ["subtitle-quick-jobs", videoId] });
    },
    onError: e => { setError(e.message); setMessage(""); },
    onSettled: () => { submitting.current = false; },
  });
  const cancel = useMutation({
    mutationFn: (id: number) => subtitleApi(`/jobs/${id}/cancel`, "POST"),
    onSuccess: () => {
      void active.refetch();
      void jobs.refetch();
    },
    onError: e => setError(e.message),
  });
  const busy = submit.isPending || !!activeId || !!pending;
  const ready = config.isSuccess && options.isSuccess && jobs.isSuccess;
  const start = (kind: "generate" | "translate", trackId?: number) => {
    if (!ready || busy || submitting.current || (kind === "translate" && !trackId)) return;
    submitting.current = true;
    setMessage(""); setError("");
    submit.mutate({ kind, trackId });
  };
  const settings = config.data, saved = options.data;
  const asrModel = settings && (saved?.asr_provider === settings.asr_provider && saved.asr_model
    ? saved.asr_model : settings.asr_provider === "local" ? settings.asr_model : settings.asr_api.model);
  const translationModel = settings && (saved?.translation_provider === settings.translation_provider && saved.translation_model
    ? saved.translation_model : settings.translation_provider === "local" ? settings.local_translation_model
    : settings.translation_provider === "ollama" ? settings.ollama_model : settings.translation_api.model);
  return {
    start, busy, ready, job: active.data && running(active.data) ? active.data : pending,
    message, error: error || active.error?.message || config.error?.message || options.error?.message || jobs.error?.message,
    cancel, asrModel, translationModel, settings, saved,
    refresh: () => { void config.refetch(); void options.refetch(); void jobs.refetch(); if (activeId) void active.refetch(); },
  };
}
