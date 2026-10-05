import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { createCategory, listCategories, moveVideo } from "../api/client";
import type { DirectoryCategory, VideoDetail } from "../api/types";
import Icon from "./Icon";
import "./directory-tools.css";

const categoryLabel = (c: DirectoryCategory) => `${c.storage_name} / ${c.path || "根目录"}`;
const useCategories = (mediaType: "video" | "photo") => useQuery({
  queryKey: ["categories", mediaType], queryFn: () => listCategories(mediaType), staleTime: 30_000,
});

export function DirectoryDialog({ title, children, pending, onClose,
  eyebrow = "DIRECTORY · 目录整理", dialogId, className = "",
}: {
  title: string; children: ReactNode; pending: boolean; onClose: () => void;
  eyebrow?: string; dialogId?: string; className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  // Capture the trigger before a child input's autoFocus runs during mounting.
  const returnFocus = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const id = useId();
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => {
      dialog.close();
      if (returnFocus.current?.isConnected) returnFocus.current.focus({ preventScroll: true });
    };
  }, []);
  return <dialog ref={ref} id={dialogId} className={`directory-dialog ${className}`} aria-labelledby={id}
    onCancel={(e) => { e.preventDefault(); if (!pending) onClose(); }}>
    <div className="directory-dialog-heading">
      <div><span className="eyebrow">{eyebrow}</span><h2 id={id}>{title}</h2></div>
      <button type="button" className="btn-ghost" aria-label="关闭窗口" disabled={pending} onClick={onClose}>
        <Icon name="close" size={18} />
      </button>
    </div>
    {children}
  </dialog>;
}

function CreateCategoryDialog({ initial, mediaType, onClose, onCreated }: {
  mediaType: "video" | "photo";
  initial: string; onClose: () => void; onCreated: (category: DirectoryCategory) => void;
}) {
  const categories = useCategories(mediaType);
  const available = Array.from(new Map([...(categories.data?.items ?? []), ...(categories.data?.roots ?? [])].filter(c => c.available).map(c => [c.id, c])).values());
  const [parent, setParent] = useState(initial);
  const [name, setName] = useState("");
  const chosen = available.find((c) => c.id === parent) ?? available[0];
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: () => createCategory({ media_type: mediaType, storage_id: chosen!.storage_id, path: chosen!.path, name: name.trim() }),
    onSuccess: (category) => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      onCreated(category);
    },
  });
  return <DirectoryDialog title={mediaType === "photo" ? "新建照片分类" : "新建视频分类"} pending={create.isPending} onClose={onClose}>
    <p className="directory-help">{mediaType === "photo" ? "此分类仅用于照片库。" : "此分类仅用于视频库。"}分类对应本地目录，创建后可将文件移入。</p>
    <form onSubmit={(e) => { e.preventDefault(); if (chosen && name.trim() && !create.isPending) create.mutate(); }}>
      <label>创建位置<select className="input" value={chosen?.id ?? ""} disabled={create.isPending || !chosen}
        onChange={(e) => { setParent(e.target.value); create.reset(); }}>
        {available.map((c) => <option key={c.id} value={c.id}>{categoryLabel(c)}</option>)}
      </select></label>
      <label>分类名称<input className="input" autoFocus maxLength={180} required value={name}
        disabled={create.isPending} placeholder="例如：旅行记录"
        onChange={(e) => { setName(e.target.value); create.reset(); }} /></label>
      {chosen && <p className="directory-path">创建于：{categoryLabel(chosen)} / {name.trim() || "新分类"}</p>}
      {categories.isLoading && <p role="status">正在读取目录…</p>}
      {categories.isError && <p className="inline-alert" role="alert">{categories.error.message}</p>}
      {!categories.isLoading && !categories.isError && !chosen && <p>没有可写入的本地分类位置。请先<Link className="text-link" to="/storages">连接本地存储源</Link>。</p>}
      {create.isError && <p className="inline-alert" role="alert">{create.error.message}</p>}
      <div className="directory-dialog-actions">
        <button type="button" className="btn-outline" disabled={create.isPending} onClick={onClose}>取消</button>
        <button className="btn-primary" disabled={!chosen || !name.trim() || create.isPending}>
          {create.isPending ? "创建中…" : "创建目录"}
        </button>
      </div>
    </form>
  </DirectoryDialog>;
}

export function CategoryFilter({ value, onChange, mediaType = "video" }: { value: string; onChange: (id: string) => void; mediaType?: "video" | "photo" }) {
  const categories = useCategories(mediaType);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState("");
  return <div className="directory-filter">
    <div className="directory-filter-row">
      <Icon name="folder" size={19} />
      <label className="directory-filter-label">目录分类
        <select className="input" value={value} onChange={(e) => { onChange(e.target.value); setNotice(""); }}>
          <option value="">全部分类</option>
          {value && !categories.data?.items.some((c) => c.id === value) && <option value={value}>当前分类</option>}
          {categories.data?.items.map((c) => <option key={c.id} value={c.id}>
            {categoryLabel(c)} · {c.video_count ?? 0} {mediaType === "photo" ? "张" : "部"}{c.available ? "" : "（不可用）"}
          </option>)}
        </select>
      </label>
      <button className="btn-outline" onClick={() => setCreating(true)}><Icon name="plus" size={16} />新建分类</button>
      <button className="btn-ghost" aria-label="刷新目录分类" disabled={categories.isFetching} onClick={() => categories.refetch()}>
        <Icon name="refresh" size={16} />
      </button>
    </div>
    <p className="directory-help">按文件所在的父目录分类；每个分类仅展示当前媒体库中该目录直属的文件。</p>
    {categories.isLoading && <p role="status">正在识别目录分类…</p>}
    {categories.isError && <p className="inline-alert" role="alert">分类加载失败：{categories.error.message}</p>}
    {categories.data?.warnings.map((warning) => <p className="directory-help" key={warning} role="status">{warning}</p>)}
    {notice && <p className="directory-notice" role="status">{notice}</p>}
    {creating && <CreateCategoryDialog mediaType={mediaType} initial={value} onClose={() => setCreating(false)} onCreated={(category) => {
      setCreating(false); onChange(category.id); setNotice(`已创建本地目录「${category.name}」，现在可以将文件移入。`);
    }} />}
  </div>;
}

function MoveVideoDialog({ video, onClose, onMoved }: {
  video: VideoDetail; onClose: () => void; onMoved: (jobId: number) => void;
}) {
  const categories = useCategories(video.media_type);
  const qc = useQueryClient();
  const sources = video.sources.filter((s) => s.source_type === "local" && s.available && s.local_path);
  const [sourceId, setSourceId] = useState(sources[0]?.id ?? 0);
  const [target, setTarget] = useState("");
  const [creating, setCreating] = useState(false);
  const source = sources.find((s) => s.id === sourceId);
  const chosen = categories.data?.items.find((c) => c.id === target);
  const filename = source?.local_path?.split("/").at(-1) ?? video.title;
  const move = useMutation({
    mutationFn: () => moveVideo(video.id, { source_id: sourceId, storage_id: chosen!.storage_id, path: chosen!.path }),
    onSuccess: (result) => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      onMoved(result.id);
    },
  });
  return <DirectoryDialog title={video.media_type === "photo" ? "移动照片" : "移动视频"} pending={move.isPending} onClose={onClose}>
    <p className="directory-help">移动会在后台完成，提交后可离开页面，完成后会收到通知。多来源条目仅移动所选来源，同名文件不会被覆盖。</p>
    <form onSubmit={(e) => { e.preventDefault(); if (source && chosen?.available && !move.isPending) move.mutate(); }}>
      <label>要移动的文件<select className="input" value={sourceId} disabled={move.isPending}
        onChange={(e) => { setSourceId(Number(e.target.value)); move.reset(); }}>
        {sources.map((s) => <option key={s.id} value={s.id}>{s.local_path}</option>)}
      </select></label>
      <label>目标分类<select className="input" value={target} disabled={move.isPending}
        onChange={(e) => { setTarget(e.target.value); move.reset(); }}>
        <option value="">请选择目标目录</option>
        {categories.data?.items.filter((c) => c.available).map((c) =>
          <option key={c.id} value={c.id}>{categoryLabel(c)}</option>)}
      </select></label>
      <button type="button" className="text-link" disabled={move.isPending} onClick={() => setCreating(true)}>
        <Icon name="plus" size={15} /> 新建目标分类
      </button>
      {chosen && <p className="directory-path">移入：{categoryLabel(chosen)} / {filename}</p>}
      {categories.isLoading && <p role="status">正在读取分类…</p>}
      {categories.isError && <p className="inline-alert" role="alert">{categories.error.message}</p>}
      {move.isError && <p className="inline-alert" role="alert">{move.error.message}</p>}
      {move.isPending && <p className="directory-notice" role="status">正在提交后台任务…</p>}
      <div className="directory-dialog-actions">
        <button type="button" className="btn-outline" onClick={onClose} disabled={move.isPending}>取消</button>
        <button className="btn-primary" disabled={!source || !chosen?.available || move.isPending}>
          {move.isPending ? "提交中…" : "提交后台移动"}
        </button>
      </div>
    </form>
    {creating && <CreateCategoryDialog mediaType={video.media_type} initial={target} onClose={() => setCreating(false)} onCreated={(category) => {
      setTarget(category.id); setCreating(false);
    }} />}
  </DirectoryDialog>;
}

export function MoveVideoButton({ video }: { video: VideoDetail }) {
  const [open, setOpen] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const hasLocal = video.sources.some((s) => s.source_type === "local" && s.available);
  if (!hasLocal) return null;
  return <div className="directory-move-control">
    <button className="btn-outline" onClick={() => { setOpen(true); }}><Icon name="folder" size={16} />{video.media_type === "photo" ? "移动照片" : "移动视频"}</button>
    {jobId && <p className="directory-move-notice" role="status">已提交后台移动，可离开当前页面。<Link className="text-link" to={`/jobs?job=${jobId}`}>查看任务 #{jobId}</Link></p>}
    {open && <MoveVideoDialog video={video} onClose={() => setOpen(false)} onMoved={(id) => { setJobId(id); setOpen(false); }} />}
  </div>;
}
