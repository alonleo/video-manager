import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteVideoSource, previewSourceDeletion, type SourceDeletionResult } from "../api/client";
import type { VideoDetail } from "../api/types";
import { formatSize } from "../lib/format";
import { DirectoryDialog } from "./DirectoryTools";
import Icon from "./Icon";
import "./delete-video-source.css";

function DeleteSourceDialog({ video, onClose, onDeleted }: {
  video: VideoDetail;
  onClose: () => void;
  onDeleted: (result: SourceDeletionResult) => void;
}) {
  const sources = video.sources.filter(source => source.source_type === "local" && source.local_path);
  const [sourceId, setSourceId] = useState(sources[0]?.id ?? 0);
  const [confirmed, setConfirmed] = useState("");
  const qc = useQueryClient();
  const preview = useQuery({
    queryKey: ["source-deletion", video.id, sourceId],
    queryFn: () => previewSourceDeletion(video.id, sourceId),
    enabled: !!sourceId,
    retry: false,
    staleTime: 0,
  });
  const info = preview.data;
  const remove = useMutation({
    mutationFn: () => deleteVideoSource(video.id, sourceId, info!.confirmation),
    onSuccess: async result => {
      await Promise.all(["videos", "video", "playback"].map(key =>
        qc.cancelQueries({ queryKey: key === "videos" ? [key] : [key, video.id] }),
      ));
      onDeleted(result);
      for (const key of ["video", "playback", "subtitles", "conversions", "enhancements"]) {
        void qc.invalidateQueries({
          queryKey: [key, video.id], refetchType: result.video_removed ? "none" : "active",
        });
      }
      await Promise.all(["videos", "tags", "categories", "jobs", "storages"].map(key =>
        qc.invalidateQueries({ queryKey: [key] }),
      ));
      qc.removeQueries({ queryKey: ["source-deletion", video.id] });
    },
    onError: () => setConfirmed(""),
  });
  const ready = !!info && confirmed === info.confirmation &&
    !preview.isFetching && !preview.isError && !remove.isPending;

  return <DirectoryDialog title="删除磁盘源文件？" eyebrow="SOURCE · 文件删除"
    className="delete-source-dialog" pending={remove.isPending} onClose={onClose}>
    <p className="delete-source-title">{video.title}</p>
    <p className="delete-source-warning">永久删除所选视频文件，不经过系统回收站。请确认文件路径；所在目录和其他文件会保留。</p>
    <label>要删除的本地来源
      <select className="input" value={sourceId} disabled={remove.isPending}
        onChange={event => { setSourceId(Number(event.target.value)); setConfirmed(""); remove.reset(); }}>
        {sources.map(source => <option key={source.id} value={source.id}>{source.local_path}</option>)}
      </select>
    </label>
    {preview.isFetching && <p className="directory-help" role="status">正在检查文件与处理任务…</p>}
    {preview.isError && <p className="inline-alert" role="alert">{preview.error.message}</p>}
    {info && !preview.isError && <div className="delete-source-details">
      <p className="delete-source-path">{info.path}</p>
      <p className="directory-help">文件大小：{formatSize(info.size_bytes)}</p>
      <p className="directory-help">{info.removes_video
        ? "这是最后一个来源。删除文件后，此视频的收录、收藏、标签关联、观看进度及字幕记录也会移除。"
        : `删除后保留其他 ${info.remaining_sources} 个来源，以及收藏、标签、观看进度和字幕。`}</p>
      {info.affected_sources > 1 && <p className="directory-help">此文件由多个存储目录重复收录，关联的 {info.affected_sources} 条来源记录会同步更新。</p>}
    </div>}
    <label className="delete-source-confirmation">
      <input type="checkbox" checked={!!info && confirmed === info.confirmation}
        disabled={!info || preview.isFetching || preview.isError || remove.isPending}
        onChange={event => setConfirmed(event.target.checked ? info!.confirmation : "")} />
      <span>我确认永久删除上述文件，此操作无法撤销</span>
    </label>
    {remove.isError && <p className="inline-alert" role="alert">{remove.error.message}</p>}
    <div className="directory-dialog-actions">
      <button type="button" className="btn-outline" autoFocus disabled={remove.isPending} onClick={onClose}>取消</button>
      {(preview.isError || remove.isError) && <button type="button" className="btn-outline"
        disabled={preview.isFetching || remove.isPending}
        onClick={() => { setConfirmed(""); remove.reset(); void preview.refetch(); }}>重新检查</button>}
      <button type="button" className="btn-primary delete-source-submit" disabled={!ready}
        onClick={() => { if (ready) remove.mutate(); }}>
        {remove.isPending ? "正在删除…" : "永久删除文件"}
      </button>
    </div>
  </DirectoryDialog>;
}

export default function DeleteVideoSourceButton({ video, onDeleted }: {
  video: VideoDetail;
  onDeleted: (result: SourceDeletionResult) => void;
}) {
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState("");
  if (!video.sources.some(source => source.source_type === "local" && source.local_path)) return null;
  return <>
    <button type="button" className="btn-outline delete-source-trigger" aria-haspopup="dialog"
      onClick={() => { setNotice(""); setOpen(true); }}>
      <Icon name="trash" size={16} />删除源文件
    </button>
    {notice && <p className="delete-source-notice" role="status">{notice}</p>}
    {open && <DeleteSourceDialog video={video} onClose={() => setOpen(false)} onDeleted={result => {
      setOpen(false);
      setNotice(result.warning ?? "已删除所选磁盘文件，其余来源和观看记录已保留。");
      onDeleted(result);
    }} />}
  </>;
}
