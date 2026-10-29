import { useId, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Button, Input, Modal, Select } from "@arco-design/web-react";
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
  const id = useId();
  return <Modal
    visible
    className={`directory-dialog ${className}`}
    title={<div className="directory-dialog-heading"><div><span className="eyebrow">{eyebrow}</span><h2 id={id}>{title}</h2></div></div>}
    footer={null}
    alignCenter
    maskClosable={!pending}
    escToExit={!pending}
    onCancel={() => { if (!pending) onClose(); }}
  >
    <div id={dialogId}>{children}</div>
  </Modal>;
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
      <label>创建位置<Select value={chosen?.id ?? ""} style={{ width: "100%" }} disabled={create.isPending || !chosen}
        onChange={(value) => { setParent(value as string); create.reset(); }}
        options={available.map((c) => ({ value: c.id, label: categoryLabel(c) }))} /></label>
      <label>分类名称<Input autoFocus maxLength={180} required value={name}
        disabled={create.isPending} placeholder="例如：旅行记录"
        onChange={(value) => { setName(value); create.reset(); }} /></label>
      {chosen && <p className="directory-path">创建于：{categoryLabel(chosen)} / {name.trim() || "新分类"}</p>}
      {categories.isLoading && <p role="status">正在读取目录…</p>}
      {categories.isError && <p className="inline-alert" role="alert">{categories.error.message}</p>}
      {!categories.isLoading && !categories.isError && !chosen && <p>没有可写入的本地分类位置。请先<Link className="text-link" to="/storages">连接本地存储源</Link>。</p>}
      {create.isError && <p className="inline-alert" role="alert">{create.error.message}</p>}
      <div className="directory-dialog-actions">
        <Button disabled={create.isPending} onClick={onClose}>取消</Button>
        <Button type="primary" htmlType="submit" loading={create.isPending}
          disabled={!chosen || !name.trim() || create.isPending}>
          创建目录
        </Button>
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
        <Select value={value} style={{ width: "100%" }} onChange={(value) => { onChange(value as string); setNotice(""); }}
          options={[
            { label: "全部分类", value: "" },
            ...(value && !categories.data?.items.some((c) => c.id === value) ? [{ label: "当前分类", value }] : []),
            ...(categories.data?.items.map((c) => ({
              value: c.id,
              label: `${categoryLabel(c)} · ${c.video_count ?? 0} ${mediaType === "photo" ? "张" : "部"}${c.available ? "" : "（不可用）"}`,
            })) ?? []),
          ]} />
      </label>
      <Button onClick={() => setCreating(true)}><Icon name="plus" size={16} />新建分类</Button>
      <button type="button" className="btn-ghost" aria-label="刷新目录分类" disabled={categories.isFetching} onClick={() => categories.refetch()}>
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
      <label>要移动的文件<Select value={sourceId} style={{ width: "100%" }} disabled={move.isPending}
        onChange={(value) => { setSourceId(Number(value)); move.reset(); }}
        options={sources.map((s) => ({ value: s.id, label: s.local_path }))} /></label>
      <label>目标分类<Select value={target} style={{ width: "100%" }} disabled={move.isPending}
        onChange={(value) => { setTarget(value as string); move.reset(); }}
        options={[
          { label: "请选择目标目录", value: "" },
          ...(categories.data?.items.filter((c) => c.available).map((c) =>
            ({ value: c.id, label: categoryLabel(c) })) ?? []),
        ]} /></label>
      <button type="button" className="text-link" disabled={move.isPending} onClick={() => setCreating(true)}>
        <Icon name="plus" size={15} /> 新建目标分类
      </button>
      {chosen && <p className="directory-path">移入：{categoryLabel(chosen)} / {filename}</p>}
      {categories.isLoading && <p role="status">正在读取分类…</p>}
      {categories.isError && <p className="inline-alert" role="alert">{categories.error.message}</p>}
      {move.isError && <p className="inline-alert" role="alert">{move.error.message}</p>}
      {move.isPending && <p className="directory-notice" role="status">正在提交后台任务…</p>}
      <div className="directory-dialog-actions">
        <Button onClick={onClose} disabled={move.isPending}>取消</Button>
        <Button type="primary" htmlType="submit" loading={move.isPending}
          disabled={!source || !chosen?.available || move.isPending}>
          提交后台移动
        </Button>
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
    <Button onClick={() => { setOpen(true); }}><Icon name="folder" size={16} />{video.media_type === "photo" ? "移动照片" : "移动视频"}</Button>
    {jobId && <p className="directory-move-notice" role="status">已提交后台移动，可离开当前页面。<Link className="text-link" to={`/jobs?job=${jobId}`}>查看任务 #{jobId}</Link></p>}
    {open && <MoveVideoDialog video={video} onClose={() => setOpen(false)} onMoved={(id) => { setJobId(id); setOpen(false); }} />}
  </div>;
}
