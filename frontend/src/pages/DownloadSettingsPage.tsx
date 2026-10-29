import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Input, Select, Spin } from "@arco-design/web-react";
import { downloadSettings, downloadStatus, saveDownloadSettings, checkDownloadCookies, type DownloadConfig } from "../api/downloads";
import "../components/downloads.css";

const BROWSER_OPTIONS = [
  { label: "不读取浏览器 Cookie", value: "" },
  { label: "Chrome", value: "chrome" },
  { label: "Chromium", value: "chromium" },
  { label: "Firefox", value: "firefox" },
  { label: "Edge", value: "edge" },
  { label: "Brave", value: "brave" },
];
const KEYRING_OPTIONS = [
  { label: "自动识别桌面环境", value: "" },
  { label: "GNOME Keyring / Secret Service", value: "gnomekeyring" },
  { label: "KWallet 6", value: "kwallet6" },
  { label: "KWallet 5", value: "kwallet5" },
  { label: "KWallet 4", value: "kwallet" },
  { label: "Basic text", value: "basictext" },
];

export default function DownloadSettingsPage() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["download-settings"], queryFn: downloadSettings });
  const engine = useQuery({ queryKey: ["download-status"], queryFn: downloadStatus });
  const [draft, setDraft] = useState<DownloadConfig | null>(null);
  const [saved, setSaved] = useState(false);
  const form = draft ?? query.data;
  const save = useMutation({
    mutationFn: () => saveDownloadSettings(form!),
    onSuccess: data => { qc.setQueryData(["download-settings"], data); setDraft(null); setSaved(true); },
  });
  const check = useMutation({ mutationFn: () => checkDownloadCookies(form!) });
  const change = (patch: Partial<DownloadConfig>) => { setDraft({ ...form!, ...patch }); setSaved(false); save.reset(); check.reset(); };
  return <div className="page content-page download-settings-page">
    <div className="page-heading"><div><p className="eyebrow"><span /> DOWNLOAD PREFERENCES</p>
      <h1>让下载，顺畅一点。</h1><p className="page-intro">为视频站点配置网络与登录信息。</p></div>
      <Link className="btn-outline" to="/downloads">返回链接下载</Link></div>
    <nav className="download-settings-tabs" aria-label="设置分类">
      <Link to="/settings">字幕与翻译</Link><Link to="/settings/playback">播放与轮播</Link><Link to="/settings/ai">AI 模型与服务</Link><Link to="/settings/downloads" aria-current="page">链接下载</Link><Link to="/settings/devices">信任设备</Link>
    </nav>
    {query.isError && <Alert type="error" content={query.error.message} action={<Button size="mini" onClick={() => query.refetch()}>重试</Button>} />}
    {!form && !query.isError && <Spin dot tip="正在读取下载设置…" />}
    {form && <form className="card download-settings-card" onSubmit={e => { e.preventDefault(); save.mutate(); }}>
      <h2>网络与登录</h2><p className="download-hint">设置用于之后新建或重试的下载任务。</p>
      <label className="download-field">下载代理（可选）
        <Input value={form.proxy} placeholder="http://127.0.0.1:7897" allowClear
          onChange={value => change({ proxy: value })} /></label>
      <p className="download-hint">留空时沿用后端运行环境的网络配置。这里的 127.0.0.1 指运行项目的电脑。</p>
      <label className="download-field">浏览器登录信息
        <Select value={form.cookies_browser} options={BROWSER_OPTIONS} style={{ width: "100%" }}
          onChange={value => change({ cookies_browser: value as DownloadConfig["cookies_browser"], cookie_file: "" })} /></label>
      <p className="download-hint">选择后，下载引擎会读取后端电脑上该浏览器的 Cookie，并按站点使用。请先在该浏览器登录目标站点；远程访问时不会读取当前设备的浏览器。</p>
      {form.cookies_browser && <>
        <label className="download-field">浏览器配置目录（可选）
          <Input value={form.browser_profile ?? ""} placeholder="Default、Profile 1 或完整目录路径" allowClear
            onChange={value => change({ browser_profile: value })} /></label>
        <p className="download-hint">多账号浏览器可指定登录抖音的配置目录；留空由下载引擎自动选择。</p>
        {form.cookies_browser !== "firefox" && <label className="download-field">Linux Cookie 密钥环
          <Select value={form.browser_keyring ?? ""} options={KEYRING_OPTIONS} style={{ width: "100%" }}
            onChange={value => change({ browser_keyring: value as DownloadConfig["browser_keyring"] })} /></label>}
        <p className="download-hint">仅用于 Linux。浏览器实际使用的密钥环可能与当前桌面不同；检测提示解密失败时可明确选择。</p>
      </>}
      <label className="download-field">或使用 cookies.txt 文件
        <Input value={form.cookie_file} disabled={!!form.cookies_browser} allowClear
          placeholder="/home/leo/Downloads/cookies.txt" onChange={value => change({ cookie_file: value })} /></label>
      <p className="download-hint">填写后端电脑上的 Netscape 格式 Cookie 文件路径。Cookie 内容不会返回到网页或写入任务记录。</p>
      <Button disabled={check.isPending || (!form.cookies_browser && !form.cookie_file)}
        loading={check.isPending} onClick={() => check.mutate()}>检测 Cookie</Button>
      {check.isError && <p className="download-error" role="alert">{check.error.message}</p>}
      {check.data && <div role="status">
        <p className={check.data.ok ? "download-success" : "download-error"}>{check.data.message}</p>
        <p className="download-hint">{Object.entries(check.data.sites).map(([site, count]) => site + "：" + count + " 条").join(" · ")}</p>
      </div>}
      {save.isError && <p className="download-error" role="alert">{save.error.message}</p>}
      {saved && <p className="download-success" role="status">下载设置已保存</p>}
      <div className="download-submit"><span>抖音、B站或 YouTube 可能需要有效登录信息</span>
        <Button type="primary" htmlType="submit" loading={save.isPending}>保存设置</Button></div>
    </form>}
    <section className="card download-settings-card"><h2>下载环境</h2>
      {engine.data && <dl className="download-environment"><div><dt>yt-dlp</dt><dd>{engine.data.version ?? "未安装"}</dd></div>
        <div><dt>音视频合并</dt><dd>{engine.data.ffmpeg ? "FFmpeg 已就绪" : "FFmpeg 未安装"}</dd></div>
        <div><dt>YouTube 脚本运行环境</dt><dd>{engine.data.javascript ? "已就绪" : "未检测到，部分 YouTube 视频可能无法下载"}</dd></div>
        <div><dt>默认保存目录</dt><dd>{engine.data.default_directory}</dd></div></dl>}
      {engine.isError && <p className="download-error">无法读取下载环境：{engine.error.message}</p>}
      <p className="download-hint">更多站点由 yt-dlp 提供支持。站点规则变化、地区或账号权限限制可能影响下载；不支持 DRM 视频。</p>
    </section>
  </div>;
}
