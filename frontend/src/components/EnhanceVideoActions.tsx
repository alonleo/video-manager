import { useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, InputNumber, Select } from "@arco-design/web-react";
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
          <Button
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
          </Button>,
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
                  <Select
                    value={options.source_id}
                    style={{ width: "100%" }}
                    onChange={(value) =>
                      change("source_id", Number(value))
                    }
                    options={sources.map((s) => ({
                      value: s.id,
                      label: s.local_path?.split("/").pop(),
                    }))}
                  />
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
                  <Select
                    value={options.ai_model}
                    style={{ width: "100%" }}
                    onChange={(value) =>
                      change(
                        "ai_model",
                        value as EnhanceOptions["ai_model"],
                      )
                    }
                    options={(
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
                    ).map((m) => ({
                      value: m.id,
                      label: `${m.name}${m.available
                        ? ` · 已安装 ${formatSize(m.size_bytes)}`
                        : " · 未就绪"}`,
                    }))}
                  />
                  <small>
                    AI
                    会推测细节，运动画面可能出现闪烁；先查看预览再处理完整视频。
                  </small>
                </label>
              )}
              <div className="enhancement-fields">
                <label className="enhancement-field">
                  目标分辨率
                  <Select
                    value={options.resolution}
                    style={{ width: "100%" }}
                    onChange={(value) =>
                      change(
                        "resolution",
                        value as EnhanceOptions["resolution"],
                      )
                    }
                    options={[
                      { label: "保持原分辨率", value: "source", disabled: options.method === "ai" },
                      { label: "2 倍放大", value: "2x" },
                      { label: "1080p", value: "1080" },
                      { label: "1440p", value: "1440" },
                      { label: "4K · 2160p", value: "2160" },
                    ]}
                  />
                  <small>保持原画面比例，自动适配横屏和竖屏。</small>
                </label>
                <label className="enhancement-field">
                  目标帧率
                  <Select
                    value={options.fps ?? ""}
                    style={{ width: "100%" }}
                    onChange={(value) =>
                      change(
                        "fps",
                        value
                          ? (Number(value) as EnhanceOptions["fps"])
                          : null,
                      )
                    }
                    options={[
                      { label: "保持原帧率", value: "" },
                      ...[24, 25, 30, 48, 50, 60, 120].map((f) => ({
                        label: `${f} fps`,
                        value: f,
                      })),
                    ]}
                  />
                  <small>增加帧率不会改变视频时长或播放速度。</small>
                </label>
              </div>
              {options.fps !== null && (
                <label className="enhancement-field">
                  帧率处理
                  <Select
                    value={options.frame_method}
                    style={{ width: "100%" }}
                    onChange={(value) =>
                      change(
                        "frame_method",
                        value as EnhanceOptions["frame_method"],
                      )
                    }
                    options={[
                      { label: "运动补帧 · 生成中间画面", value: "motion" },
                      { label: "快速重复帧 · 只调整帧率", value: "duplicate" },
                    ]}
                  />
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
                  <Select
                    value={options.quality}
                    style={{ width: "100%" }}
                    onChange={(value) =>
                      change(
                        "quality",
                        value as EnhanceOptions["quality"],
                      )
                    }
                    options={[
                      { label: "自动质量 · 推荐", value: "quality" },
                      { label: "指定目标码率", value: "bitrate" },
                    ]}
                  />
                </label>
                {options.quality === "bitrate" ? (
                  <label className="enhancement-field">
                    目标视频码率（Mbps）
                    <InputNumber
                      min={1}
                      max={150}
                      step={0.5}
                      style={{ width: "100%" }}
                      value={
                        Number.isFinite(options.bitrate_mbps)
                          ? options.bitrate_mbps
                          : undefined
                      }
                      onChange={(value) =>
                        change("bitrate_mbps", Number(value))
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
                  <Select
                    value={options.encoder}
                    style={{ width: "100%" }}
                    onChange={(value) =>
                      change(
                        "encoder",
                        value as EnhanceOptions["encoder"],
                      )
                    }
                    options={[
                      { label: "自动 · 优先硬件加速", value: "auto" },
                      { label: "软件编码 · H.264", value: "software" },
                    ]}
                  />
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
                  <InputNumber
                    min={0}
                    step={1}
                    max={
                      video.duration_ms
                        ? Math.max(0, video.duration_ms / 1000 - 0.1)
                        : undefined
                    }
                    style={{ width: "100%" }}
                    value={
                      Number.isFinite(options.preview_start)
                        ? options.preview_start
                        : undefined
                    }
                    onChange={(value) =>
                      change("preview_start", Number(value))
                    }
                  />
                </label>
                <Button
                  disabled={invalid || submit.isPending || !!previewBusy}
                  loading={!!previewBusy}
                  onClick={() => submit.mutate(true)}
                >
                  <Icon name="play" size={15} />
                  生成 5 秒预览
                </Button>
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
                <Button size="small" onClick={() => capabilities.refetch()}>
                  重试
                </Button>
              </p>
            )}
            {submit.isError && (
              <p className="inline-alert" role="alert">
                {submit.error.message}
              </p>
            )}
            {preview && !["queued", "running"].includes(preview.state) && (
              <section
                className="enhancement-preview-status"
                aria-label="增强预览"
              >
                <div className="enhancement-job-title">
                  <strong>预览 #{preview.id}</strong>
                  <span>
                    {states[preview.state]}
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
              <Button
                disabled={submit.isPending}
                onClick={() => setOpen(false)}
              >
                关闭
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={submit.isPending}
                disabled={invalid || submit.isPending}
              >
                开始完整增强
                <Icon name="arrow" size={16} />
              </Button>
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
              {job.message && !["queued", "running"].includes(job.state) && <p className="directory-help">{job.message}</p>}
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
