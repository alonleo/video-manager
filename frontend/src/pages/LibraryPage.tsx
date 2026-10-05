import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { listStorages, listTags, listVideos } from "../api/client";
import VideoCard from "../components/VideoCard";
import TagManager from "../components/TagManager";
import { CategoryFilter } from "../components/DirectoryTools";
import Icon from "../components/Icon";
import { cn } from "../lib/cn";
import { useLibraryPosition } from "../lib/useLibraryPosition";

const SORTS = [
  { value: "created_desc", label: "最近添加" },
  { value: "title", label: "标题 A → Z" },
  { value: "duration_desc", label: "时长优先" },
  { value: "size_desc", label: "大小优先" },
];

function getPageNumbers(page: number, totalPages: number): (number | string)[] {
  if (totalPages <= 7)
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  if (page <= 4) return [1, 2, 3, 4, 5, "end", totalPages];
  if (page >= totalPages - 3)
    return [
      1,
      "start",
      totalPages - 4,
      totalPages - 3,
      totalPages - 2,
      totalPages - 1,
      totalPages,
    ];
  return [1, "start", page - 1, page, page + 1, "end", totalPages];
}

function ArchiveArtwork() {
  return (
    <div className="archive-art" aria-hidden="true">
      <div className="art-orbit" />
      <div className="art-label">A PLACE FOR EVERY FRAME</div>
      <div className="film-frame frame-back">
        <div className="film-perfs" />
        <div className="frame-landscape">
          <i />
          <b />
        </div>
        <div className="film-perfs" />
      </div>
      <div className="film-frame frame-front">
        <div className="film-perfs" />
        <div className="frame-image">
          <div className="frame-sun" />
          <div className="frame-hill hill-one" />
          <div className="frame-hill hill-two" />
          <span>
            THE ART OF
            <br />
            <em>keeping.</em>
          </span>
          <small>FRAME ARCHIVE — 001</small>
        </div>
        <div className="film-perfs" />
      </div>
      <div className="art-caption">
        <span>收 集 · 归 档 · 回 看</span>
        <span>01 / ∞</span>
      </div>
    </div>
  );
}

export default function LibraryPage({ mediaType = "video" }: { mediaType?: "video" | "photo" }) {
  const isPhoto = mediaType === "photo";
  const noun = isPhoto ? "照片" : "视频";
  const unit = isPhoto ? "张" : "部";
  const location = useLocation();
  const [removalNotice, setRemovalNotice] = useState<string>(location.state?.removalNotice ?? "");
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const tag = params.get("tag") ?? "";
  const category = params.get("category") ?? "";
  const favorite = params.get("favorite") === "true";
  const sort = SORTS.some((s) => s.value === params.get("sort"))
    ? params.get("sort")!
    : "created_desc";
  const page = Math.max(
    1,
    Math.min(100000, Math.floor(Number(params.get("page"))) || 1),
  );
  const view = params.get("view") === "list" ? "list" : "grid";
  const [input, setInput] = useState(q);
  const searchRef = useRef<HTMLInputElement>(null);
  const pageSize = 24;
  useEffect(() => {
    setInput(q);
  }, [q]);
  useEffect(() => {
    if (input === q) return;
    const timer = window.setTimeout(
      () =>
        setParams(
          (previous) => {
            const next = new URLSearchParams(previous);
            input.trim() ? next.set("q", input.trim()) : next.delete("q");
            next.delete("page");
            return next;
          },
          { replace: true },
        ),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [input, q, setParams]);
  useEffect(() => {
    if (params.get("focus") !== "search") return;
    searchRef.current?.focus();
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete("focus");
        return next;
      },
      { replace: true },
    );
  }, [params, setParams]);
  const update = (key: string, value: string) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      value ? next.set(key, value) : next.delete(key);
      if (key !== "page" && key !== "view") next.delete("page");
      return next;
    });
  const clear = () => {
    setInput("");
    setParams(view === "list" ? { view: "list" } : {});
  };
  const videos = useQuery({
    queryKey: ["videos", { mediaType, q, tag, category, favorite, sort, page }],
    queryFn: () =>
      listVideos({
        media_type: mediaType,
        q: q || undefined,
        tag: tag || undefined,
        category: category || undefined,
        favorite: favorite || undefined,
        sort,
        page,
        page_size: pageSize,
      }),
    placeholderData: keepPreviousData,
  });
  const overview = useQuery({
    queryKey: ["videos", "overview", mediaType],
    queryFn: () => listVideos({ page_size: 1, media_type: mediaType }),
  });
  const storages = useQuery({ queryKey: ["storages"], queryFn: listStorages });
  const tags = useQuery({ queryKey: ["tags", mediaType], queryFn: () => listTags(mediaType) });
  const listRoot = useLibraryPosition(
    !!videos.data &&
      !videos.isPlaceholderData &&
      !videos.isFetching &&
      !videos.isError &&
      !overview.isLoading &&
      !tags.isLoading &&
      !storages.isLoading,
  );
  const total = videos.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const filtered = !!(q || tag || category || favorite);
  const emptyLibrary = overview.data?.total === 0 && !filtered;
  useEffect(() => {
    if (videos.data && !videos.isPlaceholderData && page > totalPages) {
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          next.set("page", String(totalPages));
          return next;
        },
        { replace: true },
      );
    }
  }, [page, totalPages, videos.data, videos.isPlaceholderData, setParams]);
  return (
    <div ref={listRoot} className="page library-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            <span /> THE PERSONAL FILM ARCHIVE
          </p>
          <h1>{favorite ? `收藏的${noun}` : isPhoto ? "照片，定格美好。" : "影像，皆有归处。"}</h1>
          <p className="page-intro">
            {isPhoto ? "独立收藏每一张照片，让值得珍藏的瞬间井然有序。" : "把散落各处的视频，整理成属于你的影像世界。"}
          </p>
        </div>
        <Link to="/storages?add=1" className="btn-primary">
          <Icon name="plus" size={17} />
          连接存储源
        </Link>
      </div>
      {favorite && (
        <nav className="favorite-media-switch" aria-label="收藏类型">
          <Link to="/?favorite=true" className={cn(!isPhoto && "selected")} aria-current={!isPhoto ? "page" : undefined}>
            <Icon name="film" size={18} />
            <span><strong>视频收藏</strong><small>查看收藏的视频</small></span>
          </Link>
          <Link to="/photos?favorite=true" className={cn(isPhoto && "selected")} aria-current={isPhoto ? "page" : undefined}>
            <Icon name="photo" size={18} />
            <span><strong>照片收藏</strong><small>查看收藏的照片</small></span>
          </Link>
        </nav>
      )}
      {!isPhoto && !filtered && page === 1 && (
        <section
          className={cn(
            "archive-hero",
            !!overview.data?.total && "has-collection",
          )}
          aria-label="欢迎来到帧藏"
        >
          <div className="hero-copy">
            <span className="hero-number">YOUR COLLECTION, REIMAGINED.</span>
            <h2>
              值得留下的，
              <br />
              不止<span>一帧。</span>
            </h2>
            <p>
              从一段旅途，到一场灵感。
              <br />
              让每个想重温的瞬间，都触手可及。
            </p>
            <div className="hero-actions">
              {emptyLibrary ? (
                <Link to="/storages?add=1" className="btn-dark">
                  建立我的影像库
                  <Icon name="arrow" size={17} />
                </Link>
              ) : (
                <a href="#collection" className="btn-dark">
                  探索我的影像
                  <Icon name="arrow" size={17} />
                </a>
              )}
              <span>收藏有序 · 随时回看</span>
            </div>
          </div>
          <ArchiveArtwork />
        </section>
      )}
      <div className="archive-summary">
        <div>
          <Icon name={isPhoto ? "photo" : "film"} />
          <span>已收录{noun}</span>
          <strong>
            {overview.data ? String(overview.data.total).padStart(2, "0") : "—"}
            <small>{unit}</small>
          </strong>
        </div>
        <Link to="/storages">
          <Icon name="storage" />
          <span>已连接存储源</span>
          <strong>
            {storages.data
              ? String(storages.data.length).padStart(2, "0")
              : "—"}
            <small>个</small>
          </strong>
          <Icon name="chevron" size={15} />
        </Link>
        <div className="summary-note">
          <span className="status-dot online" />
          <span>原片留在原处，收藏自在此间</span>
        </div>
      </div>
      {isPhoto && <p className="photo-library-hint"><Icon name="photo" size={18} />支持 JPG、PNG、WebP、GIF、BMP、TIFF、AVIF、HEIC / HEIF。在存储源中扫描照片目录即可收录，原文件留在原处。</p>}
      {removalNotice && <p className="directory-notice" role="status">{removalNotice}</p>}
      <section
        id="collection"
        className="collection-section"
        aria-labelledby="collection-title"
      >
        <div className="collection-heading">
          <div>
            <h2 id="collection-title">
              {favorite ? `收藏的${noun}` : isPhoto ? "我的照片库" : "我的视频库"}
            </h2>
            <span className="collection-count" role="status">
              {videos.isLoading ? "读取中" : `${total} ${unit}${noun}`}
            </span>
          </div>
          <div className="view-switch" role="group" aria-label="浏览方式">
            <button
              className={cn(view === "grid" && "selected")}
              onClick={() => update("view", "grid")}
              aria-label="网格视图"
              aria-pressed={view === "grid"}
            >
              <Icon name="grid" size={17} />
            </button>
            <button
              className={cn(view === "list" && "selected")}
              onClick={() => update("view", "list")}
              aria-label="列表视图"
              aria-pressed={view === "list"}
            >
              <Icon name="list" size={19} />
            </button>
          </div>
        </div>
        <div className="library-toolbar">
          <div className="library-search">
            <Icon name="search" size={18} />
            <input
              ref={searchRef}
              id="library-search"
              type="search"
              aria-label={`搜索${noun}标题或描述`}
              placeholder="搜索标题、描述，找到那一帧…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setInput("");
                  update("q", "");
                }
              }}
            />
            {input ? (
              <button
                onClick={() => {
                  setInput("");
                  update("q", "");
                  searchRef.current?.focus();
                }}
                aria-label="清空搜索"
              >
                <Icon name="close" size={16} />
              </button>
            ) : (
              <kbd>/</kbd>
            )}
          </div>
          <button
            className={cn(
              "btn-outline favorite-filter",
              favorite && "is-selected",
            )}
            aria-pressed={favorite}
            onClick={() => update("favorite", favorite ? "" : "true")}
          >
            <Icon name="star" size={17} />
            仅看收藏
          </button>
          <select
            className="input sort-select"
            aria-label="排序方式"
            value={sort}
            onChange={(e) => update("sort", e.target.value)}
          >
            {SORTS.filter(s => !isPhoto || s.value !== "duration_desc").map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <CategoryFilter mediaType={mediaType}
          value={category}
          onChange={(id) => update("category", id)}
        />
        <div className="tag-toolbar">
          {!!tags.data?.length && (
            <div className="tag-filters" aria-label="按标签筛选">
              <button
                className={cn(!tag && "selected")}
                aria-pressed={!tag}
                onClick={() => update("tag", "")}
              >
                全部标签
              </button>
              {tags.data.map((t) => (
                <button
                  key={t.id}
                  className={cn(tag === t.name && "selected")}
                  aria-pressed={tag === t.name}
                  onClick={() => update("tag", tag === t.name ? "" : t.name)}
                >
                  {t.name}
                </button>
              ))}
            </div>
          )}
          <TagManager mediaType={mediaType}
            onDeleted={(deleted) => {
              setParams(
                (previous) => {
                  if (previous.get("tag") !== deleted.name) return previous;
                  const next = new URLSearchParams(previous);
                  next.delete("tag");
                  next.delete("page");
                  return next;
                },
                { replace: true },
              );
            }}
          />
        </div>
        {filtered && (
          <div className="filter-feedback">
            <span>
              {q
                ? `搜索「${q}」`
                : favorite
                  ? "已筛选收藏"
                  : category
                    ? "已筛选目录分类"
                    : "已筛选标签"}
              {tag ? ` · ${tag}` : ""}
            </span>
            <button onClick={clear}>
              清除筛选
              <Icon name="close" size={13} />
            </button>
          </div>
        )}
        <div
          className={cn(
            "collection-content",
            videos.isFetching && "is-fetching",
          )}
          aria-busy={videos.isFetching}
        >
          {videos.isLoading && (
            <div className="video-grid" aria-label={`正在加载${noun}`}>
              {Array.from({ length: 6 }, (_, i) => (
                <div className="video-skeleton" key={i}>
                  <div />
                  <span />
                  <span />
                </div>
              ))}
            </div>
          )}
          {videos.isError && (
            <div className="empty-state" role="alert">
              <Icon name="activity" size={32} />
              <h3>影像库暂时没有响应</h3>
              <p>{(videos.error as Error).message}</p>
              <button className="btn-outline" onClick={() => videos.refetch()}>
                <Icon name="refresh" size={16} />
                重新加载
              </button>
            </div>
          )}
          {!videos.isError &&
            videos.data?.items.length === 0 &&
            (filtered ? (
              <div className="empty-state">
                <Icon name="search" size={32} />
                <h3>
                  {favorite && !q && !tag
                    ? "喜欢的影像，值得单独收藏。"
                    : "暂时没有找到这段影像"}
                </h3>
                <p>
                  {favorite && !q && !tag
                    ? `在${isPhoto ? "照片预览" : "播放"}页点击收藏，它就会出现在这里。`
                    : "试试其他关键词，或清除筛选看看全部影像。"}
                </p>
                <button className="btn-outline" onClick={clear}>
                  查看全部影像
                  <Icon name="arrow" size={16} />
                </button>
              </div>
            ) : (
              <div className="first-collection">
                <div className="first-collection-title">
                  <span className="empty-icon">
                    <Icon name="folder" size={23} />
                  </span>
                  <div>
                    <h3>{isPhoto ? "从第一张照片，开始珍藏。" : "好故事，从第一段影像开始。"}</h3>
                    <p>三个简单步骤，让你的收藏井然有序。</p>
                  </div>
                  <Link to="/storages?add=1" className="text-link">
                    开始连接
                    <Icon name="arrow" size={17} />
                  </Link>
                </div>
                <div className="onboarding-steps">
                  <div>
                    <span>01</span>
                    <div>
                      <h4>连接存储源</h4>
                      <p>选择本地目录或 NAS 挂载目录</p>
                    </div>
                  </div>
                  <div>
                    <span>02</span>
                    <div>
                      <h4>扫描{noun}</h4>
                      <p>读取封面与信息，原片留在原处</p>
                    </div>
                  </div>
                  <div>
                    <span>03</span>
                    <div>
                      <h4>开始回看</h4>
                      <p>{isPhoto ? "预览、搜索、分类、收藏你的照片" : "搜索、收藏，从上次看到的地方继续"}</p>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          {!videos.isError && !!videos.data?.items.length && (
            <div className={view === "list" ? "video-list" : "video-grid"}>
              {videos.data.items.map((v) => (
                <VideoCard key={v.id} video={v} onRemoved={() => setRemovalNotice(`已移除「${v.title}」的收录，原文件已保留；再次扫描对应存储源可重新收录。`)} />
              ))}
            </div>
          )}
        </div>
        {totalPages > 1 && (
          <nav className="pagination" aria-label={`${noun}分页`}>
            <button
              className="btn-outline"
              disabled={page <= 1 || videos.isPlaceholderData}
              onClick={() => update("page", String(page - 1))}
            >
              <Icon name="back" size={16} />
              上一页
            </button>
            <div className="pagination-pages">
              {getPageNumbers(page, totalPages).map((item) =>
                typeof item === "number" ? (
                  <button
                    key={item}
                    type="button"
                    className="btn-outline pagination-number"
                    aria-label={`第 ${item} 页`}
                    aria-current={item === page ? "page" : undefined}
                    title={item === 1 ? "首页" : item === totalPages ? "末页" : undefined}
                    disabled={videos.isPlaceholderData}
                    onClick={() => {
                      if (item !== page) update("page", String(item));
                    }}
                  >
                    {item}
                  </button>
                ) : (
                  <span key={item} className="pagination-ellipsis" aria-hidden="true">
                    …
                  </span>
                ),
              )}
            </div>
            <button
              className="btn-outline"
              disabled={page >= totalPages || videos.isPlaceholderData}
              onClick={() => update("page", String(page + 1))}
            >
              下一页
              <Icon name="arrow" size={16} />
            </button>
            <span className="pagination-summary" aria-live="polite" aria-atomic="true">
              第 {page} 页 / 共 {totalPages} 页
            </span>
          </nav>
        )}
      </section>
    </div>
  );
}
