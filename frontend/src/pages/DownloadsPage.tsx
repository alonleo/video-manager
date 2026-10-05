import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listCategories } from "../api/client";
import { downloadStatus, listDownloads, startDownload } from "../api/downloads";
import Icon from "../components/Icon";
import DownloadActions from "../components/DownloadActions";
import TaskProgress from "../components/TaskProgress";
import { formatRelative } from "../lib/format";
import "../components/downloads.css";

const states = { queued: "排队中", running: "下载中", done: "已入库", error: "需要重试", canceled: "已取消" };

export default function DownloadsPage() {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [quality, setQuality] = useState("1080");
  const [container, setContainer] = useState("auto");
  const [category, setCategory] = useState("");
  const [message, setMessage] = useState("");
  const [filter, setFilter] = useState("all");
  const engine = useQuery({ queryKey: ["download-status"], queryFn: downloadStatus });
  const categories = useQuery({ queryKey: ["categories", "video"], queryFn: () => listCategories("video") });
  const jobs = useQuery({ queryKey: ["downloads"], queryFn: listDownloads,
    refetchInterval: (q) => q.state.data?.items.some(j => ["running", "queued"].includes(j.state)) ? 1500 : 8000 });
  const submit = useMutation({
    mutationFn: () => {
      const selected = categories.data?.items.find(c => c.id === category);
      if (category && !selected) throw new Error("保存分类已失效，请重新选择");
      return startDownload({ url, quality, container, storage_id: selected?.storage_id ?? null, path: selected?.path ?? "" });
    },
    onSuccess: (job) => {
      setMessage("下载任务 #" + job.id + " 已加入队列，完成后会自动加入影像库。");
      setUrl("");
      for (const key of ["downloads", "jobs", "storages", "categories"]) void qc.invalidateQueries({ queryKey: [key] });
    },
  });
  const items = jobs.data?.items ?? [];
  const active = items.filter(j => ["running", "queued"].includes(j.state)).length;
  const visible = items.filter(j => filter === "all" || (filter === "active" ? ["running", "queued"].includes(j.state) : j.state === filter));
  const ready = engine.data?.installed && engine.data.ffmpeg;
  return <div className="page content-page downloads-page">
    <div className="page-heading">
      <div><p className="eyebrow"><span /> COLLECT FROM ANYWHERE</p><h1>一个链接，珍藏一段影像。</h1>
        <p className="page-intro">从喜欢的视频开始，让精彩在你的影像库中安放。</p></div>
      <Link className="btn-outline" to="/settings/downloads"><Icon name="storage" size={16} />下载设置</Link>
    </div>
    <div className="download-layout">
      <section className="card download-composer" aria-labelledby="new-download">
        <div className="download-section-title"><span className="download-step">01</span>
          <div><h2 id="new-download">添加视频链接</h2><p>粘贴播放页链接，也可以直接粘贴抖音分享文本。</p></div></div>
        <div className="download-platforms"><span>抖音</span><span>哔哩哔哩</span><span>YouTube</span><span>更多站点 ↗</span></div>
        <form onSubmit={e => { e.preventDefault(); setMessage(""); submit.mutate(); }}>
          <label className="download-field">视频链接
            <textarea className="input download-url" required maxLength={8192} rows={4} value={url}
              onChange={e => { setUrl(e.target.value); submit.reset(); setMessage(""); }}
              placeholder="https://www.bilibili.com/video/…&#10;或粘贴含视频链接的分享文本" /></label>
          <div className="download-options">
            <label className="download-field">清晰度上限
              <select className="input" value={quality} onChange={e => setQuality(e.target.value)}>
                <option value="best">最佳可用</option><option value="2160">4K · 2160p</option>
                <option value="1440">2K · 1440p</option><option value="1080">全高清 · 1080p</option>
                <option value="720">高清 · 720p</option><option value="480">流畅 · 480p</option>
              </select></label>
            <label className="download-field">文件格式
              <select className="input" value={container} onChange={e => setContainer(e.target.value)}>
                <option value="auto">自动 · 保留原始编码</option><option value="mp4">MP4</option><option value="mkv">MKV</option>
              </select></label>
          </div>
          <label className="download-field">保存到分类
            <select className="input" value={category} onChange={e => setCategory(e.target.value)}>
              <option value="">默认下载目录 · 自动加入影像库</option>
              {categories.data?.items.filter(c => c.available).map(c =>
                <option key={c.id} value={c.id}>{c.storage_name} / {c.path || "根目录"}</option>)}
            </select></label>
          {categories.isError && <p className="download-hint">分类暂时无法读取，仍可使用默认下载目录。<button type="button" onClick={() => categories.refetch()}>重新加载</button></p>}
          <p className="download-hint">每次下载单个视频；合集链接仅取首条。实际画质取决于视频源和登录权限。</p>
          {submit.isError && <p className="download-error" role="alert">{submit.error.message}</p>}
          {message && <p className="download-success" role="status">{message}</p>}
          <div className="download-submit"><span>下载完成后，可直接播放</span>
            <button className="btn-primary" disabled={!ready || !url.trim() || submit.isPending}>
              <Icon name="download" size={18} />{submit.isPending ? "正在加入队列…" : "开始下载"}</button></div>
        </form>
      </section>
      <aside className="download-note">
        <span className="tiny-label">FROM THE WEB, TO YOUR SPACE</span>
        <Icon name="download" size={38} /><h2>留存此刻，<br />随时回看。</h2>
        <p>下载进度会持续更新。离开当前页面，后台任务仍会继续。</p>
        <div className="download-engine">
          <span className={"status-dot " + (ready ? "online" : "offline")} />
          {engine.isLoading ? "正在检测下载引擎…" : engine.isError ? "下载服务暂不可用" :
            ready ? "下载引擎已就绪" : "下载依赖尚未就绪"}
          {engine.data?.version && <small>yt-dlp {engine.data.version}</small>}
        </div>
        {engine.isError && <button className="btn-outline" onClick={() => engine.refetch()}>重新检测</button>}
        <p className="download-hint">遇到登录验证？前往下载设置，配置浏览器 Cookie 或代理。</p>
        <Link to="/settings/downloads" className="download-settings-link">管理下载设置 <Icon name="arrow" size={15} /></Link>
      </aside>
    </div>
    <div className="collection-heading"><div><h2>下载记录</h2><span className="collection-count">{active} 项进行中</span></div>
      <Link to="/jobs" className="download-settings-link">任务中心 <Icon name="arrow" size={15} /></Link></div>
    <div className="tag-filters" aria-label="下载状态筛选">
      {[["all", "全部"], ["active", "进行中"], ["done", "已完成"], ["error", "失败"], ["canceled", "已取消"]].map(([v, label]) =>
        <button key={v} aria-pressed={filter === v} className={filter === v ? "selected" : ""} onClick={() => setFilter(v)}>{label}</button>)}
    </div>
    {jobs.isLoading && <div className="loading-panel" role="status">正在读取下载记录…</div>}
    {jobs.isError && <div className="inline-alert" role="alert">{jobs.error.message}<button className="btn-outline" onClick={() => jobs.refetch()}>重试</button></div>}
    <div className="download-list">{visible.map(job => {
      return <article className="card download-job" key={job.id}>
        <div className="download-job-top"><span className={"download-state download-state-" + job.state}>{states[job.state]}</span>
          <span className="download-hint">#{job.id} · {formatRelative(job.created_at)}</span></div>
        <h3>{typeof job.result?.title === "string" ? job.result.title : "视频链接下载"}</h3>
        <p className="download-source">{String(job.payload?.url ?? "")}</p>
        <TaskProgress state={job.state} progress={job.progress} createdAt={job.created_at}
          startedAt={job.started_at} finishedAt={job.finished_at} label={`下载任务 ${job.id}`} />
        <div className="download-job-bottom"><p className="download-hint">{job.state === "error" ? "下载未完成，可调整设置后重试" : job.message || "等待处理"}
          </p><DownloadActions job={job} /></div>
        {job.error && <p className="download-error">{job.error}</p>}
      </article>;
    })}</div>
    {!jobs.isLoading && !jobs.isError && !visible.length && <div className="empty-state">
      <Icon name="download" size={30} /><h3>{filter === "all" ? "从第一个链接开始。" : "此分类下暂无下载记录"}</h3>
      <p>粘贴喜欢的视频链接，下载完成后会自动出现在影像库。</p></div>}
  </div>;
}
