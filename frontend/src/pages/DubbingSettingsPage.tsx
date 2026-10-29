import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Checkbox, Input, Select } from "@arco-design/web-react";
import { dubApi, usesReferenceVoices, type DubbingSettings, type Language, type Voice, type LocalDubbingCatalog } from "../api/dubbing";
import ReferenceVoices from "../components/ReferenceVoices";
import { Link } from "react-router-dom";
import "../components/dubbing.css";

export default function DubbingSettingsPage() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["dubbing-settings"], queryFn: () => dubApi<DubbingSettings>("/dubbing/settings") });
  const languages = useQuery({ queryKey: ["dubbing-languages"], queryFn: () => dubApi<Language[]>("/dubbing/languages") });
  const catalog = useQuery({ queryKey: ["dubbing-local-models"], queryFn: () => dubApi<LocalDubbingCatalog>("/dubbing/local-models") });
  const [form, setForm] = useState<DubbingSettings | null>(null);
  const [onlineProvider, setOnlineProvider] = useState<"edge" | "api" | "reference">("api");
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (query.data && !form) {
      setForm(query.data);
      if (query.data.provider !== "local") setOnlineProvider(query.data.provider);
    }
  }, [query.data, form]);
  const voices = useQuery({
    queryKey: ["dubbing-voices", form?.target_language, query.data?.provider, query.data?.local_model, query.data?.proxy],
    queryFn: () => dubApi<Voice[]>("/dubbing/voices?language=" + form!.target_language),
    enabled: !!form && form.provider === "edge" && query.data?.provider === "edge" && !dirty,
    retry: false, staleTime: 3600000,
  });
  const change = (patch: Partial<DubbingSettings>) => { setForm(v => v ? { ...v, ...patch } : v); setDirty(true); setMessage(""); };
  const save = useMutation({
    mutationFn: () => dubApi<DubbingSettings>("/dubbing/settings", "PUT", form),
    onSuccess: data => { setForm(data); setDirty(false); setMessage("配音设置已保存"); qc.setQueryData(["dubbing-settings"], data); void qc.invalidateQueries({ queryKey: ["dubbing-voices"] }); },
  });
  if (query.isError) return <p className="inline-alert" role="alert">{query.error.message}<Button size="small" onClick={() => query.refetch()}>重试</Button></p>;
  if (!form) return <p role="status">正在读取配音设置…</p>;
  const local = form.provider === "local";
  const reference = usesReferenceVoices(form);
  const providerField = form.provider === "reference" ? "reference_api" : "api";
  const provider = form[providerField];
  const languageOptions = (languages.data ?? [{ id: form.target_language, name: form.target_language }])
    .map(l => ({ label: l.name, value: l.id, disabled: local && !!catalog.data && !catalog.data.languages.includes(l.id.split("-")[0]) }));
  const localLanguageSupported = !local || !catalog.data || catalog.data.languages.includes(form.target_language.split("-")[0]);
  const providerModeOptions = [
    { label: "API Key · 兼容语音 API", value: "api" },
    { label: "API Key · 录音／导入参考音色", value: "reference" },
    { label: "Edge TTS · 保留原有免 Key 服务", value: "edge" },
  ];
  return <>
  <section className="card dubbing-settings">
    <h2>语言与语音配音</h2>
    <p className="ai-hint">在播放器底部点击「配音」，可生成并选择配音音轨；需要修正译文或男女声时，打开「检查译文与男女声」。识别与翻译沿用「AI 模型与服务」设置。</p>
    <form onSubmit={e => { e.preventDefault(); save.mutate(); }}>
      <div className="dubbing-fields">
        <label>配音运行模式<Select value={local ? "local" : "online"} style={{ width: "100%" }} onChange={value => change({ provider: value === "local" ? "local" : onlineProvider as DubbingSettings["provider"] })}
          options={[{ label: "API Key · 在线语音模型", value: "online" }, { label: "本地模型 · 下载后运行", value: "local" }]} /></label>
        {local ? <label>本地语音模型<Select value={form.local_model} style={{ width: "100%" }} onChange={value => change({ local_model: value as DubbingSettings["local_model"] })}
          options={[
            { label: "Qwen3-TTS 0.6B · 内置男女声", value: "qwen3-tts-0.6b-custom" },
            { label: "Qwen3-TTS 0.6B · 录音／导入参考音色", value: "qwen3-tts-0.6b-base" },
          ]} /></label> : <label>在线语音服务<Select value={form.provider} style={{ width: "100%" }} options={providerModeOptions}
          onChange={value => { const v = value as typeof onlineProvider; setOnlineProvider(v); change({ provider: v, male_voice: "", female_voice: "" }); }} /></label>}
        <label>默认目标语言<Select value={form.target_language} style={{ width: "100%" }} options={languageOptions}
          onChange={value => change({ target_language: value, male_voice: "", female_voice: "" })} /></label>
        {(["male", "female"] as const).map(gender => {
          const field = gender === "male" ? "male_voice" : "female_voice";
          if (reference) return null;
          const localField = gender === "male" ? "local_male_voice" : "local_female_voice";
          return <label key={gender}>{gender === "male" ? "男声音色" : "女声音色"}
          {local ? <Select value={form[localField]} style={{ width: "100%" }} showSearch
            onChange={value => change({ [localField]: value })}
            options={[
              ...(!catalog.data?.voices.some(v => v.id === form[localField]) ? [{ label: form[localField], value: form[localField] }] : []),
              ...(catalog.data?.voices.filter(v => v.gender === gender).map(v => ({ label: v.name, value: v.id })) ?? []),
            ]} /> : form.provider === "edge" ? <Select value={form[field]} style={{ width: "100%" }}
            onChange={value => change({ [field]: value })}
            options={[
              { label: `自动选择该语言的${gender === "male" ? "男声" : "女声"}`, value: "" },
              ...(!!form[field] && !voices.data?.some(v => v.id === form[field]) ? [{ label: form[field], value: form[field] }] : []),
              ...(voices.data?.filter(v => v.gender === gender).map(v => ({ label: v.id, value: v.id })) ?? []),
            ]} /> : <Input value={form[field]} allowClear placeholder={gender === "male" ? "onyx 或服务商提供的音色 ID" : "nova 或服务商提供的音色 ID"} onChange={value => change({ [field]: value })} />}
        </label>; })}
        <label>无法判断声线时使用<Select value={form.fallback_gender} style={{ width: "100%" }}
          onChange={value => change({ fallback_gender: value as "male" | "female" })}
          options={[{ label: "女声（译配稿中标记待确认）", value: "female" }, { label: "男声（译配稿中标记待确认）", value: "male" }]} /></label>
        {form.provider === "edge" && <label>在线语音代理（可选）<Input value={form.proxy} allowClear placeholder="http://127.0.0.1:7897" onChange={value => change({ proxy: value })} /></label>}
      </div>
      {(form.provider === "api" || form.provider === "reference") && <fieldset className="dubbing-fields">
        <legend>{reference ? "参考音色服务（SiliconFlow references 协议）" : "兼容 /audio/speech 的语音服务"}</legend>
        <label>API 地址<Input type="url" required value={provider.base_url} onChange={value => change({ [providerField]: { ...provider, base_url: value } })} /></label>
        <label>语音模型<Input required value={provider.model} onChange={value => change({ [providerField]: { ...provider, model: value } })} /></label>
        <label>API Key<Input type="password" autoComplete="new-password" value={provider.api_key ?? ""} placeholder={provider.has_api_key ? "已保存；留空保持原密钥" : "填写所选服务的 API Key"} onChange={value => change({ [providerField]: { ...provider, api_key: value || null } })} /></label>
        {provider.has_api_key && <Checkbox checked={provider.api_key === ""} onChange={checked => change({ [providerField]: { ...provider, api_key: checked ? "" : null } })}>清除原密钥</Checkbox>}
      </fieldset>}
      {reference && <p className="ai-hint">默认男声、女声分别在下方的<a href="#reference-voices">参考音频配置区</a>导入、试听和选择。</p>}
      {form.provider === "reference" && <p className="ai-hint">默认接入 SiliconFlow CosyVoice2，支持中文、英语、日语、韩语。需要所选服务的 API Key、音色权限与额度；普通语音 API 不一定支持参考音频协议。</p>}
      {local && <p className="ai-hint">先到<Link className="text-link" to="/settings/ai#local-model-library">AI 模型与服务 → 本地模型库</Link>下载所选模型。内置男女声可直接使用；参考音色模型使用下方录音或导入的样本。切换模式会保留已保存的 API 配置。</p>}
      {!localLanguageSupported && <p className="inline-alert" role="alert">本地模型暂不支持当前目标语言，请重新选择。</p>}
      <Checkbox checked={form.keep_original} onChange={checked => change({ keep_original: checked })}>成片中附加原声音轨，配音轨作为默认音轨</Checkbox>
      <p className="ai-hint">自动声线分配根据音高估计，童声、音乐与重叠对白可能误判，可逐句修正。配音轨只含生成语音，原对白与音乐保留在可选的原声音轨中。</p>
      <p className="ai-privacy-note">{local ? "本地语音合成不上传译文或参考音色，也不使用 API Key。识别与翻译是独立设置；若要全程本地处理，请同时将它们设为本地模型。" : "在线语音会发送译文到所选服务；参考音色服务还会发送音频样本与原文。合成试听可能计费，原视频文件不会发送到语音合成服务。识别与翻译仍沿用各自设置。"}</p>
      {dirty && form.provider === "edge" && <p className="ai-hint">保存设置后加载该语言的在线音色列表。</p>}
      {voices.isFetching && <p role="status" className="ai-hint">正在加载音色…</p>}
      {voices.isError && !local && <p className="inline-alert" role="alert">{voices.error.message}<Button size="small" onClick={() => voices.refetch()}>重试</Button></p>}
      {catalog.isError && local && <p className="inline-alert" role="alert">{catalog.error.message}</p>}
      {save.isError && <p className="inline-alert" role="alert">{save.error.message}</p>}
      {message && <p role="status">{message}</p>}
      <Button type="primary" htmlType="submit" loading={save.isPending} disabled={!localLanguageSupported}>保存配音设置</Button>
    </form>
  </section>
  <ReferenceVoices settings={form} saved={query.data!} dirty={dirty || save.isPending} onBind={(gender, id) => change({ [gender === "male" ? "male_reference" : "female_reference"]: id })} />
  {dirty && <div className="dubbing-actions"><Button type="primary" loading={save.isPending} disabled={!localLanguageSupported} onClick={() => save.mutate()}>保存男女声选择及配音设置</Button><span>音色选择尚未保存。</span>{save.isError && <p role="alert">{save.error.message}</p>}</div>}
  </>;
}
