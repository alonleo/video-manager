import { useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  enhanceVideo,
  enhancementCapabilities,
  listEnhancements,
} from "../api/enhancement";
import type { EnhanceOptions } from "../api/enhancement";
import type { VideoDetail } from "../api/types";
import { DirectoryDialog } from "./DirectoryTools";
import EnhancementResult from "./EnhancementResult";
import Icon from "./Icon";
import { formatSize } from "../lib/format";
import "./EnhanceVideo.css";

const states = {
  queued: "排队中",
  running: "处理中",
  done: "已完成",
  error: "失败",
  canceled: "已取消",
};
const defaults: EnhanceOptions = {
  resolution: "source",
  method: "standard",
  ai_model: "general",
  fps: null,
  frame_method: "motion",
  quality: "quality",
  bitrate_mbps: 12,
  encoder: "auto",
  keep_subtitles: true,
  preview: false,
  preview_start: 0,
};

export default function EnhanceVideoActions({
  video,
  toolbar,
}: {
  video: VideoDetail;
  toolbar: HTMLDivElement | null;
}) {
  const qc = useQueryClient();
  const sources = video.sources.filter(
    (s) => s.source_type === "local" && s.available && s.local_path,
  );
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<EnhanceOptions>({
    ...defaults,
    source_id: sources.find((s) => s.is_primary)?.id ?? sources[0]?.id,
  });
  const [previewId, setPreviewId] = useState<number | null>(null);
  const [previewSignature, setPreviewSignature] = useState("");
  const [notice, setNotice] = useState("");
  const capabilities = useQuery({
    queryKey: ["enhancement-capabilities"],
    queryFn: enhancementCapabilities,
    enabled: open,
  });
  const jobs = useQuery({
    queryKey: ["enhancements", video.id],
    queryFn: () => listEnhancements(video.id),
    refetchInterval: (q) =>
      q.state.data?.items.some((j) => ["queued", "running"].includes(j.state))
        ? 1500
        : 10000,
  });
  const change = <K extends keyof EnhanceOptions>(
    key: K,
    value: EnhanceOptions[K],
  ) => setOptions((old) => ({ ...old, [key]: value }));
  const submit = useMutation({
    mutationFn: (preview: boolean) =>
      enhanceVideo(video.id, { ...options, preview }),
    onSuccess: (job, preview) => {
      if (preview) {
        setPreviewId(job.id);
        setPreviewSignature(JSON.stringify(options));
      } else {
        setOpen(false);
        setNotice("增强任务已提交，完成后会弹出通知。");
      }
      for (const key of ["enhancements", "jobs"])
        void qc.invalidateQueries({ queryKey: [key] });
    },
  });
  const preview = jobs.data?.items.find((j) => j.id === previewId);
  const previewBusy = preview && ["queued", "running"].includes(preview.state);
  const aiReady = capabilities.data?.models.find(
    (m) => m.id === options.ai_model,
  )?.available;
  const invalid =
    !sources.some((s) => s.id === options.source_id) ||
    (options.method === "ai" && !aiReady) ||
    (options.quality === "bitrate" &&
      (!Number.isFinite(options.bitrate_mbps) ||
        options.bitrate_mbps < 1 ||
        options.bitrate_mbps > 150)) ||
    !Number.isFinite(options.preview_start) ||
    options.preview_start < 0 ||
    options.preview_start >= (video.duration_ms ?? Infinity) / 1000;
  const estimatedSize =
    options.quality === "bitrate" && video.duration_ms
      ? (((options.bitrate_mbps * 1e6) / 8 + 192000 / 8) * video.duration_ms) /
        1000
      : null;
  const estimateSeconds =
    preview?.result && previewSignature === JSON.stringify(options)
      ? (Number(preview.result.elapsed_seconds) *
          Number(preview.result.source_duration)) /
        (Number(preview.result.duration_ms) / 1000)
      : null;
  return (
    <>
      {toolbar &&
        createPortal(
          <button
            type="button"
            className="btn-outline"
            disabled={!sources.length}
            title={!sources.length ? "画质增强需要可用的本地视频" : undefined}
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => {
              setOpen(true);
              setNotice("");
              submit.reset();
            }}
          >
            <Icon name="enhance" size={16} />
            画质增强
          </button>,
          toolbar,
        )}
      {open && (
        <DirectoryDialog
          title="画质增强"
          eyebrow="ENHANCE · 让影像更清晰流畅"
          pending={submit.isPending}
          className="enhancement-dialog"
          onClose={() => setOpen(false)}
        >
          <div className="enhancement-source-summary">
            <Icon name="film" size={19} />
            <div>
              <strong>{video.title}</strong>
              <span>
                {video.width ?? "—"} × {video.height ?? "—"} ·{" "}
                {video.fps ?? "—"} fps
                {video.bitrate_bps
                  ? ` · ${(video.bitrate_bps / 1e6).toFixed(2)} Mbps`
                  : ""}
              </span>
            </div>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!invalid && !submit.isPending) submit.mutate(false);
            }}
          >
            <fieldset disabled={submit.isPending}>
              <legend className="sr-only">增强参数</legend>
              {sources.length > 1 && (
                <label className="enhancement-field">
                  处理来源
                  <select
                    className="input"
                    value={options.source_id}
                    onChange={(e) =>
                      change("source_id", Number(e.target.value))
                    }
                  >
                    {sources.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.local_path?.split("/").pop()}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div
                className="enhancement-mode"
                role="group"
                aria-label="增强方式"
              >
                <button
                  type="button"
                  aria-pressed={options.method === "standard"}
                  className={options.method === "standard" ? "selected" : ""}
                  onClick={() => change("method", "standard")}
                >
                  <strong>普通增强</strong>
                  <span>高质量放大 · 处理较快</span>
                </button>
                <button
                  type="button"
                  aria-pressed={options.method === "ai"}
                  className={options.method === "ai" ? "selected" : ""}
                  onClick={() =>
                    setOptions((o) => ({
                      ...o,
                      method: "ai",
                      resolution:
                        o.resolution === "source" ? "2x" : o.resolution,
                    }))
                  }
                >
                  <strong>AI 超分辨率</strong>
                  <span>增强纹理 · 建议先预览</span>
                </button>
              </div>
              {options.method === "ai" && (
                <label className="enhancement-field">
                  超分模型
                  <select
                    className="input"
                    value={options.ai_model}
                    onChange={(e) =>
                      change(
                        "ai_model",
                        e.target.value as EnhanceOptions["ai_model"],
                      )
                    }
                  >
                    {(
                      capabilities.data?.models ?? [
                        {
                          id: "general",
                          name: "通用影像",
                          available: false,
                          size_bytes: 0,
                        },
                        {
                          id: "anime",
                          name: "动漫 / 动画",
                          available: false,
                          size_bytes: 0,
                        },
                      ]
                    ).map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                        {m.available
                          ? ` · 已安装 ${formatSize(m.size_bytes)}`
                          : " · 未就绪"}
                      </option>
                    ))}
                  </select>
                  <small>
                    AI
                    会推测细节，运动画面可能出现闪烁；先查看预览再处理完整视频。
                  </small>
                </label>
              )}
              <div className="enhancement-fields">
                <label className="enhancement-field">
                  目标分辨率
                  <select
                    className="input"
                    value={options.resolution}
                    onChange={(e) =>
                      change(
                        "resolution",
                        e.target.value as EnhanceOptions["resolution"],
                      )
                    }
                  >
                    <option value="source" disabled={options.method === "ai"}>
                      保持原分辨率
                    </option>
                    <option value="2x">2 倍放大</option>
                    <option value="1080">1080p</option>
                    <option value="1440">1440p</option>
                    <option value="2160">4K · 2160p</option>
                  </select>
                  <small>保持原画面比例，自动适配横屏和竖屏。</small>
                </label>
                <label className="enhancement-field">
                  目标帧率
                  <select
                    className="input"
                    value={options.fps ?? ""}
                    onChange={(e) =>
                      change(
                        "fps",
                        e.target.value
                          ? (Number(e.target.value) as EnhanceOptions["fps"])
                          : null,
                      )
                    }
                  >
                    <option value="">保持原帧率</option>
                    {[24, 25, 30, 48, 50, 60, 120].map((f) => (
                      <option key={f} value={f}>
                        {f} fps
                      </option>
                    ))}
                  </select>
                  <small>增加帧率不会改变视频时长或播放速度。</small>
                </label>
              </div>
              {options.fps !== null && (
                <label className="enhancement-field">
                  帧率处理
                  <select
                    className="input"
                    value={options.frame_method}
                    onChange={(e) =>
                      change(
                        "frame_method",
                        e.target.value as EnhanceOptions["frame_method"],
                      )
                    }
                  >
                    <option value="motion">运动补帧 · 生成中间画面</option>
                    <option value="duplicate">快速重复帧 · 只调整帧率</option>
                  </select>
                  <small>
                    {options.frame_method === "motion"
                      ? "高于原帧率时估计物体运动并生成新帧；快速运动或遮挡处可能出现伪影。"
                      : "复制已有画面，速度快，但不会增加真实运动细节。"}
                  </small>
                </label>
              )}
              <div className="enhancement-fields">
                <label className="enhancement-field">
                  输出质量
                  <select
                    className="input"
                    value={options.quality}
                    onChange={(e) =>
                      change(
                        "quality",
                        e.target.value as EnhanceOptions["quality"],
                      )
                    }
                  >
                    <option value="quality">自动质量 · 推荐</option>
                    <option value="bitrate">指定目标码率</option>
                  </select>
                </label>
                {options.quality === "bitrate" ? (
                  <label className="enhancement-field">
                    目标视频码率（Mbps）
                    <input
                      className="input"
                      type="number"
                      min={1}
                      max={150}
                      step={0.5}
                      value={
                        Number.isFinite(options.bitrate_mbps)
                          ? options.bitrate_mbps
                          : ""
                      }
                      onChange={(e) =>
                        change("bitrate_mbps", e.target.valueAsNumber)
                      }
                    />
                    <small>
                      实际码率随内容变化；调高码率不会恢复原片细节。
                    </small>
                  </label>
                ) : (
                  <div className="enhancement-field enhancement-quality-note">
                    <strong>清晰度优先</strong>
                    <small>按画面复杂度分配码率，减少增强后的压缩损失。</small>
                  </div>
                )}
              </div>
              <details className="enhancement-advanced">
                <summary>更多设置</summary>
                <label className="enhancement-field">
                  编码方式
                  <select
                    className="input"
                    value={options.encoder}
                    onChange={(e) =>
                      change(
                        "encoder",
                        e.target.value as EnhanceOptions["encoder"],
                      )
                    }
                  >
                    <option value="auto">自动 · 优先硬件加速</option>
                    <option value="software">软件编码 · H.264</option>
                  </select>
                </label>
                <label className="basic-checkbox">
                  <input
                    type="checkbox"
                    checked={options.keep_subtitles}
                    onChange={(e) => change("keep_subtitles", e.target.checked)}
                  />
                  保留原片文字字幕轨
                </label>
                <p className="directory-help">
                  输出 MP4，保留全部音轨。图形字幕不写入增强文件，原片仍可查看。
                </p>
              </details>
              <div className="enhancement-preview-controls">
                <label className="enhancement-field">
                  预览起点（秒）
                  <input
                    className="input"
                    type="number"
                    min={0}
                    step={1}
                    max={
                      video.duration_ms
                        ? Math.max(0, video.duration_ms / 1000 - 0.1)
                        : undefined
                    }
                    value={
                      Number.isFinite(options.preview_start)
                        ? options.preview_start
                        : ""
                    }
                    onChange={(e) =>
                      change("preview_start", e.target.valueAsNumber)
                    }
                  />
                </label>
                <button
                  type="button"
                  className="btn-outline"
                  disabled={invalid || submit.isPending || !!previewBusy}
                  onClick={() => submit.mutate(true)}
                >
                  <Icon name="play" size={15} />
                  {previewBusy ? "正在生成预览…" : "生成 5 秒预览"}
                </button>
              </div>
            </fieldset>
            {capabilities.isLoading && options.method === "ai" && (
              <p role="status" className="directory-help">
                正在检查本地超分模型…
              </p>
            )}
            {capabilities.isError && (
              <p className="inline-alert" role="alert">
                硬件与模型信息读取失败。
                <button type="button" onClick={() => capabilities.refetch()}>
                  重试
                </button>
              </p>
            )}
            {submit.isError && (
              <p className="inline-alert" role="alert">
                {submit.error.message}
              </p>
            )}
            {preview && (
              <section
                className="enhancement-preview-status"
                aria-label="增强预览"
              >
                <div className="enhancement-job-title">
                  <strong>预览 #{preview.id}</strong>
                  <span>
                    {states[preview.state]}
                    {preview.state === "running"
                      ? ` · ${Math.round(preview.progress * 100)}%`
                      : ""}
                  </span>
                </div>
                {preview.message && (
                  <p className="directory-help">{preview.message}</p>
                )}
                {preview.error && (
                  <p className="inline-alert" role="alert">
                    {preview.error}
                  </p>
                )}
                {previewSignature !== JSON.stringify(options) && (
                  <p className="directory-help">
                    参数已调整，下面是上一次设置的预览。
                  </p>
                )}
                <EnhancementResult job={preview} comparison />
              </section>
            )}
            <div className="enhancement-estimate">
              <span>另存为 MP4 · 原视频保留</span>
              {estimatedSize && Number.isFinite(estimatedSize) ? (
                <span>
                  预计约 {formatSize(estimatedSize)}，实际大小可能不同
                </span>
              ) : (
                <span>文件大小由画面内容和增强参数决定</span>
              )}
              {estimateSeconds && Number.isFinite(estimateSeconds) ? (
                <span>
                  按预览速度估算，完整处理约{" "}
                  {Math.max(1, Math.round(estimateSeconds / 60))} 分钟
                </span>
              ) : null}
            </div>
            <div className="directory-dialog-actions">
              <button
                type="button"
                className="btn-outline"
                disabled={submit.isPending}
                onClick={() => setOpen(false)}
              >
                关闭
              </button>
              <button
                className="btn-primary"
                disabled={invalid || submit.isPending}
              >
                {submit.isPending ? "正在提交…" : "开始完整增强"}
                <Icon name="arrow" size={16} />
              </button>
            </div>
          </form>
        </DirectoryDialog>
      )}
      {(notice || jobs.isError || !!jobs.data?.items.length) && (
        <section className="card enhancement-history" aria-label="画质增强记录">
          {notice && (
            <p className="basic-notice" role="status">
              {notice}
            </p>
          )}
          <div className="enhancement-job-title">
            <h2>画质增强记录</h2>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => jobs.refetch()}
              disabled={jobs.isFetching}
              aria-label="刷新增强记录"
            >
              <Icon name="refresh" size={15} />
            </button>
          </div>
          {jobs.isError && (
            <p className="inline-alert" role="alert">
              {jobs.error.message}
            </p>
          )}
          {jobs.data?.items.map((job) => (
            <article className="enhancement-history-item" key={job.id}>
              <div className="enhancement-job-title">
                <strong>
                  {job.kind === "enhance_preview" ? "增强预览" : "视频增强"} #
                  {job.id}
                </strong>
                <span>{states[job.state]}</span>
              </div>
              {job.message && <p className="directory-help">{job.message}</p>}
              {job.error && (
                <p className="inline-alert" role="alert">
                  {job.error}
                </p>
              )}
              <EnhancementResult job={job} />
            </article>
          ))}
        </section>
      )}
    </>
  );
}
