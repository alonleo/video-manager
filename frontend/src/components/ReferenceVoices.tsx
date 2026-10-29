import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dubApi, usesReferenceVoices, type DubbingSettings, type ReferenceVoice } from "../api/dubbing";
import type { Job } from "../api/types";

type Gender = "male" | "female";
type ReferenceJob = Job & { reference_id: string; model: string; provider: string };
type Props = {
  settings: DubbingSettings;
  saved: DubbingSettings;
  dirty: boolean;
  onBind: (gender: Gender, id: string) => void;
};
const active = (job?: Job) => !!job && ["queued", "running"].includes(job.state);
async function check(response: Response) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "操作失败，请检查音频后重试");
  }
}
export default function ReferenceVoices(props: Props) {
  const samples = useQuery({
    queryKey: ["dubbing-references"], queryFn: () => dubApi<ReferenceVoice[]>("/dubbing/references"),
    refetchInterval: 2000,
  });
  const jobs = useQuery({
    queryKey: ["dubbing-reference-jobs"],
    queryFn: () => dubApi<{ items: ReferenceJob[] }>("/dubbing/references/jobs"), refetchInterval: 2000,
  });
  const asr = useQuery({
    queryKey: ["subtitle-settings"],
    queryFn: () => dubApi<{ asr_provider: string; asr_model: string; asr_api: { model: string } }>("/subtitle-settings"),
  });
  const [captureGender, setCaptureGender] = useState<Gender | null>(null);
  return <section className="card dubbing-settings reference-voices" id="reference-voices">
    <h2>男声与女声参考音频</h2>
    <p className="ai-hint">分别导入或录制男声、女声，音频会自动保存并在后台提取原文。每段 3–29 秒、最多 20 MB，建议 8–10 秒清晰单人语音。请使用本人或已获授权的声音。</p>
    <p className="ai-hint">{asr.data ? "当前原文识别：" + (asr.data.asr_provider === "local" ? "本地 Whisper · " + asr.data.asr_model : "API · " + asr.data.asr_api.model) + "。自动保留录音语言，不翻译原文。" : "原文识别沿用「AI 模型与服务」中的语音识别配置。"} <Link to="/settings/ai">调整识别模型</Link></p>
    {!usesReferenceVoices(props.settings) && <p className="inline-alert">当前配音模式使用内置音色。要使用下面导入的声音，请在上方选择「录音／导入参考音色」服务或本地模型，并保存设置。</p>}
    {(samples.isError || jobs.isError || asr.isError) && <p role="alert" className="inline-alert">{samples.error?.message || jobs.error?.message || asr.error?.message}<button className="btn-ghost" onClick={() => { void samples.refetch(); void jobs.refetch(); void asr.refetch(); }}>重试</button></p>}
    <div className="reference-genders">{(["male", "female"] as const).map(gender =>
      <ReferencePanel key={gender} {...props} gender={gender}
        samples={samples.data?.filter(row => row.gender === gender) ?? []}
        jobs={jobs.data?.items ?? []} captureGender={captureGender} setCaptureGender={setCaptureGender} />
    )}</div>
    <p className="ai-hint">识别在后台继续，支持取消、重试，完成后会通知；可直接使用自动原文，也可校对。本地识别不会上传音频；API 识别会将这段音频发送到已配置的识别服务。在线参考音色合成还会发送样本及文字。</p>
  </section>;
}

type PanelProps = Props & {
  gender: Gender; samples: ReferenceVoice[]; jobs: ReferenceJob[];
  captureGender: Gender | null; setCaptureGender: (gender: Gender | null) => void;
};
function ReferencePanel({ gender, samples, jobs, settings, saved, dirty, onBind, captureGender, setCaptureGender }: PanelProps) {
  const qc = useQueryClient();
  const label = gender === "male" ? "男声" : "女声";
  const field = gender === "male" ? "male_reference" : "female_reference";
  const selected = samples.find(row => row.id === settings[field]);
  const [name, setName] = useState("");
  const [recording, setRecording] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [failedFile, setFailedFile] = useState<File | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const mounted = useRef(true);
  const input = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current) clearInterval(timer.current);
      if (recorder.current?.state === "recording") recorder.current.stop();
      stream.current?.getTracks().forEach(t => t.stop());
    };
  }, []);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["dubbing-references"] });
    void qc.invalidateQueries({ queryKey: ["dubbing-reference-jobs"] });
    void qc.invalidateQueries({ queryKey: ["dubbing-voices"] });
  };
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const data = new FormData();
      data.append("file", file);
      data.append("name", name.trim() || file.name.replace(/\.[^.]+$/, "").slice(0, 80) || "我的" + label);
      data.append("gender", gender);
      const response = await fetch("/api/dubbing/references", { method: "POST", body: data });
      await check(response);
      return await response.json() as ReferenceVoice & { transcription_error?: string };
    },
    onSuccess: row => {
      refresh();
      if (!mounted.current) return;
      setName(""); setFailedFile(null);
      setNotice("已导入" + label + "「" + row.name + "」。原文提取完成后，可设为默认" + label + "。");
      if (row.transcription_error) setError(row.transcription_error + "；音频已保留，可在下方重试提取原文。");
    },
    onError: (e, file) => { if (mounted.current) { setError(e.message); setFailedFile(file); } },
  });
  function selectFile(file: File) {
    setError(""); setNotice(""); setFailedFile(null);
    if (file.size > 20 * 1024 * 1024) { setError("参考音频不能超过 20 MB"); return; }
    upload.mutate(file);
  }
  function stopRecording() {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }
  async function startRecording() {
    setError(""); setNotice("");
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setError("录音需要 HTTPS 或本机 localhost；当前仍可导入音频文件。"); return;
    }
    if (typeof MediaRecorder === "undefined") { setError("此浏览器不支持网页录音，请改用系统录音后导入。"); return; }
    setRequesting(true); setCaptureGender(gender);
    try {
      const source = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
      if (!mounted.current) { source.getTracks().forEach(t => t.stop()); return; }
      stream.current = source;
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find(t => MediaRecorder.isTypeSupported(t));
      const rec = new MediaRecorder(source, mime ? { mimeType: mime } : undefined);
      recorder.current = rec;
      const chunks: Blob[] = [];
      let failed = false;
      rec.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      rec.onerror = () => {
        failed = true;
        if (mounted.current) setError("录音中断，请检查麦克风并重试。");
        if (rec.state === "recording") rec.stop();
        source.getTracks().forEach(t => t.stop());
      };
      rec.onstop = () => {
        source.getTracks().forEach(t => t.stop());
        if (timer.current) { clearInterval(timer.current); timer.current = null; }
        if (!mounted.current) return;
        setRecording(false); setCaptureGender(null);
        if (!failed && chunks.length) {
          const type = rec.mimeType || chunks[0].type || "audio/webm";
          const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
          selectFile(new File(chunks, "我的" + label + "." + ext, { type }));
        }
      };
      setSeconds(0); setRecording(true); rec.start(250);
      const started = Date.now();
      timer.current = setInterval(() => {
        const elapsed = (Date.now() - started) / 1000;
        setSeconds(Math.floor(elapsed));
        if (elapsed >= 29) stopRecording();
      }, 200);
    } catch (e) {
      stream.current?.getTracks().forEach(t => t.stop());
      if (mounted.current) {
        setRecording(false); setCaptureGender(null);
        const reason = (e as DOMException).name;
        setError(reason === "NotAllowedError" ? "麦克风权限未开启，请允许浏览器使用麦克风，或导入已有音频。" : reason === "NotFoundError" ? "未找到麦克风，请连接麦克风或导入音频。" : "无法启动录音，请检查麦克风是否被其他程序占用。");
      }
    } finally { if (mounted.current) setRequesting(false); }
  }
  const busy = !!captureGender || upload.isPending;
  return <section className="reference-gender" aria-label={label + "配置"}>
    <h3>{label}配置</h3>
    <div className="reference-current">
      <label className="reference-text">默认{label}音频<select className="input" value={settings[field]} onChange={e => onBind(gender, e.target.value)}>
        <option value="">未选择默认{label}</option>
        {!!settings[field] && !selected && <option value={settings[field]}>正在读取已选择的音频…</option>}
        {samples.map(row => <option key={row.id} value={row.id} disabled={!row.text.trim()}>{row.name}{!row.text.trim() ? "（原文待识别）" : ""}</option>)}
      </select></label>
      <p><strong>{selected?.name || "尚未选择"}</strong> · {settings[field] !== saved[field] ? "选择待保存" : "已保存的选择"}</p>
      {selected && <audio controls preload="none" src={"/api/dubbing/references/" + selected.id + "/audio"} aria-label={"当前默认" + label + "试听"} />}
      <p className="ai-hint">此处只设置{label}对白使用的声音。</p>
    </div>
    <label className="reference-text">新增{label}音频名称<input className="input" value={name} maxLength={80} disabled={busy} onChange={e => setName(e.target.value)} placeholder={"可选，默认使用文件名"} /></label>
    <div className="dubbing-actions">
      <button type="button" className="btn-outline" disabled={busy} onClick={() => input.current?.click()}>导入{label}音频</button>
      <input ref={input} type="file" hidden aria-label={"导入" + label + "参考音频"} accept="audio/*,.mp3,.wav,.m4a,.webm,.ogg,.flac,.aac" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) selectFile(file); e.target.value = ""; }} />
      <button type="button" className={recording ? "btn-primary" : "btn-outline"} disabled={requesting || upload.isPending || (!!captureGender && captureGender !== gender)} onClick={recording ? stopRecording : startRecording}>{requesting ? "等待麦克风授权…" : recording ? "停止" + label + "录音" : "录制" + label}</button>
      {recording && <span role="status">录音中 {seconds} / 29 秒</span>}
      {upload.isPending && <span role="status">正在上传并校验{label}音频…</span>}
      {failedFile && !upload.isPending && <button type="button" className="btn-outline" onClick={() => selectFile(failedFile)}>重试上传</button>}
    </div>
    {error && <p className="inline-alert" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <div className="reference-clips">{samples.map(sample =>
      <ReferenceClip key={sample.id} sample={sample} job={jobs.find(job => job.reference_id === sample.id)}
        selected={settings[field] === sample.id} savedSelected={saved[field] === sample.id}
        previewEnabled={usesReferenceVoices(settings) && !dirty}
        onSelect={() => onBind(gender, sample.id)} refresh={refresh} />
    )}</div>
    {!samples.length && <p className="ai-hint">还没有{label}音频，导入或录音后自动提取原文。</p>}
  </section>;
}

function ReferenceClip({ sample, job, selected, savedSelected, previewEnabled, onSelect, refresh }: {
  sample: ReferenceVoice; job?: ReferenceJob; selected: boolean; savedSelected: boolean;
  previewEnabled: boolean; onSelect: () => void; refresh: () => void;
}) {
  const label = sample.gender === "male" ? "男声" : "女声";
  const [edit, setEdit] = useState<{ text: string; version: number } | null>(null);
  const [testText, setTestText] = useState("你好，这是我的参考音色。现在来试听一下配音效果。");
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const previewRequest = useRef<AbortController | null>(null);
  useEffect(() => () => { previewRequest.current?.abort(); }, []);
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);
  const save = useMutation({
    mutationFn: () => dubApi<ReferenceVoice>("/dubbing/references/" + sample.id, "PUT", edit),
    onSuccess: () => { setEdit(null); refresh(); },
  });
  const retry = useMutation({
    mutationFn: () => dubApi("/dubbing/references/" + sample.id + "/transcribe", "POST"),
    onSuccess: refresh,
  });
  const cancel = useMutation({
    mutationFn: () => dubApi("/jobs/" + job!.id + "/cancel", "POST"), onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => dubApi("/dubbing/references/" + sample.id, "DELETE"), onSuccess: refresh,
  });
  async function preview() {
    const controller = new AbortController(); previewRequest.current?.abort(); previewRequest.current = controller;
    setPreviewing(true); setPreviewError(""); setPreviewUrl("");
    try {
      const response = await fetch("/api/dubbing/reference-preview", { method: "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reference_id: sample.id, text: testText }) });
      await check(response); const blob = await response.blob();
      if (!controller.signal.aborted) setPreviewUrl(URL.createObjectURL(blob));
    } catch (e) { if (!controller.signal.aborted) setPreviewError((e as Error).message); }
    finally { if (!controller.signal.aborted) setPreviewing(false); }
  }
  const running = active(job);
  return <article className="reference-card" aria-label={label + "音频：" + sample.name}>
    <div className="dubbing-cue-meta"><strong>{sample.name}</strong><span>{label} · {(sample.duration_ms / 1000).toFixed(1)} 秒</span>{selected && <span className="chip">{savedSelected ? "默认" + label : "已选择，待保存"}</span>}</div>
    <audio controls preload="none" src={"/api/dubbing/references/" + sample.id + "/audio"} aria-label={sample.name + "原音试听"} />
    {job && <><p role="status">{job.state === "done" ? (job.result?.applied === false ? "已保留手动校对的原文" : "原文已自动提取") : job.state === "canceled" ? "识别已取消，可重试或手动填写" : "任务进度见右下角"}</p>{job.error && <p className="inline-alert" role="alert">{job.error}</p>}</>}
    <label className="reference-text">{label}音频原文 · 可校对<textarea className="input" rows={3} value={edit?.text ?? sample.text} maxLength={2000} onChange={e => setEdit({ text: e.target.value, version: edit?.version ?? sample.version })} placeholder={running ? "正在自动提取原文，可离开页面等待完成…" : "自动识别失败时，也可手动填写这段音频实际说出的原文。"} /></label>
    <div className="dubbing-actions">
      {edit && <><button className="btn-primary" disabled={save.isPending || !edit.text.trim()} onClick={() => save.mutate()}>保存原文校对</button><button className="btn-ghost" disabled={save.isPending} onClick={() => { setEdit(null); save.reset(); }}>恢复已保存原文</button></>}
      {running ? <button className="btn-outline" disabled={cancel.isPending} onClick={() => cancel.mutate()}>取消识别</button> : <button className="btn-outline" disabled={retry.isPending || !!edit} onClick={() => retry.mutate()}>{sample.text ? "重新提取原文" : "重试提取原文"}</button>}
      <button className="btn-outline" disabled={selected || !sample.text.trim() || !!edit} onClick={onSelect}>{selected ? "已选择" + label : "设为默认" + label}</button>
      <button className="btn-ghost" disabled={selected || savedSelected || running || remove.isPending || previewing} onClick={() => remove.mutate()}>删除音频</button>
    </div>
    <details className="reference-preview"><summary>用此{label}合成试听</summary>
      <label className="reference-text">合成试听文字<textarea className="input" rows={2} value={testText} maxLength={4000} onChange={e => setTestText(e.target.value)} /></label>
      {!previewEnabled && <p className="ai-hint">请先选择参考音色服务或本地参考音色模型，并保存配音设置。</p>}
      <button className="btn-outline" disabled={!previewEnabled || previewing || !sample.text.trim() || !!edit || !testText.trim()} onClick={preview}>{previewing ? "正在合成…" : "合成试听"}</button>
      {previewUrl && <audio controls autoPlay src={previewUrl} aria-label={sample.name + "合成试听"} />}
    </details>
    {(save.isError || retry.isError || cancel.isError || remove.isError || previewError) && <p className="inline-alert" role="alert">{save.error?.message || retry.error?.message || cancel.error?.message || remove.error?.message || previewError}</p>}
  </article>;
}
