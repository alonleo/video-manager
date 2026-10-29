import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { subtitleApi, subtitleUrl, type SubtitleTrack } from "../../api/subtitles";
import Icon from "../Icon";
import useSubtitleQuickActions from "./useSubtitleQuickActions";
import CaptionStyleEditor from "./CaptionStyleEditor";
import { useCaptionStyle } from "../../lib/captionStyle";

type Preferences = { enabled: boolean; language: string; bilingual: boolean; size: "small" | "normal" | "large" };
const defaults: Preferences = { enabled: true, language: "", bilingual: false, size: "normal" };
function readPreferences(): Preferences {
  try {
    const p = JSON.parse(localStorage.getItem("frame-player-captions") || "{}");
    return { enabled: p.enabled !== false, language: typeof p.language === "string" ? p.language : "",
      bilingual: p.bilingual === true, size: ["small", "normal", "large"].includes(p.size) ? p.size : "normal" };
  } catch { return defaults; }
}
export function languageLabel(language: string) {
  const names: Record<string, string> = { und: "未标注语言", zh: "中文", "zh-CN": "简体中文", "zh-TW": "繁體中文", en: "English", ja: "日本語", ko: "한국어", fr: "Français", de: "Deutsch", es: "Español" };
  return names[language] || language;
}
export function usePlayerCaptions(videoId: number) {
  const captionStyle = useCaptionStyle();
  const [preferences, setPreferences] = useState(readPreferences);
  const [selection, setSelection] = useState<{ videoId: number; id: number | null }>(() => {
    try { return { videoId, id: Number(localStorage.getItem("frame-caption-track:" + videoId)) || null }; }
    catch { return { videoId, id: null }; }
  });
  const query = useQuery({
    queryKey: ["subtitles", videoId],
    queryFn: () => subtitleApi<SubtitleTrack[]>(`/videos/${videoId}/subtitles`),
    staleTime: 0, refetchInterval: 2000,
  });
  const tracks = query.data || [];
  const track = tracks.find(t => selection.videoId === videoId && t.id === selection.id)
    || tracks.find(t => t.language === preferences.language) || tracks[0];
  useEffect(() => {
    try { localStorage.setItem("frame-player-captions", JSON.stringify(preferences)); } catch { /* optional persistence */ }
  }, [preferences]);
  const select = (t: SubtitleTrack) => {
    setSelection({ videoId, id: t.id });
    setPreferences(p => ({ ...p, language: t.language, enabled: true }));
    try { localStorage.setItem("frame-caption-track:" + videoId, String(t.id)); } catch { /* optional persistence */ }
  };
  const quick = useSubtitleQuickActions(videoId, select);
  const url = preferences.enabled && track
    ? subtitleUrl(track.id, "vtt", preferences.bilingual && track.bilingual, false, track.version) : undefined;
  return { videoId, query, tracks, track, preferences, setPreferences, select, url, quick, ...captionStyle };
}
export type PlayerCaptionsState = ReturnType<typeof usePlayerCaptions>;
export default function PlayerCaptions({ captions }: { captions: PlayerCaptionsState }) {
  const { tracks, track, preferences: p, setPreferences: update, query, quick } = captions;
  useEffect(() => { quick.refresh(); }, [captions.videoId]);
  const job = quick.job;
  return <div className="player-caption-options">
    <div className="player-menu-title"><strong>字幕</strong><span>{tracks.length} 个轨道</span></div>
    <div className="player-subtitle-actions">
      <label className="player-auto-translate">
        <input type="checkbox" checked={quick.autoTranslate} disabled={!quick.ready || quick.busy || quick.savingToggle}
          onChange={event => quick.setAutoTranslate(event.target.checked)} />
        <span>生成时自动翻译</span>
      </label>
      <div className="player-subtitle-action-buttons">
        <button type="button" disabled={!quick.ready || quick.busy || quick.savingToggle} onClick={() => quick.start("generate")}>
          <Icon name="plus" size={15} />{quick.autoTranslate ? "生成并翻译" : "生成字幕"}
        </button>
        <button type="button" disabled={!quick.ready || quick.busy || !track || track.building} onClick={() => quick.start("translate", track?.id)}>
          <Icon name="convert" size={15} />翻译字幕
        </button>
      </div>
      <p className="player-task-parameters">生成：{quick.asrModel || "读取设置…"} · {quick.saved?.language === "auto" ? "自动识别" : languageLabel(quick.saved?.language || "auto")}<br />
        翻译：{quick.translationModel || "读取设置…"} → {languageLabel(quick.saved?.target_language || "zh-CN")}</p>
      {!track && <p className="player-task-hint">先生成字幕，即可翻译成设置中的目标语言。</p>}

      {quick.busy && !job && <p className="player-task-hint" role="status">正在提交或读取字幕任务…</p>}
      {quick.message && !job && <p className="player-task-hint" role="status">{quick.message}</p>}
      {quick.error && <div className="player-task-error" role="alert">{quick.error}
        <button type="button" onClick={quick.refresh}>重新读取配置与任务</button></div>}
    </div>
    <button type="button" className="player-menu-option" role="switch" aria-checked={p.enabled} disabled={!tracks.length}
      onClick={() => update(v => ({ ...v, enabled: !v.enabled }))}>
      <span>显示字幕</span><span className={p.enabled ? "player-switch on" : "player-switch"} aria-hidden="true"><i /></span>
    </button>
    {query.isLoading && <p role="status" className="player-menu-hint">正在读取字幕…</p>}
    {query.isError && <div role="alert" className="player-menu-hint">字幕列表加载失败 <button type="button" onClick={() => query.refetch()}>重试</button></div>}
    {!query.isLoading && !query.isError && !tracks.length && <p className="player-menu-hint">暂无字幕，可直接生成或前往设置导入。</p>}
    {!!tracks.length && <>
      <div className="player-menu-label">字幕语言</div>
      <div className="player-track-list" role="group" aria-label="字幕语言">
        {tracks.map(t => <button type="button" className="player-menu-option player-track-option" key={t.id}
          aria-pressed={track?.id === t.id} onClick={() => captions.select(t)}>
          <span><strong>{languageLabel(t.language)}</strong><small>{t.label}{t.building ? ` · 后台处理中 · 已有 ${t.cue_count} 条` : ""}</small></span>
          {track?.id === t.id && <Icon name="check" size={16} />}
        </button>)}
      </div>
      {track?.bilingual && <button type="button" className="player-menu-option" role="switch" aria-checked={p.bilingual}
        onClick={() => update(v => ({ ...v, bilingual: !v.bilingual }))}><span>原文 + 译文</span><span>{p.bilingual ? "已开启" : "已关闭"}</span></button>}
    </>}
    <div className="player-size-row"><span>字幕字号</span><div role="group" aria-label="字幕字号预设">
      {[20, 26, 34].map((fontSize, i) => <button type="button" key={fontSize}
        aria-pressed={captions.style.fontSize === fontSize} onClick={() => captions.updateStyle({ fontSize })}>
        {["小", "标准", "大"][i]}</button>)}
    </div></div>
    <details className="caption-style-disclosure">
      <summary>自定义字幕样式</summary>
      <CaptionStyleEditor compact />
    </details>
    <Link className="player-menu-link" to={`/settings?video=${captions.videoId}`}>字幕与翻译设置 <Icon name="arrow" size={15} /></Link>
  </div>;
}
