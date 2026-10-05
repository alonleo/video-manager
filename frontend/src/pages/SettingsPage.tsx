import { useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getVideo, listVideos } from "../api/client";
import { getSubtitleStatuses } from "../api/subtitles";
import SubtitleStatusBadges from "../components/player/SubtitleStatusBadges";
import SubtitlePanel from "../components/player/SubtitlePanel";
import CaptionStyleEditor from "../components/player/CaptionStyleEditor";
import AISettingsPage from "./AISettingsPage";
import PlaybackSettingsPage from "./PlaybackSettingsPage";
import TrustedDevicesPage from "./TrustedDevicesPage";
import Icon from "../components/Icon";

export default function SettingsPage() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const isAI = location.pathname === "/settings/ai";
  const isPlayback = location.pathname === "/settings/playback";
  const isDevices = location.pathname === "/settings/devices";
  const rawId = Number(params.get("video"));
  const videoId = Number.isSafeInteger(rawId) && rawId > 0 ? rawId : 0;
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [picking, setPicking] = useState(false);
  const videos = useQuery({
    queryKey: ["settings-videos", query, page],
    queryFn: () => listVideos({ media_type: "video", q: query, page, page_size: 8, sort: "created_desc" }),
    enabled: !isAI && !isPlayback && !isDevices && (!videoId || picking),
  });
  const video = useQuery({
    queryKey: ["video", videoId], queryFn: () => getVideo(videoId), enabled: !isAI && !isPlayback && !isDevices && !!videoId,
  });
  const showList = !videoId || picking;
  const statusIds = [...new Set([
    ...(showList ? videos.data?.items.map(v => v.id) ?? [] : []),
    ...(videoId ? [videoId] : []),
  ])].sort((a, b) => a - b);
  const statuses = useQuery({
    queryKey: ["subtitle-status", statusIds],
    queryFn: ({ signal }) => getSubtitleStatuses(statusIds, signal),
    enabled: !isAI && !isPlayback && !isDevices && statusIds.length > 0,
    staleTime: 0,
    refetchOnWindowFocus: true,
    refetchInterval: q => q.state.data?.items.some(item =>
      [item.generation, item.translation].some(task => task.active_count > 0)) ? 1500 : 5000,
  });
  const statusById = new Map(statuses.data?.items.map(item => [item.video_id, item]));
  const suffix = videoId ? "?video=" + videoId : "";
  return <div className="page content-page settings-page">
    <div className="page-heading"><div>
      <p className="eyebrow"><span /> PREFERENCES · 设置</p>
      <h1>{isDevices ? "让访问，止于信任。" : isPlayback ? "让播放，按你的节奏。" : "字幕与语言，随心设定。"}</h1>
      <p className="page-intro">{isDevices ? "通过二维码添加设备，只有经你确认的浏览器才能访问影像内容。" : isPlayback ? "设置视频自动播放与图片自动轮播，等待时间也由你决定。" : "管理视频字幕、翻译与 AI 模型。播放时，在底部控制栏随时切换。"}</p>
    </div></div>
    <nav className="settings-tabs" aria-label="设置模块">
      <Link to={"/settings" + suffix} aria-current={!isAI && !isPlayback && !isDevices ? "page" : undefined}>字幕与翻译</Link>
      <Link to="/settings/playback" aria-current={isPlayback ? "page" : undefined}>播放与轮播</Link>
      <Link to={"/settings/ai" + suffix} aria-current={isAI ? "page" : undefined}>AI 模型与服务</Link>
      <Link to="/settings/downloads">链接下载</Link>
      <Link to="/settings/devices" aria-current={isDevices ? "page" : undefined}>信任设备</Link>
    </nav>
    {isDevices ? <TrustedDevicesPage /> : isAI ? <AISettingsPage embedded /> : isPlayback ? <PlaybackSettingsPage /> : <>
      <CaptionStyleEditor />
      <section className="card settings-video-picker" aria-label="选择字幕所属视频">
        <h2>管理视频字幕</h2>
        <p className="settings-subtitle-status-help">生成与翻译状态分别显示，处理中任务优先展示，进度自动刷新。</p>
        {video.data && <div className="settings-selected-video">
          <strong>{video.data.title}</strong>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-ghost" onClick={() => setPicking(v => !v)}>{picking ? "收起选择" : "切换视频"}</button>
            <Link to={"/videos/" + videoId} className="btn-outline"><Icon name="play" size={15} /> 返回播放</Link>
          </div>
        </div>}
        {video.data && <div className="settings-selected-status">
          <SubtitleStatusBadges status={statusById.get(videoId)} pending={statuses.isPending} failed={statuses.isError} />
        </div>}
        {statuses.isError && <div className="settings-subtitle-status-error" role="alert">
          字幕状态暂时无法更新，请重试。
          <button className="btn-ghost" onClick={() => statuses.refetch()} disabled={statuses.isFetching}>刷新状态</button>
        </div>}
        {video.isLoading && !!videoId && <p role="status">正在读取视频…</p>}
        {video.isError && <p className="inline-alert" role="alert">视频读取失败：{video.error.message}
          <button className="btn-ghost" onClick={() => video.refetch()}>重试</button>
          <button className="btn-ghost" onClick={() => setParams({})}>重新选择</button>
        </p>}
        {(!videoId || picking) && <>
          <form className="settings-video-search" onSubmit={e => { e.preventDefault(); setQuery(search.trim()); setPage(1); }}>
            <label>选择视频<input className="input" type="search" placeholder="搜索视频名称" value={search} onChange={e => setSearch(e.target.value)} /></label>
            <button className="btn-outline">搜索</button>
          </form>
          {videos.isLoading && <p className="ai-hint" role="status">正在读取视频列表…</p>}
          {videos.isError && <p className="inline-alert" role="alert">{videos.error.message}<button className="btn-ghost" onClick={() => videos.refetch()}>重试</button></p>}
          {videos.data && !videos.data.items.length && <p className="ai-hint">没有找到视频，请调整关键词或先添加视频。</p>}
          <div className="settings-video-results">
            {videos.data?.items.map(v => <button type="button" key={v.id} className="settings-video-row"
              data-video-id={v.id} aria-pressed={videoId === v.id}
              onClick={() => { setParams({ video: String(v.id) }); setPicking(false); }}>
              <span className="settings-video-main">
                <span className="settings-video-title">{v.title}</span>
                <SubtitleStatusBadges status={statusById.get(v.id)} pending={statuses.isPending} failed={statuses.isError} />
              </span>
              <Icon name={videoId === v.id ? "check" : "arrow"} size={16} />
            </button>)}
          </div>
          {videos.data && videos.data.total > 8 && <div className="flex items-center gap-3 mt-3">
            <button className="btn-ghost" disabled={page === 1 || videos.isFetching} onClick={() => setPage(p => p - 1)}>上一页</button>
            <span className="text-xs text-muted">第 {page} / {Math.ceil(videos.data.total / 8)} 页</span>
            <button className="btn-ghost" disabled={page * 8 >= videos.data.total || videos.isFetching} onClick={() => setPage(p => p + 1)}>下一页</button>
          </div>}
        </>}
      </section>
      {video.data && <SubtitlePanel key={videoId} videoId={videoId} />}
    </>}
  </div>;
}
