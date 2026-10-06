import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "./api/client";
import Icon from "./components/Icon";
import TaskNotifications from "./components/TaskNotifications";
import type { IconName } from "./components/Icon";
import { cn } from "./lib/cn";
import { shouldResetRouteScroll } from "./lib/routeScroll";
import { getLibraryDestination, rememberLibraryDestination } from "./lib/libraryNavigation";
import { rememberLibraryPosition } from "./lib/libraryPosition";

function HealthStatus() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
  });
  const data = health.data;
  const healthy = data?.database && data?.ffmpeg;
  const degraded = data?.redis === false || data?.openlist === false;
  const label = health.isError
    ? "连接中断"
    : !data
      ? "正在连接"
      : !healthy
        ? "服务异常"
        : degraded
          ? "部分服务未连接"
          : "服务运行正常";
  return (
    <details className="health-status">
      <summary>
        <span
          className={cn(
            "status-dot",
            healthy && !degraded ? "online" : "offline",
          )}
        />
        {label}
        <Icon name="chevron" size={13} />
      </summary>
      <div className="health-popover">
        <strong>系统状态</strong>
        {data ? (
          [
            ["数据库", data.database],
            ["媒体服务", data.ffmpeg],
            ["任务队列", data.redis],
            ["网盘网关", data.openlist],
          ].map(([key, ok]) => (
            <p key={String(key)}>
              <span>{key}</span>
              <span>{ok ? "可用" : "未连接"}</span>
            </p>
          ))
        ) : (
          <p>
            {health.isError ? "请检查后台服务是否已启动。" : "正在获取状态…"}
          </p>
        )}
      </div>
    </details>
  );
}

const navigation: {
  to: string;
  label: string;
  icon: IconName;
  number: string;
}[] = [
  { to: "/", label: "视频库", icon: "film", number: "01" },
  { to: "/photos", label: "照片库", icon: "photo", number: "02" },
  { to: "/?favorite=true", label: "我的收藏", icon: "star", number: "03" },
  { to: "/storages", label: "存储源", icon: "storage", number: "04" },
  { to: "/jobs", label: "任务中心", icon: "activity", number: "05" },
  { to: "/downloads", label: "链接下载", icon: "download", number: "06" },
  { to: "/settings", label: "设置", icon: "settings", number: "07" },
];

const SIDEBAR_STORAGE_KEY = "frame-archive-sidebar-collapsed";

function getInitialSidebarState() {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(getInitialSidebarState);
  const previousPathname = useRef<string | null>(null);
  useLayoutEffect(() => {
    rememberLibraryDestination(location);
  }, [location.pathname, location.search, location.key]);
  useLayoutEffect(() => {
    const previous = history.scrollRestoration;
    history.scrollRestoration = "manual";
    return () => {
      history.scrollRestoration = previous;
    };
  }, []);
  useEffect(() => {
    if (shouldResetRouteScroll(previousPathname.current, location.pathname, location.search)) {
      window.scrollTo({ top: 0, behavior: "instant" });
    }
    previousPathname.current = location.pathname;
  }, [location.pathname]);
  const favorite =
    new URLSearchParams(location.search).get("favorite") === "true";
  const libraryPath = location.pathname === "/" || location.pathname === "/photos";
  const title =
    favorite && libraryPath ? "我的收藏" :
    location.pathname === "/photos" ? "照片库" :
    location.pathname.startsWith("/photos/") ? "照片预览" :
    location.pathname === "/downloads" ? "链接下载" :
    location.pathname === "/settings/downloads" ? "下载设置" :
    location.pathname.startsWith("/settings")
      ? "设置"
      : location.pathname.startsWith("/videos/")
        ? "放映室"
        : location.pathname === "/storages"
          ? "存储源"
          : location.pathname === "/jobs"
            ? "任务中心"
            : "视频库";
  useEffect(() => {
    document.title = `${title} · 帧藏`;
  }, [title]);
  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(sidebarCollapsed));
    } catch {
      // Storage is optional; the sidebar still works for the current session.
    }
  }, [sidebarCollapsed]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.closest('input, textarea, select, [contenteditable="true"]'))
        return;
      if (
        e.key === "/" ||
        ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k")
      ) {
        e.preventDefault();
        const target = location.pathname.startsWith("/photos") ? "/photos" : "/";
        navigate(`${target}?${favorite ? "favorite=true&" : ""}focus=search`);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [favorite, navigate, location.pathname]);
  return (
    <div className={cn("app-shell", sidebarCollapsed && "sidebar-collapsed")}>
      <a href="#main-content" className="skip-link">
        跳转到主要内容
      </a>
      <aside id="main-sidebar" className="sidebar" aria-hidden={sidebarCollapsed}>
        <Link to="/" className="brand" aria-label="帧藏首页">
          <span className="brand-mark">
            <Icon name="film" size={25} />
          </span>
          <span>
            <strong>
              帧藏<span className="brand-period">.</span>
            </strong>
            <small>FRAME ARCHIVE</small>
          </span>
        </Link>
        <div className="sidebar-caption">你的私人影像资料馆</div>
        <nav aria-label="主导航" className="main-nav">
          {navigation.map((item) => {
            const destination = getLibraryDestination(item.to, location);
            const active =
              item.to === "/"
                ? location.pathname === "/" && !favorite
                : item.to.includes("favorite")
                  ? libraryPath && favorite
                  : item.to === "/photos"
                    ? location.pathname === "/photos" && !favorite
                    : item.to === "/settings" ? location.pathname.startsWith("/settings") : location.pathname === item.to;
            return (
              <Link
                key={item.to}
                to={destination.to}
                state={destination.state}
                onClick={() => {
                  if (libraryPath) rememberLibraryPosition(location.key, location.search);
                }}
                aria-label={item.label}
                aria-current={active ? "page" : undefined}
                className={cn("nav-item", active && "active")}
              >
                <Icon name={item.icon} />
                <span>{item.label}</span>
                <small>{item.number}</small>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-note">
          <span className="tiny-label">MADE FOR YOUR COLLECTION</span>
          <div className="note-line" />
          <p>
            散落的片段，
            <br />
            在这里珍藏。
          </p>
          <span>原片留在原处，精彩汇聚于此。</span>
        </div>
        <div className="sidebar-bottom">
          <span className="status-dot online" /> 本地优先
          <span className="edition">VOL. 01</span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="topbar-leading">
            <button
              type="button"
              className="sidebar-toggle"
              aria-controls="main-sidebar"
              aria-expanded={!sidebarCollapsed}
              aria-label={sidebarCollapsed ? "展开侧边栏" : "隐藏侧边栏"}
              title={sidebarCollapsed ? "展开侧边栏" : "隐藏侧边栏"}
              onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
            >
              <Icon name={sidebarCollapsed ? "sidebarOpen" : "sidebarClose"} size={18} />
            </button>
            <div className="breadcrumb">
              <span>我的影像空间</span>
              <span>/</span>
              <strong>{title}</strong>
            </div>
          </div>
          <div className="topbar-actions">
            <Link
              className="search-launch"
              to={`${location.pathname.startsWith("/photos") ? "/photos" : "/"}?${favorite ? "favorite=true&" : ""}focus=search`}
              aria-label="搜索当前媒体库"
            >
              <Icon name="search" size={17} />
              <span>搜索你的影像</span>
              <kbd>/</kbd>
            </Link>
            <HealthStatus />
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
        <footer className="site-footer">
          <span>
            帧藏 <span className="footer-slash">/</span> 留住值得回看的每一帧
          </span>
          <span>YOUR STORIES. YOUR SPACE.</span>
        </footer>
      </div>
      <TaskNotifications />
    </div>
  );
}
