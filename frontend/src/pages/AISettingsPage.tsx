import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  subtitleApi,
  type ModelCatalog,
  type Provider,
  type SubtitleSettings,
} from "../api/subtitles";
import type { Job } from "../api/types";
import Icon from "../components/Icon";
import TaskProgress from "../components/TaskProgress";

function formatModelSize(model: {
  size_mb: number;
  size_bytes?: number | null;
}) {
  if (model.size_bytes == null) return `约 ${model.size_mb} MB`;
  const isGB = model.size_bytes >= 1_000_000_000;
  const size = model.size_bytes / (isGB ? 1_000_000_000 : 1_000_000);
  return `${size.toLocaleString("zh-CN", { maximumFractionDigits: 1 })} ${
    isGB ? "GB" : "MB"
  }`;
}

function ProviderFields({
  title,
  value,
  onChange,
}: {
  title: string;
  value: Provider;
  onChange: (p: Provider) => void;
}) {
  return (
    <fieldset className="ai-provider-fields">
      <legend>{title}</legend>
      <label>
        API 地址（Base URL）
        <input
          className="input"
          type="url"
          required
          value={value.base_url}
          onChange={(e) => onChange({ ...value, base_url: e.target.value })}
          placeholder="https://api.example.com/v1"
        />
      </label>
      <label>
        模型名称
        <input
          className="input"
          required
          value={value.model}
          onChange={(e) => onChange({ ...value, model: e.target.value })}
          placeholder="填写服务商提供的模型 ID"
        />
      </label>
      <label>
        API Key
        <input
          className="input"
          type="password"
          autoComplete="new-password"
          value={value.api_key ?? ""}
          onChange={(e) =>
            onChange({ ...value, api_key: e.target.value || null })
          }
          placeholder={
            value.has_api_key
              ? "已保存密钥；留空保持不变"
              : "填写密钥（本机服务可留空）"
          }
        />
      </label>
      {value.has_api_key && (
        <label className="ai-checkbox">
          <input
            type="checkbox"
            checked={value.api_key === ""}
            onChange={(e) =>
              onChange({ ...value, api_key: e.target.checked ? "" : null })
            }
          />
          清除已保存的密钥
        </label>
      )}
    </fieldset>
  );
}

export default function AISettingsPage({ embedded = false }: { embedded?: boolean }) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["subtitle-settings"],
    queryFn: () => subtitleApi<SubtitleSettings>("/subtitle-settings"),
  });
  const models = useQuery({
    queryKey: ["subtitle-models"],
    queryFn: () => subtitleApi<ModelCatalog>("/subtitle-models"),
    refetchInterval: 5000,
  });
  const jobs = useQuery({
    queryKey: ["subtitle-downloads"],
    queryFn: () =>
      subtitleApi<{ items: Job[] }>(
        "/jobs?kind=subtitle_model_download&limit=10",
      ),
    refetchInterval: 2000,
  });
  const [form, setForm] = useState<SubtitleSettings | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [pullModel, setPullModel] = useState("qwen2.5:3b");
  useEffect(() => {
    if (query.data && !form) setForm(query.data);
  }, [query.data, form]);
  const change = (patch: Partial<SubtitleSettings>) => {
    setForm((v) => (v ? { ...v, ...patch } : v));
    setDirty(true);
    setMessage("");
  };
  const save = useMutation({
    mutationFn: () =>
      subtitleApi<SubtitleSettings>("/subtitle-settings", "PUT", form),
    onSuccess: (data) => {
      setForm(data);
      setDirty(false);
      setError("");
      setMessage("设置已保存");
      qc.setQueryData(["subtitle-settings"], data);
      qc.invalidateQueries({ queryKey: ["subtitle-models"] });
    },
    onError: (e) => setError(e.message),
  });
  const test = useMutation({
    mutationFn: (provider: string) =>
      subtitleApi<{ message: string; models: string[] }>(
        `/subtitle-settings/test/${provider}`,
        "POST",
      ),
    onSuccess: (data) => {
      setError("");
      setMessage(
        `${data.message}${
          data.models.length
            ? "。可用模型：" + data.models.slice(0, 8).join("、")
            : ""
        }`,
      );
    },
    onError: (e) => setError(e.message),
  });
  const download = useMutation({
    mutationFn: (body: { engine: string; model: string }) =>
      subtitleApi<{ job_id: number }>(
        "/subtitle-models/download",
        "POST",
        body,
      ),
    onSuccess: (data) => {
      setError("");
      setMessage(`下载任务 #${data.job_id} 已加入队列`);
      qc.invalidateQueries({ queryKey: ["subtitle-downloads"] });
    },
    onError: (e) => setError(e.message),
  });
  const cancel = useMutation({
    mutationFn: (id: number) => subtitleApi(`/jobs/${id}/cancel`, "POST"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["subtitle-downloads"] }),
    onError: (e) => setError(e.message),
  });
  if (query.isError)
    return (
      <div className="page">
        <div className="inline-alert" role="alert">
          {query.error.message}
        </div>
        <button className="btn-outline" onClick={() => query.refetch()}>
          重新加载
        </button>
      </div>
    );
  if (!form) return <div className="page loading-panel">正在读取 AI 设置…</div>;
  return (
    <div className={embedded ? "ai-settings-page" : "page content-page ai-settings-page"}>
      {!embedded && <div className="page-heading">
        <div>
          <p className="eyebrow">
            <span /> LANGUAGE & INTELLIGENCE
          </p>
          <h1>让每一帧，都能被读懂。</h1>
          <p className="page-intro">
            选择识别与翻译引擎，把影像里的声音，变成随时可读的文字。
          </p>
        </div>
        <span className="chip ai-studio-badge">字幕工作台</span>
      </div>}
      <div className="ai-privacy-note">
        <Icon name="storage" size={18} />
        <p>
          本地模型在电脑上处理。使用语音 API 会发送音频片段，翻译 API
          会发送字幕文字；密钥仅保存在本机后端。
        </p>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="ai-settings-grid">
          <section className="card ai-setting-card">
            <div className="ai-section-heading">
              <span>01</span>
              <div>
                <h2>语音 → 字幕</h2>
                <p>自动识别语言，生成带时间轴的字幕。</p>
              </div>
            </div>
            <label>
              处理方式
              <select
                className="input"
                value={form.asr_provider}
                onChange={(e) =>
                  change({ asr_provider: e.target.value as "local" | "api" })
                }
              >
                <option value="local">本地 Whisper</option>
                <option value="api">第三方语音 API</option>
              </select>
            </label>
            {form.asr_provider === "local" ? (
              <>
                <label>
                  默认语音模型
                  <select
                    className="input"
                    value={form.asr_model}
                    onChange={(e) => change({ asr_model: e.target.value })}
                  >
                    {(
                      models.data?.whisper ?? [
                        {
                          id: form.asr_model,
                          name: form.asr_model,
                          installed: false,
                        },
                      ]
                    ).map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                        {m.installed ? " · 已下载" : " · 需下载"}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="ai-hint">
                  使用 CPU / INT8 运行，字幕任务串行处理。首次使用请先下载模型。
                </p>
              </>
            ) : (
              <>
                <ProviderFields
                  title="语音服务"
                  value={form.asr_api}
                  onChange={(value) => change({ asr_api: value })}
                />
                <p className="ai-hint">
                  需要兼容 /audio/transcriptions 并返回分段时间戳（verbose_json
                  / segments），例如 whisper-1。普通聊天模型不能用于语音识别。
                </p>
                <button
                  type="button"
                  className="btn-outline"
                  disabled={dirty || test.isPending}
                  onClick={() => test.mutate("asr_api")}
                >
                  检测语音 API
                </button>
              </>
            )}
          </section>
          <section className="card ai-setting-card">
            <div className="ai-section-heading">
              <span>02</span>
              <div>
                <h2>字幕 → 另一种语言</h2>
                <p>保留原文时间轴，独立保存每份译文。</p>
              </div>
            </div>
            <label>
              处理方式
              <select
                className="input"
                value={form.translation_provider}
                onChange={(e) =>
                  change({
                    translation_provider: e.target
                      .value as SubtitleSettings["translation_provider"],
                  })
                }
              >
                <option value="local">本地轻量翻译模型</option>
                <option value="ollama">本地 Ollama</option>
                <option value="api">第三方翻译 / 聊天 API</option>
              </select>
            </label>
            {form.translation_provider === "local" ? (
              <>
                <label>
                  默认翻译模型
                  <select
                    className="input"
                    value={form.local_translation_model}
                    onChange={(e) =>
                      change({ local_translation_model: e.target.value })
                    }
                  >
                    {(
                      models.data?.translation ?? [
                        {
                          id: "nllb-600m-int8",
                          name: "NLLB-200 600M · INT8",
                          installed: false,
                        },
                      ]
                    ).map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                        {m.installed ? " · 已下载" : " · 需下载"}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="ai-hint">
                  约 630 MB，下载一次即可离线翻译。无需 Ollama 或 API
                  Key，保留字幕时间轴。
                </p>
              </>
            ) : form.translation_provider === "ollama" ? (
              <>
                <label>
                  Ollama 地址
                  <input
                    className="input"
                    type="url"
                    required
                    value={form.ollama_url}
                    onChange={(e) => change({ ollama_url: e.target.value })}
                  />
                </label>
                <label>
                  默认翻译模型
                  <input
                    className="input"
                    required
                    list="ollama-installed"
                    value={form.ollama_model}
                    onChange={(e) => change({ ollama_model: e.target.value })}
                  />
                  <datalist id="ollama-installed">
                    {models.data?.ollama.map((m) => (
                      <option key={m.id} value={m.id} />
                    ))}
                  </datalist>
                </label>
                <p className="ai-hint">
                  需先安装并启动本机 Ollama，再从下方下载模型。
                </p>
                <button
                  type="button"
                  className="btn-outline"
                  disabled={dirty || test.isPending}
                  onClick={() => test.mutate("ollama")}
                >
                  检测 Ollama
                </button>
              </>
            ) : (
              <>
                <ProviderFields
                  title="OpenAI 兼容服务"
                  value={form.translation_api}
                  onChange={(value) => change({ translation_api: value })}
                />
                <p className="ai-hint">
                  支持 OpenAI、DeepSeek，以及提供 /chat/completions
                  接口的服务。填写服务商实际模型 ID。
                </p>
                <button
                  type="button"
                  className="btn-outline"
                  disabled={dirty || test.isPending}
                  onClick={() => test.mutate("translation_api")}
                >
                  检测翻译 API
                </button>
              </>
            )}
          </section>
        </div>
        <label className="ai-proxy-field">
          模型下载代理（可选）
          <input
            className="input"
            type="url"
            value={form.model_download_proxy || ""}
            placeholder="例如 http://127.0.0.1:7897；留空使用系统网络"
            onChange={(e) => change({ model_download_proxy: e.target.value })}
          />
          <span className="ai-hint">
            用于 Whisper 和轻量翻译模型下载。Ollama 的下载网络由 Ollama
            服务自身管理。
          </span>
        </label>
        <div className="ai-save-row">
          <span className="text-sm text-muted">
            {dirty
              ? "有未保存的修改。检测连接前请先保存。"
              : "设置会用于后续发起的字幕任务。"}
          </span>
          <button className="btn-primary" disabled={save.isPending}>
            {save.isPending ? "正在保存…" : "保存 AI 设置"}
          </button>
        </div>
      </form>
      {error && (
        <p className="inline-alert" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="ai-success" role="status">
          {message}
        </p>
      )}
      <div className="collection-heading">
        <div>
          <h2>本地模型库</h2>
          <span className="collection-count">按需下载</span>
        </div>
        <Link className="btn-ghost" to="/jobs">
          任务中心 →
        </Link>
      </div>
      {models.isError && (
        <p className="inline-alert" role="alert">
          模型列表加载失败：{models.error.message}
        </p>
      )}
      {models.data && !models.data.asr_runtime && (
        <p className="inline-alert">
          本地语音依赖尚未安装：在 backend 目录执行 uv sync --extra subtitles。
        </p>
      )}
      {models.data && !models.data.translation_runtime && (
        <p className="inline-alert">
          本地翻译依赖尚未安装：在 backend 目录执行 uv sync --extra subtitles。
        </p>
      )}
      <div className="ai-model-grid">
        {models.data?.translation?.map((m) => (
          <article className="card ai-model-card" key={m.id}>
            <div className="ai-model-top">
              <span className="tiny-label">OFFLINE TRANSLATION</span>
              <span
                className={
                  m.installed
                    ? "text-emerald-700 text-xs"
                    : "text-muted text-xs"
                }
              >
                {m.installed ? "已就绪 · " : ""}
                {formatModelSize(m)}
              </span>
            </div>
            <h3>{m.name}</h3>
            <p>{m.description}</p>
            <button
              className="btn-outline"
              disabled={
                m.installed ||
                download.isPending ||
                dirty ||
                !models.data.translation_runtime
              }
              onClick={() =>
                download.mutate({ engine: "translation", model: m.id })
              }
            >
              {m.installed ? "已下载" : "下载轻量翻译模型"}
            </button>
          </article>
        ))}
        {models.data?.whisper.map((m) => (
          <article className="card ai-model-card" key={m.id}>
            <div className="ai-model-top">
              <span className="tiny-label">SPEECH RECOGNITION</span>
              <span
                className={
                  m.installed
                    ? "text-emerald-700 text-xs"
                    : "text-muted text-xs"
                }
              >
                {m.installed ? "已就绪 · " : ""}
                {formatModelSize(m)}
              </span>
            </div>
            <h3>{m.name}</h3>
            <p>{m.description}</p>
            <button
              className="btn-outline"
              disabled={
                m.installed || download.isPending || !models.data.asr_runtime
              }
              onClick={() =>
                download.mutate({ engine: "whisper", model: m.id })
              }
            >
              {m.installed ? "已下载" : "下载模型"}
            </button>
          </article>
        ))}
      </div>
      <section className="card ai-ollama-download">
        <div>
          <h3>翻译模型 · Ollama</h3>
          <p className="ai-hint">
            {models.data?.ollama_error ||
              "已连接本机 Ollama。可下载推荐模型或填写模型名称。"}
          </p>
        </div>
        <label>
          下载模型
          <input
            className="input"
            list="ollama-installed"
            value={pullModel}
            onChange={(e) => setPullModel(e.target.value)}
          />
        </label>
        <button
          className="btn-outline"
          disabled={
            !pullModel.trim() ||
            download.isPending ||
            dirty ||
            !!models.data?.ollama_error
          }
          onClick={() =>
            download.mutate({ engine: "ollama", model: pullModel.trim() })
          }
        >
          下载翻译模型
        </button>
      </section>
      {(jobs.data?.items ?? []).map((job) => (
        <div className="card ai-download-job" key={job.id}>
          <span>
            #{job.id} ·{" "}
            {job.message ||
              (job.state === "queued"
                ? "等待下载"
                : job.state === "done"
                ? "下载完成"
                : job.state === "canceled"
                ? "已取消"
                : "下载任务")}
          </span>
          <TaskProgress state={job.state} progress={job.progress} createdAt={job.created_at}
            startedAt={job.started_at} finishedAt={job.finished_at} label={`模型下载任务 ${job.id}`} compact />
          {["queued", "running"].includes(job.state) && (
            <button
              className="btn-ghost"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate(job.id)}
            >
              取消
            </button>
          )}
          {job.error && <p className="text-red-700 w-full">{job.error}</p>}
        </div>
      ))}
    </div>
  );
}
