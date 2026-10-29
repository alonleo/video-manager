import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Modal, Radio } from "@arco-design/web-react";
import { dubApi, type DubbingSettings, type LocalDubbingCatalog } from "../api/dubbing";
import type { Job } from "../api/types";

export const modelSize = (size: number) => (size / 1024 ** 3).toFixed(2) + " GiB";
export default function LocalDubbingModels({ library = false, downloadDisabled = false }: {
  library?: boolean;
  downloadDisabled?: boolean;
}) {
  const qc = useQueryClient();
  const [message, setMessage] = useState("");
  const [voiceKind, setVoiceKind] = useState<"all" | "builtin" | "reference">("all");
  const catalog = useQuery({ queryKey: ["dubbing-local-models"], queryFn: () => dubApi<LocalDubbingCatalog>("/dubbing/local-models"), refetchInterval: 5000 });
  const jobs = useQuery({ queryKey: ["dubbing-model-jobs"], queryFn: () => dubApi<{ items: (Job & { model: string })[] }>("/dubbing/local-models/jobs"), refetchInterval: 2000 });
  const download = useMutation({
    mutationFn: (model: DubbingSettings["local_model"]) => dubApi("/dubbing/local-models/download", "POST", { model }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["dubbing-model-jobs"] }); },
  });
  const uninstall = useMutation({
    mutationFn: (model: DubbingSettings["local_model"]) => dubApi("/dubbing/local-models/uninstall", "POST", { model }),
    onSuccess: () => {
      setMessage("模型已卸载，已有配音和参考音色会保留。使用前请重新下载或选择其他模型。");
      void qc.invalidateQueries({ queryKey: ["dubbing-local-models"] });
    },
  });
  const cancel = useMutation({
    mutationFn: (id: number) => dubApi("/jobs/" + id + "/cancel", "POST"),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["dubbing-model-jobs"] }); },
  });
  return <section className={library ? "ai-speech-models" : "card dubbing-settings"}>
    {!library && <h2>本地语音模型</h2>}
    <p className="ai-hint">首次下载后在服务器本机运行，无需语音 API Key。支持中文、英语、日语、韩语、德语、法语、俄语、葡萄牙语、西班牙语、意大利语。当前使用 CPU，合成速度取决于硬件和文字长度。</p>
    {catalog.data && !catalog.data.runtime_ready && <p className="inline-alert" role="alert">本地语音运行环境尚未安装，下载和使用模型前请先运行 scripts/setup-local-tts.sh。</p>}
    {(catalog.isError || jobs.isError || download.isError || cancel.isError || uninstall.isError) && <p className="inline-alert" role="alert">{catalog.error?.message || jobs.error?.message || download.error?.message || cancel.error?.message || uninstall.error?.message}<Button size="small" onClick={() => { void catalog.refetch(); void jobs.refetch(); }}>刷新模型与任务</Button></p>}
    {message && <p role="status" className="ai-success">{message}</p>}
    {catalog.isLoading && <p role="status">正在检查本地模型…</p>}
    {library && <div className="ai-model-filters" role="group" aria-label="语音模型音色分类">
      <Radio.Group type="button" value={voiceKind} onChange={(value) => setVoiceKind(value)}>
        <Radio value="all">全部音色</Radio>
        <Radio value="builtin">内置音色</Radio>
        <Radio value="reference">参考音色</Radio>
      </Radio.Group>
    </div>}
    <div className={library ? "ai-model-grid ai-speech-model-grid" : "reference-list"}>{catalog.data?.models.filter(model => !library || voiceKind === "all" || model.kind === voiceKind).map(model => {
      const latest = jobs.data?.items.find(job => job.model === model.id);
      const active = latest && ["queued", "running"].includes(latest.state);
      const status = model.installed ? (catalog.data.runtime_ready ? "已就绪" : "已下载 · 缺少运行环境") : active ? (latest.state === "queued" ? "等待下载" : "下载中") : "未下载";
      return <article className={library ? "card ai-model-card" : "reference-card"} key={model.id} aria-label={model.name}>
        {library ? <>
          <div className="ai-model-top"><span className="tiny-label">{model.kind === "builtin" ? "内置男女声" : "录音／导入参考音色"}</span><span className="chip">{status}</span></div>
          <h4>{model.name}</h4>
        </> : <div className="dubbing-cue-meta"><strong>{model.name}</strong><span className="chip">{status}</span></div>}
        <p className="ai-hint">{model.kind === "builtin" ? "直接选择内置男声、女声，不需要录音样本。" : "使用已录制或导入的男声、女声样本合成目标语言。"} 模型大小：{modelSize(model.size_bytes ?? model.download_bytes)}。</p>
        {latest && <>{latest.error && <p className="inline-alert" role="alert">{latest.error}</p>}</>}
        <div className={library ? "ai-model-actions" : "dubbing-actions"}>{active ? <Button disabled={cancel.isPending} onClick={() => cancel.mutate(latest.id)}>取消下载</Button> : <Button disabled={model.installed || !catalog.data.runtime_ready || download.isPending || uninstall.isPending || downloadDisabled || jobs.isPending || jobs.isError} onClick={() => download.mutate(model.id)}>{model.installed ? "模型已安装" : latest ? "继续下载／重试" : "下载模型"}</Button>}
          {model.installed && <Button className="text-red-700" disabled={uninstall.isPending || download.isPending || !!active} onClick={() => {
            Modal.confirm({
              title: `卸载 ${model.name}？`,
              content: "将删除模型文件，已有配音和参考音色会保留。",
              okText: "卸载",
              cancelText: "取消",
              onOk: () => uninstall.mutate(model.id),
            });
          }}>卸载模型</Button>}
        </div>
      </article>;
    })}</div>
    <p className="ai-hint">下载在后台继续，支持取消与续传，完成后会通知。网络代理沿用「AI 模型与服务」中的模型下载代理。{library && <> <Link className="text-link" to="/settings/dubbing">设置配音语言与男女声音色 →</Link></>}</p>
  </section>;
}
