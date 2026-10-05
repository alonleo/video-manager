import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router-dom";
import { getVideo, listVideos, updateVideo } from "../api/client";
import type { VideoListItem } from "../api/types";
import BasicVideoActions from "../components/BasicVideoActions";
import { MoveVideoButton } from "../components/DirectoryTools";
import RemoveVideoButton from "../components/RemoveVideoButton";
import VideoTagPicker from "../components/VideoTagPicker";
import Icon from "../components/Icon";
import { formatDate, formatSize } from "../lib/format";
import { readPlaybackPreferences } from "../lib/playbackPreferences";

type Neighbor = { photo: VideoListItem; page: number } | null;
const PHOTO_DETAIL_STALE_TIME = 30_000;

function photoImageUrl(photo: Pick<VideoListItem, "id" | "created_at" | "cover_path"> & { updated_at?: string }, attempt = 0) {
  const version = `${photo.updated_at ?? photo.created_at}:${photo.cover_path}`;
  return `/api/photos/${photo.id}/image?v=${encodeURIComponent(version)}&retry=${attempt}`;
}

function photoListOptions(search: string) {
  const params = new URLSearchParams(search);
  return {
    page: Math.max(1, Math.floor(Number(params.get("page"))) || 1),
    q: params.get("q") || undefined,
    tag: params.get("tag") || undefined,
    category: params.get("category") || undefined,
    favorite: params.get("favorite") === "true" || undefined,
    sort: params.get("sort") || "created_desc",
  };
}

function PhotoViewer({ id, version, title, previous, next, first, autoplay, intervalSeconds, onNavigate }: {
  id: number;
  version: string;
  title: string;
  previous: Neighbor;
  next: Neighbor;
  first: Neighbor;
  autoplay: boolean;
  intervalSeconds: number;
  onNavigate: (neighbor: NonNullable<Neighbor>) => void;
}) {
  const viewer = useRef<HTMLElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const preloaded = useRef(new Map<number, HTMLImageElement>());
  const [bounds, setBounds] = useState({ w: 800, h: 600 });
  const [natural, setNatural] = useState({ w: 0, h: 0 });
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [screenError, setScreenError] = useState("");

  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setBounds({ w: entry.contentRect.width, h: entry.contentRect.height }));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === viewer.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);
  useEffect(() => {
    setZoom(1);
    setRotation(0);
    setError(false);
    setAttempt(0);
  }, [id]);
  useEffect(() => {
    const neighbors = [previous?.photo, next?.photo ?? first?.photo].filter((photo): photo is VideoListItem => Boolean(photo));
    const retainedIds = new Set([id, ...neighbors.map(photo => photo.id)]);
    for (const cachedId of preloaded.current.keys()) {
      if (!retainedIds.has(cachedId)) preloaded.current.delete(cachedId);
    }
    for (const photo of neighbors) {
      if (preloaded.current.has(photo.id)) continue;
      const image = new Image();
      image.decoding = "async";
      image.src = photoImageUrl(photo);
      preloaded.current.set(photo.id, image);
      void image.decode().catch(() => preloaded.current.delete(photo.id));
    }
  }, [first?.photo, id, next?.photo, previous?.photo]);
  useEffect(() => {
    if (!autoplay || error || !natural.w) return;
    const target = next ?? first;
    if (!target || target.photo.id === id) return;
    const timer = window.setTimeout(() => onNavigate(target), intervalSeconds * 1000);
    return () => window.clearTimeout(timer);
  }, [autoplay, error, first, id, intervalSeconds, natural.w, next, onNavigate]);

  const sideways = Math.abs(rotation % 180) === 90;
  const visualWidth = sideways ? natural.h : natural.w;
  const visualHeight = sideways ? natural.w : natural.h;
  const fit = natural.w ? Math.min((bounds.w - 40) / visualWidth, (bounds.h - 40) / visualHeight, 1) : 1;
  const changeZoom = (value: number) => setZoom(Math.max(0.25, Math.min(8, value)));

  return <section className="photo-viewer" ref={viewer} aria-label="照片预览">
    <div className="photo-viewport" ref={viewport} tabIndex={0} autoFocus aria-label="照片画布，按左右方向键切换，按加减键缩放，按 R 旋转"
      onKeyDown={e => {
        if (e.key === "ArrowLeft" && previous) { e.preventDefault(); onNavigate(previous); }
        else if (e.key === "ArrowRight" && next) { e.preventDefault(); onNavigate(next); }
        else if (e.key.toLowerCase() === "r") { e.preventDefault(); setRotation(value => value + (e.shiftKey ? -90 : 90)); }
        else if (["+", "=", "-", "0"].includes(e.key)) {
          e.preventDefault();
          if (e.key === "0") setZoom(1); else changeZoom(zoom * (e.key === "-" ? 0.8 : 1.25));
        }
      }}
      onTouchStart={e => { const touch = e.touches[0]; touchStart.current = { x: touch.clientX, y: touch.clientY }; }}
      onTouchEnd={e => {
        const start = touchStart.current;
        const touch = e.changedTouches[0];
        touchStart.current = null;
        if (!start || zoom !== 1) return;
        const dx = touch.clientX - start.x;
        const dy = touch.clientY - start.y;
        if (Math.abs(dx) < 55 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
        if (dx > 0 && previous) onNavigate(previous);
        if (dx < 0 && next) onNavigate(next);
      }}>
      {error ? <div className="photo-preview-status" role="alert"><Icon name="photo" size={36} /><p>无法预览照片，请检查源文件或磁盘是否可用。</p><button className="btn-outline" onClick={() => { setError(false); setNatural({ w: 0, h: 0 }); setAttempt(a => a + 1); }}>重新加载</button></div> : <>
        {!natural.w && <div className="photo-preview-status" role="status">正在读取照片…</div>}
        <button className="photo-step photo-step-previous" aria-label="查看上一张照片" disabled={!previous} onClick={() => previous && onNavigate(previous)}><Icon name="chevron" size={26} /></button>
        <div className="photo-canvas">
          <div className="photo-rotation-stage" style={{ width: natural.w ? visualWidth * fit * zoom : undefined, height: natural.h ? visualHeight * fit * zoom : undefined }}>
            <img key={attempt} src={`/api/photos/${id}/image?v=${encodeURIComponent(version)}&retry=${attempt}`}
              alt={title} draggable={false} onLoad={e => setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
              onError={() => setError(true)} style={{ width: natural.w ? natural.w * fit * zoom : undefined, height: natural.h ? natural.h * fit * zoom : undefined, opacity: natural.w ? 1 : 0, transform: `translate(-50%, -50%) rotate(${rotation}deg)` }} />
          </div>
        </div>
        <button className="photo-step photo-step-next" aria-label="查看下一张照片" disabled={!next} onClick={() => next && onNavigate(next)}><Icon name="chevron" size={26} /></button>
      </>}
    </div>
    <div className="photo-controls" role="group" aria-label="照片查看工具">
      <button className="btn-outline" aria-label="向左旋转照片" disabled={error || !natural.w} onClick={() => setRotation(value => value - 90)}><Icon name="rotateLeft" size={17} /></button>
      <button className="btn-outline" aria-label="向右旋转照片" disabled={error || !natural.w} onClick={() => setRotation(value => value + 90)}><Icon name="rotateRight" size={17} /></button>
      <button className="btn-outline" aria-label="缩小照片" disabled={error || !natural.w || zoom <= 0.25} onClick={() => changeZoom(zoom / 1.25)}>−</button>
      <span aria-live="polite">{Math.round(zoom * 100)}%</span>
      <button className="btn-outline" aria-label="放大照片" disabled={error || !natural.w || zoom >= 8} onClick={() => changeZoom(zoom * 1.25)}>+</button>
      <button className="btn-outline" onClick={() => setZoom(1)}>适应窗口</button>
      {autoplay && <span className="photo-slideshow-status" aria-live="polite">自动轮播 · {intervalSeconds} 秒</span>}
      <button className="btn-outline" onClick={async () => {
        try { setScreenError(""); if (document.fullscreenElement) await document.exitFullscreen(); else await viewer.current?.requestFullscreen(); }
        catch { setScreenError("当前环境无法进入全屏，请使用窗口预览。"); }
      }}><Icon name={fullscreen ? "fullscreenExit" : "fullscreen"} size={16} />{fullscreen ? "退出全屏" : "全屏"}</button>
    </div>
    {screenError && <p className="photo-screen-error" role="status">{screenError}</p>}
  </section>;
}

export default function PhotoPage() {
  const id = Number(useParams().id);
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [toolbar, setToolbar] = useState<HTMLDivElement | null>(null);
  const [manage, setManage] = useState(false);
  const playbackPreferences = useState(readPlaybackPreferences)[0];
  const query = useQuery({
    queryKey: ["video", id],
    queryFn: () => getVideo(id),
    enabled: Number.isSafeInteger(id) && id > 0,
    staleTime: PHOTO_DETAIL_STALE_TIME,
  });
  const favorite = useMutation({ mutationFn: (is_favorite: boolean) => updateVideo(id, { is_favorite }), onSuccess: () => {
    void qc.invalidateQueries({ queryKey: ["video", id] }); void qc.invalidateQueries({ queryKey: ["videos"] });
  } });
  const search = typeof location.state?.librarySearch === "string" && location.state.librarySearch.startsWith("?") ? location.state.librarySearch : "";
  const listOptions = useMemo(() => photoListOptions(search), [search]);
  const sequence = useQuery({
    queryKey: ["photo-sequence", listOptions],
    queryFn: async () => {
      const current = await listVideos({ ...listOptions, media_type: "photo", page_size: 24 });
      const totalPages = Math.max(1, Math.ceil(current.total / current.page_size));
      const adjacent = await Promise.all([
        listOptions.page > 1 ? listVideos({ ...listOptions, media_type: "photo", page: listOptions.page - 1, page_size: 24 }) : null,
        listOptions.page < totalPages ? listVideos({ ...listOptions, media_type: "photo", page: listOptions.page + 1, page_size: 24 }) : null,
      ]);
      return [
        ...(adjacent[0]?.items ?? []).map(photo => ({ photo, page: listOptions.page - 1 })),
        ...current.items.map(photo => ({ photo, page: listOptions.page })),
        ...(adjacent[1]?.items ?? []).map(photo => ({ photo, page: listOptions.page + 1 })),
      ];
    },
  });
  const firstPhoto = useQuery({
    queryKey: ["photo-sequence-first", { ...listOptions, page: 1 }],
    queryFn: () => listVideos({ ...listOptions, media_type: "photo", page: 1, page_size: 1 }),
    enabled: playbackPreferences.autoplayPhotos,
    staleTime: PHOTO_DETAIL_STALE_TIME,
  });
  const returnState = { restoreLibraryKey: location.state?.libraryEntryKey };
  const back = `/photos${search}`;
  const sequenceIndex = sequence.data?.findIndex(item => item.photo.id === id) ?? -1;
  const previous = sequenceIndex > 0 ? sequence.data![sequenceIndex - 1] : null;
  const next = sequenceIndex >= 0 && sequenceIndex < (sequence.data?.length ?? 0) - 1 ? sequence.data![sequenceIndex + 1] : null;
  const first = useMemo<Neighbor>(() => firstPhoto.data?.items[0]
    ? { photo: firstPhoto.data.items[0], page: 1 }
    : sequence.data?.find(item => item.page === 1) ?? null,
  [firstPhoto.data, sequence.data]);
  useEffect(() => {
    for (const neighbor of [previous, next]) {
      if (!neighbor) continue;
      void qc.prefetchQuery({
        queryKey: ["video", neighbor.photo.id],
        queryFn: () => getVideo(neighbor.photo.id),
        staleTime: PHOTO_DETAIL_STALE_TIME,
      });
    }
  }, [next, previous, qc]);
  const currentSequenceItem = sequenceIndex >= 0 ? sequence.data![sequenceIndex] : null;
  const sequencePosition = currentSequenceItem
    ? (currentSequenceItem.page - 1) * 24 + sequence.data!.filter(item => item.page === currentSequenceItem.page).findIndex(item => item.photo.id === id) + 1
    : null;
  const navigatePhoto = useCallback((neighbor: NonNullable<Neighbor>) => {
    const nextParams = new URLSearchParams(search);
    if (neighbor.page > 1) nextParams.set("page", String(neighbor.page)); else nextParams.delete("page");
    const librarySearch = nextParams.toString() ? `?${nextParams}` : "";
    navigate(`/photos/${neighbor.photo.id}`, { state: { ...location.state, librarySearch } });
  }, [location.state, navigate, search]);

  if (query.isLoading) return <div className="page" role="status">正在读取照片…</div>;
  if (!query.data || query.isError) return <div className="page empty-state" role="alert"><h1>照片读取失败</h1><p>{query.error?.message ?? "照片不存在"}</p><Link to={back} className="btn-outline">返回照片库</Link></div>;
  const photo = query.data;
  if (photo.media_type !== "photo") return <Navigate to={`/videos/${id}`} replace />;
  return <div className={`page photo-page${manage ? " is-managing" : " is-viewing"}`}>
    <div className="player-heading">
      <Link to={back} state={returnState} className="btn-ghost"><Icon name="back" size={17} />返回照片库</Link>
      <div><p className="eyebrow"><span />PHOTO COLLECTION · 照片收藏</p><h1>{photo.title}</h1>{sequencePosition && <p className="photo-sequence-count">第 {sequencePosition} 张 · 当前浏览序列</p>}</div>
      <div className="player-action-row">
        <button className="btn-outline photo-manage-toggle" aria-pressed={manage} onClick={() => setManage(value => !value)}><Icon name={manage ? "photo" : "settings"} size={16} />{manage ? "返回浏览" : "管理照片"}</button>
        {manage && <><div className="player-basic-action-slot" ref={setToolbar} /><MoveVideoButton key={id} video={photo} /><RemoveVideoButton video={photo} onRemoved={() => navigate(back, { replace: true, state: { ...returnState, removalNotice: `已移除「${photo.title}」的收录，原文件已保留；再次扫描可重新收录。` } })} /></>}
        <button className="btn-outline" aria-pressed={photo.is_favorite} disabled={favorite.isPending} onClick={() => favorite.mutate(!photo.is_favorite)}><Icon name="star" size={16} />{photo.is_favorite ? "已收藏" : "收藏"}</button>
        <a className="btn-outline" href={`/api/photos/${id}/image?download=true`} download><Icon name="download" size={16} />下载原图</a>
      </div>
    </div>
    {favorite.isError && <p className="inline-alert" role="alert">收藏失败：{favorite.error.message}</p>}
    <div className="photo-detail-grid">
      <div className="photo-main"><PhotoViewer id={id} version={`${photo.updated_at}:${photo.cover_path}`} title={photo.title} previous={previous} next={next} first={first}
        autoplay={playbackPreferences.autoplayPhotos && !manage} intervalSeconds={playbackPreferences.photoIntervalSeconds} onNavigate={navigatePhoto} />
        {manage && <BasicVideoActions key={id} video={photo} toolbar={toolbar} />}
        {manage && <p className="photo-original-note">原图保持不变。HEIC / HEIF、TIFF 和 BMP 使用兼容预览；可下载原图查看完整细节。</p>}
      </div>
      {manage && <aside className="photo-details">
        <section className="card photo-info"><h2>照片信息</h2><dl>
          {[["尺寸", photo.width && photo.height ? `${photo.width} × ${photo.height}` : "—"], ["格式", photo.container?.toUpperCase() ?? "—"], ["大小", formatSize(photo.size_bytes)], ["收录时间", formatDate(photo.created_at)]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl>{photo.categories.map(c => <p key={c.id} className="photo-path"><Icon name="folder" size={14} />{c.storage_name} / {c.path || "根目录"}</p>)}</section>
        <VideoTagPicker video={photo} />
        <section className="card photo-info"><h2>原图来源</h2>{photo.sources.map(s => <p className="photo-path" key={s.id}>{s.local_path ?? s.uri}{!s.available && <strong> · 不可用</strong>}</p>)}</section>
      </aside>}
    </div>
  </div>;
}
