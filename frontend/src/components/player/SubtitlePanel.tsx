import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  subtitleApi,
  subtitleUrl,
  type Cue,
  type ModelCatalog,
  type SubtitleSettings,
  type SubtitleTrack,
  type SubtitleTaskOptions,
  defaultSubtitleOptions,
} from "../../api/subtitles";
import type { Job } from "../../api/types";
import Icon from "../Icon";

const languages = [
  ["zh-CN", "简体中文"],
  ["zh-TW", "繁體中文"],
  ["en", "English"],
  ["ja", "日本語"],
  ["ko", "한국어"],
  ["fr", "Français"],
  ["de", "Deutsch"],
  ["es", "Español"],
];
export default function SubtitlePanel({
  videoId,
}: {
  videoId: number;
}) {
  const qc = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [mode, setMode] = useState("single");
  const [target, setTarget] = useState("zh-CN");
  const [autoTranslate, setAutoTranslate] = useState(false);
  const [sourceLang, setSourceLang] = useState("auto");
  const [asrModel, setAsrModel] = useState("");
  const [translationModel, setTranslationModel] = useState("");
  const [translationSource, setTranslationSource] = useState("auto");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [activeJob, setActiveJob] = useState<number | null>(null);
  const [editor, setEditor] = useState<{
    id: number;
    label: string;
    version: number;
    cues: Cue[];
  } | null>(null);
  const [editPage, setEditPage] = useState(0);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [optionsLoaded, setOptionsLoaded] = useState(false);
  const tracks = useQuery({
    queryKey: ["subtitles", videoId],
    queryFn: () => subtitleApi<SubtitleTrack[]>(`/videos/${videoId}/subtitles`),
    refetchInterval: 2000,
  });
  const config = useQuery({
    queryKey: ["subtitle-settings"],
    queryFn: () => subtitleApi<SubtitleSettings>("/subtitle-settings"),
  });
  const taskOptions = useQuery({
    queryKey: ["subtitle-options", videoId],
    queryFn: () => subtitleApi<SubtitleTaskOptions>(`/videos/${videoId}/subtitle-options`),
  });
  useEffect(() => {
    if (!optionsLoaded && taskOptions.data && config.data) {
      const saved = taskOptions.data;
      setAsrModel(saved.asr_provider === config.data.asr_provider ? saved.asr_model : "");
      setTranslationModel(saved.translation_provider === config.data.translation_provider ? saved.translation_model : "");
      setSourceLang(saved.language);
      setTranslationSource(saved.source_language);
      setTarget(saved.target_language);
      setAutoTranslate(saved.auto_translate ?? false);
      setOptionsLoaded(true);
    }
  }, [taskOptions.data, config.data, optionsLoaded]);
  const models = useQuery({
    queryKey: ["subtitle-models"],
    queryFn: () => subtitleApi<ModelCatalog>("/subtitle-models"),
  });
  const jobs = useQuery({
    queryKey: ["subtitle-jobs", videoId],
    queryFn: () =>
      subtitleApi<{ items: Job[] }>(`/jobs?video_id=${videoId}&limit=15`),
    refetchInterval: 2000,
  });
  const track = tracks.data?.find((t) => t.id === selected);
  const settings = config.data;
  const effectiveASR =
    asrModel ||
    (settings?.asr_provider === "local"
      ? settings.asr_model
      : settings?.asr_api.model) ||
    "";
  const effectiveTranslation =
    settings?.translation_provider === "local"
      ? settings.local_translation_model
      : translationModel ||
        (settings?.translation_provider === "ollama"
          ? settings.ollama_model
          : settings?.translation_api.model) ||
        "";
  useEffect(() => {
    if (taskOptions.data) setAutoTranslate(taskOptions.data.auto_translate ?? false);
  }, [taskOptions.data?.auto_translate]);
  const currentOptions: SubtitleTaskOptions = {
    auto_translate: autoTranslate,
    asr_provider: asrModel.trim() ? settings?.asr_provider ?? null : null,
    asr_model: asrModel.trim(), language: sourceLang,
    translation_provider: translationModel.trim() && settings?.translation_provider !== "local" ? settings?.translation_provider ?? null : null,
    translation_model: settings?.translation_provider === "local" ? "" : translationModel.trim(),
    source_language: translationSource, target_language: target,
  };
  const optionsDirty = optionsLoaded && Object.entries(currentOptions).some(([key, value]) =>
    value !== (taskOptions.data || defaultSubtitleOptions)[key as keyof SubtitleTaskOptions]);
  const persistOptions = async () => {
    const saved = await subtitleApi<SubtitleTaskOptions>(`/videos/${videoId}/subtitle-options`, "PUT", currentOptions);
    qc.setQueryData(["subtitle-options", videoId], saved);
    return saved;
  };
  const saveOptions = useMutation({
    mutationFn: persistOptions,
    onSuccess: () => { setError(""); setMessage("字幕与翻译参数已保存，播放器 CC 菜单将使用这些参数。"); },
    onError: e => setError(e.message),
  });
  const recent = (jobs.data?.items ?? []).filter((j) =>
    j.kind.startsWith("subtitle_"),
  );
  const pending = recent.some((j) => ["queued", "running"].includes(j.state));
  const jobSignature = recent.map((j) => `${j.id}:${j.state}:${j.result?.track_id || ""}`).join(",");
  useEffect(() => {
    if (tracks.data && selected === null)
      setSelected(tracks.data[0]?.id ?? null);
  }, [tracks.data, selected]);
  useEffect(() => {
    qc.invalidateQueries({ queryKey: ["subtitles", videoId] });
    qc.invalidateQueries({ queryKey: ["subtitle-status"] });
    const job = recent.find((j) => j.id === activeJob);
    if (job && ["running", "done"].includes(job.state) && typeof job.result?.track_id === "number") {
      setSelected(job.result.track_id);
      if (job.state !== "done") return;
      setSelected(job.result.track_id);
      setMode("single");
      setActiveJob(null);
      setMessage("字幕处理完成，已选中新字幕。返回播放器即可选择显示。");
    }
  }, [jobSignature]);
  const changed = () => {
    qc.invalidateQueries({ queryKey: ["subtitle-status"] });
    qc.invalidateQueries({ queryKey: ["subtitles", videoId] });
    qc.invalidateQueries({ queryKey: ["subtitle-jobs", videoId] });
  };
  const perform = useMutation({
    mutationFn: async ({
      path,
      method = "POST",
      body,
    }: {
      path: string;
      method?: string;
      body?: unknown;
    }) => {
      if (path.endsWith("/generate") || path.endsWith("/translate")) await persistOptions();
      return subtitleApi<{ id?: number; job_id?: number }>(path, method, body);
    },
    onSuccess: (result, variables) => {
      setError("");
      changed();
      if (variables.method === "DELETE") {
        setSelected(null);
        setEditor(null);
        setMessage("字幕已删除");
      }
      if (result?.job_id) {
        setActiveJob(result.job_id);
        setMessage(`任务 #${result.job_id} 已加入队列`);
      } else if (result?.id && !variables.path.startsWith("/jobs/")) {
        setSelected(result.id);
        setMode("single");
        setMessage("字幕已保存");
      }
    },
    onError: (e) => setError(e.message),
  });
  const loadEditor = async () => {
    if (!track) return;
    try {
      const d = await subtitleApi<SubtitleTrack>(`/subtitles/${track.id}`);
      setEditor({
        id: d.id,
        label: d.label,
        version: d.version,
        cues: d.cues!,
      });
      setEditPage(0);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const saveEditor = async () => {
    if (!editor) return;
    try {
      await perform.mutateAsync({
        path: `/subtitles/${editor.id}`,
        method: "PUT",
        body: editor,
      });
      setEditor(null);
    } catch {
      /* visible mutation error */
    }
  };
  const editCue = (index: number, patch: Partial<Cue>) =>
    setEditor((old) =>
      old
        ? {
            ...old,
            cues: old.cues.map((c, i) =>
              i === index ? { ...c, ...patch } : c,
            ),
          }
        : old,
    );
  const importFile = async (f: File) => {
    try {
      if (f.size > 3_000_000) throw new Error("字幕文件不能超过 3 MB");
      const content = new TextDecoder("utf-8", { fatal: true }).decode(
        await f.arrayBuffer(),
      );
      perform.mutate({
        path: `/videos/${videoId}/subtitles/import`,
        body: { label: f.name, language: "und", content },
      });
    } catch (e) {
      setError(
        `导入失败：${(e as Error).message}。请使用 UTF-8 的 SRT / VTT 文件。`,
      );
    }
  };
  return (
    <section className="card subtitle-panel" aria-label="字幕工作台">
      <div className="subtitle-panel-heading">
        <div>
          <span className="tiny-label">SUBTITLE STUDIO</span>
          <h2>字幕与翻译</h2>
        </div>
        <Link className="btn-ghost" to={`/settings/ai?video=${videoId}`}>
          模型与服务 <Icon name="arrow" size={15} />
        </Link>
      </div>
      <p className="ai-hint">在这里生成、翻译或校对字幕；显示开关、语言和字号请在播放器底部菜单中调整。</p>
      {tracks.isLoading && <p role="status">正在读取字幕…</p>}
      {tracks.isError && (
        <p role="alert" className="inline-alert">
          {tracks.error.message}
          <button className="btn-ghost" onClick={() => tracks.refetch()}>
            重试
          </button>
        </p>
      )}
      <div className="subtitle-display-row">
        <label>
          字幕轨道
          <select
            className="input"
            value={selected ?? ""}
            onChange={(e) => {
              setSelected(Number(e.target.value));
              setDeleteConfirm(false);
            }}
            disabled={!tracks.data?.length}
          >
            <option value="" disabled>
              尚无字幕，导入或生成一份
            </option>
            {tracks.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label} · {t.cue_count} 条
              </option>
            ))}
          </select>
        </label>
        <label>
          导出内容
          <select
            className="input"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="single">所选字幕</option>
            <option value="bilingual" disabled={!track?.bilingual}>
              原文 + 译文
            </option>
          </select>
        </label>
      </div>
      {track?.partial && <p className="ai-hint" role="status">已显示 {track.cue_count} 条完成片段。{track.building ? "后台仍在继续处理，字幕会自动更新；可返回播放器观看。" : "任务已停止，已完成的部分字幕仍可使用。"}</p>}
      <div className="subtitle-toolbar">
        <input
          ref={file}
          hidden
          type="file"
          accept=".srt,.vtt"
          aria-label="选择字幕文件"
          onChange={(e) => {
            if (e.target.files?.[0]) void importFile(e.target.files[0]);
            e.target.value = "";
          }}
        />
        <button
          className="btn-outline"
          disabled={perform.isPending}
          onClick={() => file.current?.click()}
        >
          导入 SRT / VTT
        </button>
        {track && (
          <>
            <button className="btn-ghost" disabled={track.building} onClick={loadEditor}>
              校对字幕
            </button>
            <a
              className="btn-ghost"
              href={subtitleUrl(track.id, "srt", mode === "bilingual", true)}
            >
              导出 SRT
            </a>
            <a
              className="btn-ghost"
              href={subtitleUrl(track.id, "vtt", mode === "bilingual", true)}
            >
              导出 VTT
            </a>
            <button
              className="btn-ghost text-red-700"
              disabled={track.building}
              onClick={() => setDeleteConfirm(!deleteConfirm)}
            >
              删除
            </button>
          </>
        )}
      </div>
      {deleteConfirm && track && (
        <div className="inline-alert">
          删除「{track.label}」？已有译文会保留。
          <button
            className="btn-ghost"
            disabled={perform.isPending}
            onClick={() => {
              perform.mutate({
                path: `/subtitles/${track.id}`,
                method: "DELETE",
              });
              setDeleteConfirm(false);
            }}
          >
            确认删除
          </button>
          <button className="btn-ghost" onClick={() => setDeleteConfirm(false)}>
            返回
          </button>
        </div>
      )}
      <div className="subtitle-process-grid">
        <div>
          <h3>
            <span>01</span> 自动生成字幕
          </h3>
          <label>
            语音模型
            {settings?.asr_provider === "local" ? (
              <select
                className="input"
                value={effectiveASR}
                onChange={(e) => setAsrModel(e.target.value)}
              >
                {models.data?.whisper.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                    {m.installed ? "" : "（需下载）"}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="input"
                value={effectiveASR}
                onChange={(e) => setAsrModel(e.target.value)}
              />
            )}
          </label>
          <label>
            视频语言
            <select
              className="input"
              value={sourceLang}
              onChange={(e) => setSourceLang(e.target.value)}
            >
              <option value="auto">自动识别</option>
              {[["zh", "中文"], ...languages.slice(2)].map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <p className="ai-hint">
            {settings?.asr_provider === "api"
              ? "通过已配置语音 API 处理，将上传音频片段。"
              : "本机 Whisper 处理。缺少模型时请先到 AI 设置下载。"}
          </p>
          <label className="player-auto-translate">
            <input type="checkbox" checked={autoTranslate} disabled={!optionsLoaded || pending || perform.isPending || saveOptions.isPending}
              onChange={event => setAutoTranslate(event.target.checked)} />
            <span>生成时自动翻译</span>
          </label>
          <p className="ai-hint">开启后使用下方翻译模型与目标语言，原文和译文逐步显示。</p>
          <button
            className="btn-primary"
            disabled={
              !settings || !optionsLoaded || !effectiveASR || perform.isPending || saveOptions.isPending || pending
            }
            onClick={() =>
              perform.mutate({
                path: `/videos/${videoId}/subtitles/generate`,
                body: {
                  provider: settings!.asr_provider,
                  model: effectiveASR,
                  language: sourceLang,
                  auto_translate: autoTranslate,
                },
              })
            }
          >
            {autoTranslate ? "生成并翻译" : "生成字幕"}
          </button>
        </div>
        <div>
          <h3>
            <span>02</span> 翻译所选字幕
          </h3>
          <label>
            翻译模型
            {settings?.translation_provider === "local" ? (
              <select className="input" value={effectiveTranslation} disabled>
                <option value="nllb-600m-int8">
                  NLLB-200 600M · INT8（本地）
                </option>
              </select>
            ) : (
              <input
                className="input"
                value={effectiveTranslation}
                onChange={(e) => setTranslationModel(e.target.value)}
              />
            )}
          </label>
          {settings?.translation_provider === "local" && (
            <label>
              原文语言
              <select
                className="input"
                value={translationSource}
                onChange={(e) => setTranslationSource(e.target.value)}
              >
                <option value="auto">
                  使用字幕语言（
                  {track?.language && track.language !== "und"
                    ? track.language
                    : "未知，请手动选择"}
                  ）
                </option>
                {languages.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            目标语言
            <select
              className="input"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            >
              {languages.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <p className="ai-hint">
            {settings?.translation_provider === "api"
              ? "通过已配置翻译 API 处理，将发送所选字幕文字。"
              : settings?.translation_provider === "local"
              ? "本地轻量模型离线处理。导入字幕请先确认原文语言，译文单独保存。"
              : "本机 Ollama 处理，保留原文并生成独立译文。"}
          </p>
          <button
            className="btn-outline"
            disabled={
              !settings ||
              !track ||
              !effectiveTranslation ||
              !optionsLoaded ||
              saveOptions.isPending ||
              perform.isPending ||
              pending
            }
            onClick={() =>
              perform.mutate({
                path: `/subtitles/${track!.id}/translate`,
                body: {
                  provider: settings!.translation_provider,
                  model: effectiveTranslation,
                  target_language: target,
                  source_language: translationSource,
                },
              })
            }
          >
            翻译字幕
          </button>
        </div>
      </div>
      <div className="subtitle-options-save">
        <p className="ai-hint">{optionsDirty ? "参数已修改，请保存后在 CC 菜单使用。" : "这些参数用于当前视频的 CC 快捷生成和翻译；未单独选择模型时沿用 AI 模型与服务设置。"}</p>
        <button type="button" className="btn-primary" disabled={!optionsLoaded || !optionsDirty || saveOptions.isPending || perform.isPending}
          onClick={() => saveOptions.mutate()}>{saveOptions.isPending ? "正在保存…" : "保存字幕与翻译参数"}</button>
      </div>
      {taskOptions.isError && <p className="inline-alert" role="alert">字幕参数加载失败：{taskOptions.error.message}
        <button className="btn-ghost" onClick={() => taskOptions.refetch()}>重试</button></p>}
      {config.isError && (
        <p className="inline-alert" role="alert">
          AI 设置加载失败：{config.error.message}
        </p>
      )}
      {error && (
        <p className="inline-alert" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="ai-success" role="status">
          {message}
        </p>
      )}

      {editor && (
        <div className="subtitle-editor">
          <div className="subtitle-panel-heading">
            <h3>校对时间轴与文字</h3>
            <button className="btn-ghost" onClick={() => setEditor(null)}>
              关闭编辑
            </button>
          </div>
          <label>
            字幕名称
            <input
              className="input"
              maxLength={120}
              value={editor.label}
              onChange={(e) => setEditor({ ...editor, label: e.target.value })}
            />
          </label>
          <p className="ai-hint">
            时间单位为秒。编辑原文不会改变已生成译文；双语显示使用翻译时的原文版本。
          </p>
          {editor.cues
            .slice(editPage * 20, editPage * 20 + 20)
            .map((cue, local) => {
              const index = editPage * 20 + local;
              return (
                <div className="subtitle-cue" key={index}>
                  <span>{index + 1}</span>
                  <label>
                    开始（秒）
                    <input
                      className="input"
                      type="number"
                      min="0"
                      step="0.001"
                      value={cue.start_ms / 1000}
                      onChange={(e) =>
                        editCue(index, {
                          start_ms: Math.round(Number(e.target.value) * 1000),
                        })
                      }
                    />
                  </label>
                  <label>
                    结束（秒）
                    <input
                      className="input"
                      type="number"
                      min="0"
                      step="0.001"
                      value={cue.end_ms / 1000}
                      onChange={(e) =>
                        editCue(index, {
                          end_ms: Math.round(Number(e.target.value) * 1000),
                        })
                      }
                    />
                  </label>
                  <textarea
                    className="input"
                    aria-label={`第 ${index + 1} 条字幕`}
                    maxLength={4000}
                    value={cue.text}
                    onChange={(e) => editCue(index, { text: e.target.value })}
                  />
                  <button
                    className="btn-ghost"
                    aria-label={`删除第 ${index + 1} 条字幕`}
                    onClick={() =>
                      setEditor({
                        ...editor,
                        cues: editor.cues.filter((_, i) => i !== index),
                      })
                    }
                  >
                    ×
                  </button>
                </div>
              );
            })}
          <div className="subtitle-toolbar">
            <button
              className="btn-outline"
              disabled={editPage === 0}
              onClick={() => setEditPage((p) => p - 1)}
            >
              上一页
            </button>
            <span>
              {editPage + 1} / {Math.max(1, Math.ceil(editor.cues.length / 20))}
            </span>
            <button
              className="btn-outline"
              disabled={(editPage + 1) * 20 >= editor.cues.length}
              onClick={() => setEditPage((p) => p + 1)}
            >
              下一页
            </button>
            <button
              className="btn-ghost"
              onClick={() => {
                const end = editor.cues.at(-1)?.end_ms ?? 0;
                setEditor({
                  ...editor,
                  cues: [
                    ...editor.cues,
                    { start_ms: end, end_ms: end + 3000, text: "" },
                  ],
                });
                setEditPage(Math.floor(editor.cues.length / 20));
              }}
            >
              添加片段
            </button>
            <button
              className="btn-primary"
              disabled={
                perform.isPending ||
                !editor.cues.length ||
                editor.cues.some(
                  (c) => !c.text.trim() || c.end_ms <= c.start_ms,
                )
              }
              onClick={saveEditor}
            >
              保存校对
            </button>
          </div>
        </div>
      )}
      {track && !editor && (
        <p className="ai-hint">
          当前字幕：{track.label} · {track.cue_count} 条 · {track.language} ·
          可导出后在其他播放器使用。
        </p>
      )}
    </section>
  );
}
