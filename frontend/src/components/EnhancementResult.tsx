import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@arco-design/web-react";
import { cancelJob } from "../api/client";
import { enhancementFile, importEnhancement } from "../api/enhancement";
import type { Job } from "../api/types";
import { formatSize } from "../lib/format";
import Icon from "./Icon";
import "./EnhanceVideo.css";

export function EnhancementComparison({ job }: { job: Job }) {
  const original = useRef<HTMLVideoElement>(null);
  const enhanced = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(false);
  const toggle = async () => {
    const videos = [original.current, enhanced.current].filter(
      (v): v is HTMLVideoElement => !!v,
    );
    if (playing) {
      videos.forEach((v) => v.pause());
      setPlaying(false);
      return;
    }
    setError("");
    try {
      videos.forEach((v) => {
        v.currentTime = 0;
        v.muted = true;
      });
      await Promise.all(videos.map((v) => v.play()));
      setPlaying(true);
    } catch {
      videos.forEach((v) => v.pause());
      setPlaying(false);
      setError("预览暂时无法播放，请重试或下载文件查看。");
    }
  };
  return (
    <div className="enhancement-comparison-wrap">
      <p className="directory-help">
        预览对比画面与音轨，文字字幕设置用于完整输出。
      </p>
      <div className="enhancement-comparison">
        <figure>
          <figcaption>
            原片<span>相同时间片段</span>
          </figcaption>
          <video
            ref={original}
            controls
            playsInline
            muted
            preload="metadata"
            aria-label="原片预览"
            onError={() => setError("原片预览加载失败，请重新生成预览。")}
            src={enhancementFile(job.id, true)}
            onEnded={() => setPlaying(false)}
          />
        </figure>
        <figure>
          <figcaption>
            增强后
            <span>
              {String(job.result?.width)} × {String(job.result?.height)} ·{" "}
              {String(job.result?.fps)} fps
            </span>
          </figcaption>
          <video
            ref={enhanced}
            controls
            playsInline
            muted
            preload="metadata"
            aria-label="增强后预览"
            onError={() => setError("增强预览加载失败，请重新生成预览。")}
            src={enhancementFile(job.id)}
            onEnded={() => setPlaying(false)}
          />
        </figure>
      </div>
      <Button onClick={toggle}>
        <Icon name="play" size={14} />
        {playing ? "暂停对比" : "从头同步播放"}
      </Button>
      {error && (
        <p className="inline-alert" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export default function EnhancementResult({
  job,
  comparison = false,
}: {
  job: Job;
  comparison?: boolean;
  showProgress?: boolean;
}) {
  const qc = useQueryClient();
  const refresh = () => {
    for (const key of ["jobs", "job", "enhancements"])
      void qc.invalidateQueries({ queryKey: [key] });
  };
  const cancel = useMutation({
    mutationFn: () => cancelJob(job.id),
    onSuccess: refresh,
  });
  const imported = useMutation({
    mutationFn: () => importEnhancement(job.id),
    onSuccess: () => {
      refresh();
      for (const key of ["videos", "storages", "categories"])
        void qc.invalidateQueries({ queryKey: [key] });
    },
  });
  const result = job.result;
  const importedId =
    imported.data?.video_id ?? Number(result?.imported_video_id || 0);
  return (
    <div className="enhancement-result">

      {["queued", "running"].includes(job.state) && (
        <>
          <Button
            onClick={() => cancel.mutate()}
            loading={cancel.isPending}
          >
            取消增强任务
          </Button>
        </>
      )}
      {job.state === "done" && result && (
        <>
          <p className="enhancement-output">
            {String(result.width)} × {String(result.height)}
            <span>·</span>
            {String(result.fps)} fps<span>·</span>
            {formatSize(Number(result.size_bytes))}
            {Number(result.bitrate_bps) > 0 && (
              <>
                <span>·</span>实际总码率{" "}
                {(Number(result.bitrate_bps) / 1e6).toFixed(2)} Mbps
              </>
            )}
          </p>
          {!!Number(result.skipped_subtitles) && (
            <p className="directory-help">
              原片中的图形字幕无法写入 MP4，已保留原片供查看；文字字幕正常保留。
            </p>
          )}
          <div className="enhancement-result-actions">
            <a
              className="btn-outline"
              href={enhancementFile(job.id, false, true)}
              download={String(result.filename)}
            >
              <Icon name="arrow" size={14} />
              {result.preview ? "下载预览" : "下载增强视频"}
            </a>
            {!result.preview &&
              (importedId ? (
                <Link className="btn-outline" to={`/videos/${importedId}`}>
                  查看增强视频
                </Link>
              ) : (
                <Button
                  type="primary"
                  onClick={() => imported.mutate()}
                  loading={imported.isPending}
                >
                  加入影像库
                </Button>
              ))}
          </div>
          {result.preview === true &&
            (comparison ? (
              <EnhancementComparison job={job} />
            ) : (
              <details className="enhancement-preview-details">
                <summary>查看原片与增强对比</summary>
                <EnhancementComparison job={job} />
              </details>
            ))}
        </>
      )}
      {cancel.isError && (
        <p className="inline-alert" role="alert">
          {cancel.error.message}
        </p>
      )}
      {imported.isError && (
        <p className="inline-alert" role="alert">
          {imported.error.message}
        </p>
      )}
    </div>
  );
}
