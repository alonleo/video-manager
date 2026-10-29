import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { convertVideo, inspectTsRepair, listVideoConversions, renameVideo, repairTs } from "../api/client";
import type { ConversionOptions } from "../api/client";
import type { Job, VideoDetail } from "../api/types";
import ConversionActions from "./ConversionActions";
import ConversionSettings from "./ConversionSettings";
import { DirectoryDialog } from "./DirectoryTools";

const STATES: Record<Job["state"], string> = {
  queued: "排队中", running: "转换中", done: "已完成", error: "失败", canceled: "已取消",
};

export default function BasicVideoActions({ video, toolbar, onReplaced }: {
  video: VideoDetail;
  toolbar: HTMLDivElement | null;
  onReplaced?: () => void;
}) {
  const isPhoto = video.media_type === "photo";
  const qc = useQueryClient();
  const sources = video.sources.filter(s => s.source_type === "local" && s.available && s.local_path);
  const [sourceId, setSourceId] = useState<number | undefined>(sources.find(s => s.is_primary)?.id ?? sources[0]?.id);
  const source = sources.find(s => s.id === sourceId) ?? sources[0];
  const [form, setForm] = useState<"rename" | "convert" | "ts-repair" | null>(null);
  const [confirmRepair, setConfirmRepair] = useState(false);
  const panelId = `video-actions-${video.id}`;
  const [name, setName] = useState(video.title);
  const [renameFile, setRenameFile] = useState(sources.length > 0);
  const [notice, setNotice] = useState("");
  const conversions = useQuery({
    enabled: !isPhoto,
    queryKey: ["conversions", video.id],
    queryFn: () => listVideoConversions(video.id),
    refetchInterval: q => q.state.data?.items.some(j => ["queued", "running"].includes(j.state)) ? 1500 : 10000,
  });
  const rename = useMutation({
    mutationFn: () => renameVideo(video.id, {
      name: name.trim(), rename_file: renameFile, source_id: source?.id,
    }),
    onSuccess: data => {
      setForm(null);
      setNotice(data.renamed_file ? `已重命名为 ${data.filename}` : "名称已更新");
      qc.invalidateQueries({ queryKey: ["video", video.id] });
      qc.invalidateQueries({ queryKey: ["videos"] });
      qc.invalidateQueries({ queryKey: ["playback", video.id] });
    },
  });
  const convert = useMutation({
    mutationFn: (options: ConversionOptions) => convertVideo(video.id, options.format, source?.id, options),
    onSuccess: (_data, options) => {
      setForm(null);
      setNotice(options.replace_source ? "转换已提交，校验成功后替换原视频并保留备份。" : "转换已提交，完成后可在下方或任务中心下载。");
      qc.invalidateQueries({ queryKey: ["conversions", video.id] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: () => qc.invalidateQueries({ queryKey: ["conversions", video.id] }),
  });
  const isTs = !!source?.local_path && /\.ts$/i.test(source.local_path);
  const tsInspection = useQuery({
    enabled: form === "ts-repair" && isTs,
    queryKey: ["ts-repair", video.id, source?.id],
    queryFn: () => inspectTsRepair(video.id, source?.id),
    staleTime: 0,
  });
  const repair = useMutation({
    mutationFn: (replaceSource: boolean) => repairTs(video.id, source?.id, replaceSource),
    onSuccess: (_data, replaceSource) => {
      setConfirmRepair(false);
      setForm(null);
      setNotice(replaceSource
        ? "TS 时长修复已提交；完成校验后会替换当前来源并移除原片。"
        : "TS 时长修复已提交；当前来源保持不变，完成后可下载新文件。");
      qc.invalidateQueries({ queryKey: ["conversions", video.id] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: () => qc.invalidateQueries({ queryKey: ["conversions", video.id] }),
  });
  const seenReplacements = useRef(new Set<number>());
  useEffect(() => {
    for (const job of conversions.data?.items ?? []) {
      if (job.state === "done" && job.result?.replaced && !seenReplacements.current.has(job.id)) {
        seenReplacements.current.add(job.id);
        onReplaced?.();
        for (const key of ["video", "videos", "playback", "categories", "conversion-options"])
          qc.invalidateQueries({ queryKey: [key] });
      }
    }
  }, [conversions.data, qc, onReplaced]);
  const busy = rename.isPending || convert.isPending || repair.isPending;
  const open = (next: "rename" | "convert" | "ts-repair") => {
    setForm(form === next ? null : next);
    setNotice("");
    rename.reset();
    convert.reset();
    repair.reset();
    if (next === "rename") {
      const filename = source?.local_path?.split("/").pop() ?? video.title;
      setName(renameFile ? filename.replace(/\.[^.]+$/, "") : video.title);
    }
  };
  const sourceSelect = sources.length > 1 ? (
    <label className="basic-field">选择来源
      <select className="input" value={source?.id ?? ""} onChange={e => {
        const selected = sources.find(s => s.id === Number(e.target.value));
        setSourceId(selected?.id);
        if (form === "rename" && renameFile && selected?.local_path) {
          setName(selected.local_path.split("/").pop()!.replace(/\.[^.]+$/, ""));
        }
      }}>
        {sources.map(s => <option value={s.id} key={s.id}>{s.local_path?.split("/").pop()}</option>)}
      </select>
    </label>
  ) : null;
  const badName = !name.trim() || /[<>:"/\\|?*\x00-\x1f\x7f]/.test(name) || name.trim().endsWith(".");
  const hasActive = conversions.data?.items.some(j => ["queued", "running"].includes(j.state));
  return (
    <>
      {toolbar && createPortal(<>
        <button type="button" className="btn-outline" aria-expanded={form === "rename"}
          aria-haspopup="dialog" aria-controls={form === "rename" ? panelId : undefined}
          disabled={busy} onClick={() => open("rename")}>
          <Icon name="edit" size={16} />重命名
        </button>
        {!isPhoto && <button type="button" className="btn-outline" aria-expanded={form === "convert"}
          aria-haspopup="dialog" aria-controls={form === "convert" ? panelId : undefined}
          disabled={busy || !sources.length}
          title={!sources.length ? "转换需要可用的本地视频文件" : undefined}
          onClick={() => open("convert")}>
          <Icon name="convert" size={16} />转换格式
        </button>}
        {!isPhoto && isTs && <button type="button" className="btn-outline" aria-expanded={form === "ts-repair"}
          aria-haspopup="dialog" aria-controls={form === "ts-repair" ? panelId : undefined}
          disabled={busy || !sources.length} onClick={() => open("ts-repair")}>
          <Icon name="refresh" size={16} />修复 TS 时长
        </button>}
      </>, toolbar)}
      {form && (
        <DirectoryDialog key={form} dialogId={panelId} className="basic-action-dialog"
          title={form === "rename" ? (isPhoto ? "重命名照片" : "重命名视频") : form === "convert" ? "转换格式" : "检查并修复 TS 时长"}
          eyebrow={isPhoto ? "PHOTO · 照片操作" : "VIDEO · 视频操作"} pending={busy} onClose={() => setForm(null)}>
      {form === "rename" && (
        <form className="basic-action-form" onSubmit={e => { e.preventDefault(); if (!badName && !busy) rename.mutate(); }}>
          <label className="basic-field">新名称{renameFile ? "（不含扩展名）" : ""}
            <input autoFocus className="input" value={name} maxLength={180} required
              onChange={e => setName(e.target.value)} />
          </label>
          {sources.length > 0 && (
            <label className="basic-checkbox">
              <input type="checkbox" checked={renameFile} onChange={e => {
                setRenameFile(e.target.checked);
                const filename = source?.local_path?.split("/").pop() ?? video.title;
                setName(e.target.checked ? filename.replace(/\.[^.]+$/, "") : video.title);
              }} />
              同时重命名本地文件
            </label>
          )}
          {renameFile && sourceSelect}
          <p className="text-xs text-muted">
            {renameFile ? "保留原扩展名，已有同名文件时不会覆盖。媒体库中的名称同步更新。" : "只修改媒体库中的显示名称。"}
          </p>
          {renameFile && hasActive && <p className="inline-alert">此视频有待完成的转换，请完成或取消后再重命名文件。</p>}
          {rename.isError && <p className="inline-alert" role="alert">{rename.error.message}</p>}
          <div className="basic-action-buttons">
            <button type="button" className="btn-outline" disabled={busy} onClick={() => setForm(null)}>取消</button>
            <button className="btn-primary" disabled={badName || busy || (renameFile && hasActive)}>
              {rename.isPending ? "正在保存…" : "保存名称"}
            </button>
          </div>
        </form>
      )}
      {form === "convert" && (
        <ConversionSettings videoId={video.id} source={source} sourceSelect={sourceSelect}
          pending={busy} error={convert.isError ? convert.error.message : null}
          onSubmit={options => convert.mutate(options)} onClose={() => setForm(null)} />
      )}
      {form === "ts-repair" && (
        <div className="basic-action-form">
          {sourceSelect}
          {tsInspection.isLoading && <p role="status" className="text-xs text-muted">正在读取视频流与容器时长…</p>}
          {tsInspection.isError && <p role="alert" className="inline-alert">检查失败：{tsInspection.error.message}</p>}
          {tsInspection.data && <>
            <p>{tsInspection.data.reason}</p>
            <dl className="text-xs text-muted">
              <div><dt>视频流时长</dt><dd>{tsInspection.data.stream_duration?.toFixed(3) ?? "缺失"} 秒</dd></div>
              <div><dt>容器时长</dt><dd>{tsInspection.data.format_duration?.toFixed(3) ?? "缺失"} 秒</dd></div>
            </dl>
            <p className="text-xs text-muted">修复会无损复制全部轨道并重建时间戳，不截断或重新编码。下一步可选择下载新文件，或替换当前来源并移除原片。</p>
          </>}
          {repair.isError && <p role="alert" className="inline-alert">提交失败：{repair.error.message}</p>}
          <div className="basic-action-buttons">
            <button type="button" className="btn-outline" disabled={busy} onClick={() => setForm(null)}>取消</button>
            <button type="button" className="btn-primary"
              disabled={busy || !tsInspection.data?.needs_repair} onClick={() => { setForm(null); setConfirmRepair(true); }}>
              继续修复
            </button>
          </div>
        </div>
      )}
        </DirectoryDialog>
      )}
      {confirmRepair && (
        <DirectoryDialog dialogId={`${panelId}-repair-confirm`} className="basic-action-dialog"
          title="修复完成后如何处理？" eyebrow="TS REPAIR · 输出方式"
          pending={repair.isPending} onClose={() => setConfirmRepair(false)}>
          <div className="basic-action-form">
            <p>请选择修复结果的处理方式。两种方式都不会截断或重新编码音视频。</p>
            <p className="inline-alert">选择替换后，原片会在新文件校验并安装成功后永久移除，无法从项目中恢复。</p>
            {repair.isError && <p role="alert" className="inline-alert">提交失败：{repair.error.message}</p>}
            <div className="basic-action-buttons">
              <button type="button" className="btn-outline" disabled={repair.isPending}
                onClick={() => repair.mutate(false)}>生成新文件，不替换</button>
              <button type="button" className="btn-primary" disabled={repair.isPending}
                onClick={() => repair.mutate(true)}>
                {repair.isPending ? "正在提交…" : "替换来源并移除原片"}
              </button>
            </div>
          </div>
        </DirectoryDialog>
      )}
      <section className="card basic-video-actions" aria-label="视频基本操作"
        hidden={!notice && !conversions.isError && !conversions.data?.items.length}>
      {notice && <p className="basic-notice" role="status">{notice}</p>}
      {conversions.isError && (
        <p className="inline-alert" role="alert">转换记录读取失败：{conversions.error.message}
          <button className="btn-ghost" onClick={() => conversions.refetch()}>重试</button>
        </p>
      )}
      {!!conversions.data?.items.length && (
        <div className="basic-conversion-list" aria-label="最近的转换记录">
          {conversions.data.items.map(job => (
            <article key={job.id} className="basic-conversion">
              <div className="basic-conversion-heading">
                <strong>{job.result?.ts_repair || job.payload?._ts_repair ? "TS 时长修复" : "格式转换"} #{job.id}</strong>
                <span>{STATES[job.state]}</span>
              </div>

              {job.message && !["queued", "running"].includes(job.state) && <p className="text-xs text-muted">{job.message}</p>}
              {job.error && <p className="inline-alert" role="alert">{job.error}</p>}
              <ConversionActions job={job} />
            </article>
          ))}
        </div>
      )}
    </section>
    </>
  );
}
