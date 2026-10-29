import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@arco-design/web-react";
import { deleteVideo } from "../api/client";
import { DirectoryDialog } from "./DirectoryTools";
import Icon from "./Icon";
import "./remove-video.css";

export default function RemoveVideoButton({ video, compact = false, onRemoved }: {
  video: { id: number; title: string; media_type?: "photo" | "video" };
  compact?: boolean;
  onRemoved?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: () => deleteVideo(video.id),
    onSuccess: async () => {
      // Discard older list requests before refreshing counts and pagination.
      await qc.cancelQueries({ queryKey: ["videos"] });
      setOpen(false);
      onRemoved?.();
      for (const key of ["video", "playback", "subtitles", "conversions", "enhancements"]) {
        void qc.invalidateQueries({ queryKey: [key, video.id], refetchType: "none" });
      }
      await Promise.all(["videos", "tags", "categories", "jobs", "storages"].map(
        key => qc.invalidateQueries({ queryKey: [key] }),
      ));
    },
  });
  return <>
    {compact ? <button type="button"
      className="video-remove-trigger"
      aria-label={`移除收录：${video.title}`}
      title="移除收录，保留原文件"
      aria-haspopup="dialog"
      onClick={() => { remove.reset(); setOpen(true); }}>
      <Icon name="close" size={16} />
    </button> : <Button className="video-remove-action"
      aria-label="移除收录"
      title="移除收录，保留原文件"
      onClick={() => { remove.reset(); setOpen(true); }}>
      <Icon name="close" size={16} /><span>移除收录</span>
    </Button>}
    {open && <DirectoryDialog title={video.media_type === "photo" ? "移除照片收录？" : "移除视频收录？"} eyebrow="LIBRARY · 收录管理"
      className="remove-video-dialog" pending={remove.isPending} onClose={() => setOpen(false)}>
      <p className="remove-video-title">{video.title}</p>
      <p className="directory-help">原文件会保留在磁盘上。移除后，刷新页面或重启应用都不会重新收录；再次扫描对应存储源或目录时才会重新收录。</p>
      <p className="directory-help">{video.media_type === "photo" ? "该照片的收藏与标签关联将一并清除，重新扫描不会恢复这些记录。" : "该视频的收藏、标签关联、观看进度、字幕及处理记录将一并清除，重新扫描不会恢复这些记录。"}</p>
      {remove.isError && <p className="inline-alert" role="alert">{remove.error.message}</p>}
      <div className="directory-dialog-actions">
        <Button autoFocus disabled={remove.isPending}
          onClick={() => setOpen(false)}>取消</Button>
        <Button type="primary" className="remove-video-confirm" loading={remove.isPending}
          disabled={remove.isPending}
          onClick={() => { if (!remove.isPending) remove.mutate(); }}>
          确认移除收录
        </Button>
      </div>
    </DirectoryDialog>}
  </>;
}
