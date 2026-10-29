import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  subtitleApi,
  type ModelCatalog,
  type Provider,
  type SubtitleSettings,
} from "../api/subtitles";
import Icon from "../components/Icon";
import LocalDubbingModels from "../components/LocalDubbingModels";

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
  const [form, setForm] = useState<SubtitleSettings | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [pullModel, setPullModel] = useState("qwen2.5:3b");
  const [modelCategory, setModelCategory] = useState<"all" | "recognition" | "translation" | "speech">("all");
  const showModels = (category: typeof modelCategory) => modelCategory === "all" || modelCategory === category;
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
  const uninstall = useMutation({
    mutationFn: (body: { engine: string; model: string }) =>
      subtitleApi<{ message: string }>("/subtitle-models/uninstall", "POST", body),
    onSuccess: () => {
      setError("");
      setMessage("模型已卸载；使用前请重新下载或选择其他模型。已生成的字幕与配音不受影响。");
      void qc.invalidateQueries({ queryKey: ["subtitle-models"] });
    },
    onError: (e) => setError(e.message),
  });
  const uninstallModel = (engine: string, model: string, name: string) => {
    if (window.confirm(`卸载 ${name}？将删除模型文件，已有字幕与配音会保留。`)) {
      uninstall.mutate({ engine, model });
    }
  };
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
            用于 Whisper、轻量翻译和 Qwen3-TTS 语音配音模型下载。Ollama 的下载网络由 Ollama
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
      <section id="local-model-library" aria-labelledby="model-library-title">
        <div className="collection-heading">
          <div><h2 id="model-library-title">本地模型库</h2><span className="collection-count">按用途分类 · 按需下载</span></div>
          <Link className="btn-ghost" to="/jobs">任务中心 →</Link>
        </div>
        <div className="ai-model-filters" role="group" aria-label="本地模型分类">
          {([
            ["all", "全部"], ["recognition", "语音识别"],
            ["translation", "文本翻译"], ["speech", "语音配音"],
          ] as const).map(([key, label]) => <button key={key} type="button"
            aria-pressed={modelCategory === key} onClick={() => setModelCategory(key)}>{label}</button>)}
        </div>
        <p className="ai-hint">语音识别把音频转成原文，文本翻译转换文字语言，语音配音把文字合成为声音。</p>
        {dirty && <p className="inline-alert" role="status">AI 设置有未保存修改，请先保存，再下载模型，以使用最新的下载代理。</p>}
        {models.isLoading && <p role="status" className="ai-hint">正在读取本地模型库…</p>}
        {models.isError && <p className="inline-alert" role="alert">识别与翻译模型列表加载失败：{models.error.message}<button type="button" className="btn-ghost" onClick={() => models.refetch()}>重新加载</button></p>}

        <section className="ai-model-category" aria-labelledby="recognition-model-title" hidden={!showModels("recognition")}>
          <div className="ai-model-category-heading"><div><h3 id="recognition-model-title">语音识别</h3><p>Whisper · 生成视频字幕、提取参考音频原文。</p></div><span className="tiny-label">AUDIO → TEXT</span></div>
          {models.data && !models.data.asr_runtime && <p className="inline-alert">本地语音识别依赖尚未安装：在 backend 目录执行 uv sync --extra subtitles。</p>}
          <div className="ai-model-grid">
            {models.data?.whisper.map(m => <article className="card ai-model-card" key={m.id} aria-label={m.name}>
              <div className="ai-model-top"><span className="tiny-label">语音识别</span><span className={m.installed ? "text-emerald-700 text-xs" : "text-muted text-xs"}>{m.installed ? "已就绪 · " : ""}{formatModelSize(m)}</span></div>
              <h4>{m.name}</h4><p>{m.description}</p>
              <button type="button" className="btn-outline" disabled={m.installed || download.isPending || uninstall.isPending || dirty || !models.data.asr_runtime} onClick={() => download.mutate({ engine: "whisper", model: m.id })}>{m.installed ? "已下载" : "下载模型"}</button>
              {m.installed && <button type="button" className="btn-ghost text-red-700" disabled={uninstall.isPending || download.isPending} onClick={() => uninstallModel("whisper", m.id, m.name)}>卸载模型</button>}
            </article>)}
          </div>
        </section>

        <section className="ai-model-category" aria-labelledby="translation-model-title" hidden={!showModels("translation")}>
          <div className="ai-model-category-heading"><div><h3 id="translation-model-title">文本翻译</h3><p>NLLB 与 Ollama · 将字幕和配音稿翻译为目标语言。</p></div><span className="tiny-label">TEXT → TEXT</span></div>
          {models.data && !models.data.translation_runtime && <p className="inline-alert">本地翻译依赖尚未安装：在 backend 目录执行 uv sync --extra subtitles。</p>}
          <div className="ai-model-grid">
            {models.data?.translation?.map(m => <article className="card ai-model-card" key={m.id} aria-label={m.name}>
              <div className="ai-model-top"><span className="tiny-label">轻量离线翻译</span><span className={m.installed ? "text-emerald-700 text-xs" : "text-muted text-xs"}>{m.installed ? "已就绪 · " : ""}{formatModelSize(m)}</span></div>
              <h4>{m.name}</h4><p>{m.description}</p>
              <button type="button" className="btn-outline" disabled={m.installed || download.isPending || uninstall.isPending || dirty || !models.data.translation_runtime} onClick={() => download.mutate({ engine: "translation", model: m.id })}>{m.installed ? "已下载" : "下载轻量翻译模型"}</button>
              {m.installed && <button type="button" className="btn-ghost text-red-700" disabled={uninstall.isPending || download.isPending} onClick={() => uninstallModel("translation", m.id, m.name)}>卸载模型</button>}
            </article>)}
          </div>
          <h4>本地大模型翻译 · Ollama</h4>
          <p className="ai-hint">轻量专用翻译可先尝试 HY-MT1.5 1.8B；也可选择 Qwen 等通用模型对比效果。下列大小为估算下载大小，运行还需要额外内存。需安装并启动本机 Ollama；下载后点击「设为默认翻译模型」，再保存设置。更大模型的实际翻译效果请用同一段字幕对比。</p>
          {models.data?.ollama_error && <div className="inline-alert" role="alert">
            <p>{models.data.ollama_error}，因此暂时无法下载这些模型。</p>
            <p>请在运行后端的电脑上安装并启动 Ollama；已安装时可运行 <code>ollama serve</code>。若使用其他端口，请在上方选择「本地 Ollama」并保存正确地址。</p>
            <a className="text-link" href="https://ollama.com/download" target="_blank" rel="noreferrer">安装 Ollama →</a>
            <button type="button" className="btn-ghost" disabled={models.isFetching} onClick={() => models.refetch()}>{models.isFetching ? "正在检查…" : "重新检查连接"}</button>
          </div>}
          <div className="ai-model-grid">
            {models.data?.ollama.map(m => <article className="card ai-model-card" key={m.id} aria-label={m.name || m.id}>
              <div className="ai-model-top"><span className="tiny-label">Ollama · 本地翻译</span><span className="chip">{m.installed ? "已下载" : "未下载"}</span></div>
              <h4>{m.name || m.id}</h4>
              <p>{m.description || "本机已安装的 Ollama 模型，翻译能力取决于模型本身。"}</p>
              {m.size_mb != null && <p className="ai-hint">约 {(m.size_mb / 1000).toLocaleString("zh-CN")} GB · {m.hardware_hint}</p>}
              {m.source_url && <a className="text-link" href={m.source_url} target="_blank" rel="noreferrer">官方模型与使用说明 →</a>}
              <div className="ai-model-actions">
                {m.installed && <button type="button" className="btn-ghost text-red-700" disabled={uninstall.isPending || download.isPending} onClick={() => uninstallModel("ollama", m.id, m.name || m.id)}>卸载模型</button>}
                <button type="button" className="btn-outline" disabled={m.installed || download.isPending || uninstall.isPending || dirty || !!models.data.ollama_error} onClick={() => download.mutate({ engine: "ollama", model: m.id })}>{m.installed ? "已下载" : models.data.ollama_error ? "需启动 Ollama" : dirty ? "请先保存设置" : download.isPending ? "正在提交…" : "下载模型"}</button>
                {m.installed && <button type="button" className="btn-ghost" disabled={form.translation_provider === "ollama" && form.ollama_model === m.id} onClick={() => change({ translation_provider: "ollama", ollama_model: m.id })}>{form.translation_provider === "ollama" && form.ollama_model === m.id ? "当前默认模型" : "设为默认翻译模型"}</button>}
              </div>
            </article>)}
          </div>
          <section className="card ai-ollama-download">
            <div><h4>翻译模型 · Ollama</h4><p className="ai-hint">{models.data?.ollama_error || "已连接本机 Ollama。可下载推荐模型或填写模型名称。"}</p></div>
            <label>下载 Ollama 模型<input className="input" list="ollama-download-models" value={pullModel} onChange={e => setPullModel(e.target.value)} /></label>
            <datalist id="ollama-download-models">{models.data?.ollama.map(m => <option key={m.id} value={m.id}>{m.installed ? "已下载" : "可下载"}</option>)}</datalist>
            <button type="button" className="btn-outline" disabled={!pullModel.trim() || download.isPending || uninstall.isPending || dirty || !models.data || !!models.data.ollama_error} onClick={() => download.mutate({ engine: "ollama", model: pullModel.trim() })}>下载翻译模型</button>
          </section>
        </section>

        <section className="ai-model-category" aria-labelledby="speech-model-title" hidden={!showModels("speech")}>
          <div className="ai-model-category-heading"><div><h3 id="speech-model-title">语音配音</h3><p>Qwen3-TTS · 使用内置男女声，或录音、导入自己的参考音色。</p></div><span className="tiny-label">TEXT → AUDIO</span></div>
          <LocalDubbingModels library downloadDisabled={dirty || save.isPending} />
        </section>
      </section>

    </div>
  );
}
