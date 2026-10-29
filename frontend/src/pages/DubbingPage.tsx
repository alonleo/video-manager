import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dubApi, type DubCue, type DubProject, type DubbingSettings, type Language, type Voice } from "../api/dubbing";
import type { SubtitleTrack } from "../api/subtitles";
import type { Job } from "../api/types";
import { getVideo } from "../api/client";
import Icon from "../components/Icon";
import "../components/dubbing.css";

const stamp = (ms: number) => Math.floor(ms / 60000) + ":" + String(Math.floor(ms / 1000) % 60).padStart(2, "0");
export default function DubbingPage() {
  const location = useLocation();
  const videoId = Number(useParams().id);
  const qc = useQueryClient();
  const video = useQuery({ queryKey: ["video", videoId], queryFn: () => getVideo(videoId) });
  const config = useQuery({ queryKey: ["dubbing-settings"], queryFn: () => dubApi<DubbingSettings>("/dubbing/settings") });
  const languages = useQuery({ queryKey: ["dubbing-languages"], queryFn: () => dubApi<Language[]>("/dubbing/languages") });
  const tracks = useQuery({ queryKey: ["subtitles", videoId], queryFn: () => dubApi<SubtitleTrack[]>("/videos/" + videoId + "/subtitles") });
  const projects = useQuery({ queryKey: ["dubbing-projects", videoId], queryFn: () => dubApi<DubProject[]>("/videos/" + videoId + "/dubbing"), refetchInterval: 3000 });
  const jobs = useQuery({ queryKey: ["dubbing-jobs", videoId], queryFn: () => dubApi<{ items: Job[] }>("/jobs?video_id=" + videoId + "&limit=100"), refetchInterval: 2000 });
  const [target, setTarget] = useState("");
  const [trackId, setTrackId] = useState("");
  const [mode, setMode] = useState("auto");
  const [selected, setSelected] = useState(0);
  const [draft, setDraft] = useState<DubProject | null>(null);
  const [dirty, setDirty] = useState(false);
  const [male, setMale] = useState("");
  const [female, setFemale] = useState("");
  const [original, setOriginal] = useState(true);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [previewing, setPreviewing] = useState<number | null>(null);
  const [sample, setSample] = useState("");
  const previewRequest = useRef<AbortController | null>(null);
  const initialized = useRef(false);
  useEffect(() => {
    if (config.data && !initialized.current) {
      setTarget(config.data.target_language); setOriginal(config.data.keep_original);
      initialized.current = true;
    }
  }, [config.data]);
  useEffect(() => { if (!selected && projects.data?.length) setSelected(projects.data[0].id); }, [projects.data, selected]);
  const project = useQuery({ queryKey: ["dubbing-project", selected], queryFn: () => dubApi<DubProject>("/dubbing/projects/" + selected), enabled: selected > 0 });
  useEffect(() => {
    if (project.data) { setDraft(project.data); setDirty(false); setPage(1); setMale(""); setFemale(""); }
  }, [project.data]);
  useEffect(() => () => { if (sample) URL.revokeObjectURL(sample); }, [sample]);
  useEffect(() => { previewRequest.current?.abort(); setPreviewing(null); setSample(""); }, [selected]);
  useEffect(() => () => previewRequest.current?.abort(), []);
  const voices = useQuery({ queryKey: ["dubbing-voices", draft?.language, config.data?.provider, config.data?.local_model, config.data?.proxy], queryFn: () => dubApi<Voice[]>("/dubbing/voices?language=" + draft!.language), enabled: !!draft && !!config.data && config.data.provider !== "api", retry: false, staleTime: 3600000 });
  const allJobs = jobs.data?.items.filter(j => j.kind === "dub_prepare" || j.kind === "dub_render") ?? [];
  const active = allJobs.filter(j => j.state === "queued" || j.state === "running");
  const prepare = useMutation({
    mutationFn: () => dubApi<{job_id: number}>("/videos/" + videoId + "/dubbing/prepare", "POST", { target_language: target, track_id: trackId ? Number(trackId) : null, voice_mode: mode }),
    onSuccess: () => { setError(""); void qc.invalidateQueries({ queryKey: ["dubbing-jobs", videoId] }); },
    onError: e => setError(e.message),
  });
  const seen = useRef(new Set<number>());
  useEffect(() => {
    for (const job of allJobs) {
      if (job.kind === "dub_prepare" && job.state === "done" && job.result?.project_id && !seen.current.has(job.id)) {
        seen.current.add(job.id);
        if (!dirty && job.id === prepare.data?.job_id) setSelected(Number(job.result.project_id));
      }
    }
  }, [allJobs, dirty, selected, prepare.data]);
  const save = useMutation({
    mutationFn: () => dubApi<DubProject>("/dubbing/projects/" + draft!.id, "PUT", { version: draft!.version, cues: draft!.cues }),
    onSuccess: data => { setDraft(data); setDirty(false); setError(""); qc.setQueryData(["dubbing-project", data.id], data); },
    onError: e => setError(e.message),
  });
  const render = useMutation({
    mutationFn: () => dubApi<{job_id: number}>("/dubbing/projects/" + draft!.id + "/render", "POST", { version: draft!.version, male_voice: male, female_voice: female, keep_original: original }),
    onSuccess: () => { setError(""); void qc.invalidateQueries({ queryKey: ["dubbing-jobs", videoId] }); },
    onError: e => setError(e.message),
  });
  const edit = (index: number, patch: Partial<DubCue>) => {
    setDraft(d => d ? { ...d, cues: d.cues.map((c, i) => i === index ? { ...c, ...patch } : c) } : d);
    setDirty(true);
  };
  async function preview(index: number) {
    if (!draft) return;
    previewRequest.current?.abort();
    const controller = new AbortController(); previewRequest.current = controller;
    setPreviewing(index); setError("");
    try {
      const cue = draft.cues[index];
      const response = await fetch("/api/dubbing/projects/" + draft.id + "/preview", { method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal, body: JSON.stringify({ text: cue.text, gender: cue.gender, male_voice: male, female_voice: female }) });
      if (!response.ok) { const data = await response.json(); throw new Error(typeof data.detail === "string" ? data.detail : "试听失败，请检查文字和音色"); }
      const blob = await response.blob();
      if (!controller.signal.aborted) setSample(URL.createObjectURL(blob));
    } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
    finally { if (previewRequest.current === controller) setPreviewing(null); }
  }
  const projectPages = Math.ceil((draft?.cues.length ?? 0) / 12);
  const results = allJobs.filter(j => j.kind === "dub_render" && j.state === "done");
  return <div className="page content-page dubbing-page">
    <div className="page-heading"><div>
      <p className="eyebrow"><span /> VOICE & LANGUAGE</p><h1>让对白，换一种语言。</h1>
      <p className="page-intro">{video.data?.title ?? "视频译配"} · 男女声分别配音，沿用原对白时间轴。</p>
    </div><div className="dubbing-actions"><Link className="btn-outline" to={"/videos/" + videoId} state={location.state}><Icon name="back" size={16} />返回视频</Link><Link className="btn-outline" to="/settings/dubbing"><Icon name="settings" size={16} />配音设置</Link></div></div>
    {(video.isError || config.isError || projects.isError) && <p role="alert" className="inline-alert">{video.error?.message || config.error?.message || projects.error?.message}</p>}
    <section className="card dubbing-panel"><h2><span>01</span> 准备译配稿</h2>
      <p className="ai-hint">使用现有字幕或自动识别，翻译到目标语言。男女声按音高估计，可在下方逐句修正。</p>
      <div className="dubbing-fields">
        <label>目标语言<select className="input" value={target} onChange={e => setTarget(e.target.value)}>{languages.data?.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
        <label>对白来源<select className="input" value={trackId} onChange={e => setTrackId(e.target.value)}><option value="">自动识别原视频对白</option>{tracks.data?.map(t => <option key={t.id} value={t.id}>{t.label} · {t.language}</option>)}</select></label>
        <label>声线分配<select className="input" value={mode} onChange={e => setMode(e.target.value)}><option value="auto">自动估计男女声</option><option value="male">全部使用男声</option><option value="female">全部使用女声</option></select></label>
      </div>
      <button className="btn-primary" disabled={!target || prepare.isPending || active.some(j => j.kind === "dub_prepare") || dirty || !config.data} onClick={() => prepare.mutate()}><Icon name="captions" size={17} />{prepare.isPending ? "提交中…" : "识别并准备译配稿"}</button>
      <p className="ai-hint">识别与翻译沿用 <Link className="text-link" to="/settings/ai">AI 模型与服务</Link>。后台处理，关闭页面后继续执行。</p>
    </section>
    {error && <p className="inline-alert" role="alert">{error}</p>}

    {!!projects.data?.length && <section className="card dubbing-panel"><h2><span>02</span> 检查译文与男女声</h2>
      <label className="dubbing-project-picker">译配稿<select className="input" value={selected} disabled={dirty || save.isPending} onChange={e => setSelected(Number(e.target.value))}>{projects.data.map(p => <option key={p.id} value={p.id}>#{p.id} · {languages.data?.find(l => l.id === p.language)?.name ?? p.language} · {p.cue_count} 句</option>)}</select></label>
      {project.isFetching && <p role="status">读取译配稿…</p>}
      {project.isError && <p role="alert" className="inline-alert">{project.error.message}<button className="btn-ghost" onClick={() => project.refetch()}>重新载入</button></p>}
      {draft && <><div className="dubbing-fields">{(["male", "female"] as const).map(gender => <label key={gender}>{gender === "male" ? "男声" : "女声"}音色
        {config.data?.provider !== "api" ? <select className="input" value={gender === "male" ? male : female} onChange={e => gender === "male" ? setMale(e.target.value) : setFemale(e.target.value)}><option value="">使用配音设置中的默认音色</option>{voices.data?.filter(v => v.gender === gender).map(v => <option key={v.id} value={v.id}>{v.name || v.id}</option>)}</select> : <input className="input" value={gender === "male" ? male : female} placeholder="留空使用配音设置" onChange={e => gender === "male" ? setMale(e.target.value) : setFemale(e.target.value)} />}
      </label>)}</div>
      {voices.isError && <p className="inline-alert">{voices.error.message}</p>}
      <p className="ai-hint">「待确认」表示声线估计不确定；童声、音乐和多人重叠可能误判。试听为 AI 合成语音。{config.data?.provider === "local" ? "当前使用本地模型，译文与音色样本在服务器本机合成，CPU 处理需要等待。" : "在线模式会发送译文到所选语音服务，参考音色服务还会发送对应音频样本。"}句子过长时会自动加快语速，建议精简译文。</p>
      {sample && <audio className="dubbing-sample" controls autoPlay src={sample} aria-label="配音试听" />}
      <div className="dubbing-cues">{draft.cues.slice((page - 1) * 12, page * 12).map((cue, row) => {
        const index = (page - 1) * 12 + row;
        return <article key={index} className="dubbing-cue"><div className="dubbing-cue-meta"><strong>#{index + 1}</strong><span>{stamp(cue.start_ms)} – {stamp(cue.end_ms)}</span>{cue.uncertain && <span className="chip">待确认声线</span>}</div>
          <p className="dubbing-source">{cue.source_text}</p>
          <label>配音译文<textarea className="input" rows={2} maxLength={4000} value={cue.text} disabled={save.isPending} onChange={e => edit(index, { text: e.target.value })} /></label>
          <div className="dubbing-actions"><label>使用声线<select className="input" value={cue.gender} disabled={save.isPending} onChange={e => edit(index, { gender: e.target.value as "male" | "female", uncertain: false })}><option value="male">男声</option><option value="female">女声</option></select></label><button className="btn-outline" disabled={previewing !== null || !cue.text.trim()} onClick={() => preview(index)}><Icon name="volume" size={16} />{previewing === index ? "生成试听…" : "试听此句"}</button></div>
        </article>;
      })}</div>
      {projectPages > 1 && <div className="dubbing-actions"><button className="btn-outline" disabled={page === 1} onClick={() => setPage(p => p - 1)}>上一组</button><span>{page} / {projectPages}</span><button className="btn-outline" disabled={page === projectPages} onClick={() => setPage(p => p + 1)}>下一组</button></div>}
      <div className="dubbing-actions"><button className="btn-outline" disabled={!dirty || save.isPending || draft.cues.some(c => !c.text.trim())} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : "保存译文与声线"}</button>{dirty && <span role="status">有未保存修改，保存后再生成成片。</span>}</div>
      <label className="dubbing-check"><input type="checkbox" checked={original} onChange={e => setOriginal(e.target.checked)} />附加原声音轨，保留原对白与音乐供切换</label>
      <p className="ai-hint">仅生成配音并替换音轨，画面直接复制，不重新编码。根据原编码保存为 MP4 或 MKV，原片保留；配音轨只含生成对白。</p>
      {config.data?.provider === "local" && <p className="ai-hint">本地 CPU 首次生成语音仍需要时间。每句完成即缓存，取消或重试时保留；修改译文、音色或模型后只重新生成相应语句。</p>}
      <button className="btn-primary" disabled={dirty || save.isPending || render.isPending || active.some(j => j.kind === "dub_render") || draft.cues.some(c => !c.text.trim())} onClick={() => render.mutate()}><Icon name="volume" size={17} />{render.isPending ? "提交中…" : "生成配音并替换音轨"}</button>
      </>}
    </section>}
    {!!results.length && <section className="card dubbing-panel"><h2><span>03</span> 已生成配音</h2><p className="ai-hint">生成的配音已加入当前视频。<Link className="text-link" to={"/videos/" + videoId} state={location.state}>返回播放器，在底部「配音」中选择音轨</Link>，即可替换原声播放。</p>{results.map(job => <details className="dubbing-result" key={job.id}><summary>#{job.id} · {String(job.result?.filename ?? "配音视频")} · AI 合成配音</summary>
      <Link className="btn-outline" to={"/videos/" + videoId} state={location.state}>返回播放器选择此配音</Link>
      <a className="btn-primary" href={"/api/jobs/" + job.id + "/dubbing-file?download=true"}><Icon name="download" size={16} />下载配音视频</a>
      {!!job.result?.accelerated_cues && <p className="ai-hint">{String(job.result.accelerated_cues)} 句因时间限制加快了语速，可精简译文后重新生成。</p>}
    </details>)}</section>}
  </div>;
}
