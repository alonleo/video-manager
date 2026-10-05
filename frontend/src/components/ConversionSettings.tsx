import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { getConversionOptions, type ContainerFormat, type ConversionOptions } from "../api/client";
import { subtitleApi, type SubtitleTrack } from "../api/subtitles";
import type { VideoSource } from "../api/types";
import "./conversion-settings.css";

const FORMATS: { value: ContainerFormat; title: string; description: string }[] = [
  { value: "mkv", title: "MKV · 推荐", description: "适合保存多音轨与多字幕轨，支持较多字幕编码。" },
  { value: "mp4", title: "MP4", description: "适合常用设备。文字软字幕转为 mov_text，部分老播放器可能不显示。" },
  { value: "mov", title: "MOV", description: "常用于苹果生态。文字软字幕转为 mov_text，复杂字幕样式可能简化。" },
  { value: "avi", title: "AVI", description: "较旧的容器。项目文字字幕请烧录到画面；软轨仅保留兼容的原始 XSUB 字幕。" },
  { value: "ts", title: "TS", description: "适合传输流。项目文字字幕请烧录；兼容的 DVB/PGS 原始字幕可保留软轨。" },
  { value: "m2ts", title: "M2TS", description: "使用 192 字节传输流封装。项目文字字幕请烧录；这不是完整蓝光光盘制作。" },
];

export default function ConversionSettings({ videoId, source, sourceSelect, pending, error, onSubmit, onClose }: {
  videoId: number; source?: VideoSource; sourceSelect: ReactNode; pending: boolean;
  error: string | null; onSubmit: (options: ConversionOptions) => void; onClose: () => void;
}) {
  const [format, setFormat] = useState<ContainerFormat>("mkv");
  const [mode, setMode] = useState<ConversionOptions["subtitle_mode"]>("none");
  const [trackIds, setTrackIds] = useState<number[]>([]);
  const [nativeIds, setNativeIds] = useState<number[]>([]);
  const [bilingual, setBilingual] = useState(false);
  const [replace, setReplace] = useState(false);
  const tracks = useQuery({ queryKey: ["subtitles", videoId],
    queryFn: () => subtitleApi<SubtitleTrack[]>(`/videos/${videoId}/subtitles`) });
  const native = useQuery({ queryKey: ["conversion-options", videoId, source?.id],
    queryFn: () => getConversionOptions(videoId, source!.id), enabled: !!source });
  useEffect(() => { setNativeIds([]); }, [source?.id]);
  const softText = ["mkv", "mp4", "mov"].includes(format);
  const selectedBilingual = tracks.data?.some(t => trackIds.includes(t.id) && t.bilingual);
  const count = trackIds.length + nativeIds.length;
  const incompatible = mode === "embed" && ((!softText && trackIds.length > 0) || native.data?.subtitles.some(t => nativeIds.includes(t.index) && !t.embed_formats.includes(format)));
  const invalid = mode !== "none" && (!count || count > 8 || (mode === "burn" && count !== 1) || incompatible);
  const targetPath = source?.local_path?.replace(/\.[^/.]+$/, `.${format}`);
  const changeMode = (next: ConversionOptions["subtitle_mode"]) => {
    setMode(next);
    if (next === "none") { setTrackIds([]); setNativeIds([]); }
    if (next === "burn") { setTrackIds(trackIds.slice(0, 1)); setNativeIds(trackIds.length ? [] : nativeIds.filter(id => native.data?.subtitles.find(s => s.index === id)?.burn_supported).slice(0, 1)); }
  };
  const selectTrack = (id: number, isNative: boolean, checked: boolean) => {
    if (mode === "burn" && checked) {
      setTrackIds(isNative ? [] : [id]); setNativeIds(isNative ? [id] : []); return;
    }
    const setter = isNative ? setNativeIds : setTrackIds;
    setter(previous => checked ? [...previous, id] : previous.filter(i => i !== id));
  };
  return <form className="basic-action-form conversion-settings" onSubmit={event => {
    event.preventDefault();
    if (!pending && source && !invalid) onSubmit({ format, subtitle_mode: mode,
      subtitle_track_ids: trackIds, source_subtitle_indices: nativeIds,
      bilingual: !!selectedBilingual && bilingual, replace_source: replace });
  }}>
    <fieldset disabled={pending}>
      {sourceSelect}
      <label className="basic-field">目标格式<select className="input" value={format} onChange={event => setFormat(event.target.value as ContainerFormat)}>
        {FORMATS.map(item => <option key={item.value} value={item.value}>{item.title}</option>)}
      </select></label>
      <p className="conversion-help">{FORMATS.find(item => item.value === format)!.description}</p>
      <label className="basic-field">字幕处理<select className="input" value={mode} onChange={event => changeMode(event.target.value as ConversionOptions["subtitle_mode"])}>
        <option value="none">不添加字幕</option>
        <option value="embed">添加可开关的字幕轨</option>
        <option value="burn">烧录到视频底部画面</option>
      </select></label>
      {mode !== "none" && <div className="conversion-track-picker">
        <strong>选择字幕{mode === "burn" ? "（一条，可双语）" : "（最多八条）"}</strong>
        <p className="conversion-help">项目字幕包括导入、识别和翻译得到的字幕。</p>
        {!softText && mode === "embed" && <p className="conversion-help">此格式仅能保留下面列出的兼容原始字幕；项目文字字幕请选择「烧录到视频底部画面」。</p>}
        {tracks.isLoading && <p role="status">读取项目字幕…</p>}
        {tracks.isError && <p className="inline-alert" role="alert">{tracks.error.message}</p>}
        {tracks.data?.map(track => <label className="conversion-track" key={`track-${track.id}`}>
          <input type="checkbox" checked={trackIds.includes(track.id)} disabled={mode === "embed" && !softText && !trackIds.includes(track.id)}
            onChange={event => selectTrack(track.id, false, event.target.checked)} />
          <span>{track.label}<small>{track.language} · {track.cue_count} 条 · 项目字幕</small></span>
        </label>)}
        {native.isLoading && <p role="status">读取原视频字幕轨…</p>}
        {native.isError && <p className="inline-alert" role="alert">原视频轨道读取失败：{native.error.message}</p>}
        {native.data?.subtitles.map(track => {
          const compatible = mode === "burn" ? track.burn_supported : track.embed_formats.includes(format);
          return <label className="conversion-track" key={`native-${track.index}`}>
            <input type="checkbox" checked={nativeIds.includes(track.index)} disabled={!compatible && !nativeIds.includes(track.index)}
              onChange={event => selectTrack(track.index, true, event.target.checked)} />
            <span>{track.label}<small>{track.language} · {track.codec} · 原视频字幕{compatible ? "" : " · 此模式不兼容"}</small></span>
          </label>;
        })}
        {!tracks.isLoading && !native.isLoading && !tracks.data?.length && !native.data?.subtitles.length && <p className="conversion-help">尚无字幕。请先在字幕面板导入或生成字幕。</p>}
        {selectedBilingual && <label className="basic-checkbox"><input type="checkbox" checked={bilingual} onChange={event => setBilingual(event.target.checked)} />翻译字幕同时显示原文（双语）</label>}
        {incompatible && <p className="inline-alert" role="alert">所选字幕不适合当前格式，请切换烧录方式或取消不兼容的选择。</p>}
        {mode === "burn" && <p className="conversion-help">字幕会永久成为画面的一部分，不能关闭。视频重新编码为 H.264，耗时较长，保留全部音轨。</p>}
      </div>}
      <label className="conversion-replace-option"><input type="checkbox" checked={replace} onChange={event => setReplace(event.target.checked)} />
        <span>转换成功后替换原视频<small>先校验结果，再更新视频库；原文件保留在同目录下的隐藏备份文件夹中。</small></span>
      </label>
      {replace && <p className="conversion-target">替换为：<span>{targetPath}</span><small>视频记录、收藏、字幕与观看进度保留。已有其他同名目标文件时停止替换。</small></p>}
      <p className="conversion-help">{mode === "burn" ? "仅视频重新编码，音频不压缩。" : "音视频保留原编码，仅调整容器和所选字幕。"}
        {!replace && "生成独立的新文件，原视频保留。"} 编码与目标容器不兼容时会明确失败，不会自动丢弃音轨。</p>
      {error && <p className="inline-alert" role="alert">{error}</p>}
      <div className="basic-action-buttons">
        <button type="button" className="btn-outline" onClick={onClose}>取消</button>
        <button className="btn-primary" disabled={pending || !source || !!invalid}>{pending ? "正在提交…" : replace ? "转换并替换原视频" : "开始转换"}</button>
      </div>
    </fieldset>
  </form>;
}
