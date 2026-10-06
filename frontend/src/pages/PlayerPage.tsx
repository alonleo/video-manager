import { useCallback, useRef, useState } from "react";
import Icon from "../components/Icon";
import { MoveVideoButton } from "../components/DirectoryTools";
import RemoveVideoButton from "../components/RemoveVideoButton";
import DeleteVideoSourceButton from "../components/DeleteVideoSourceButton";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getPlaybackInfo,
  retryPlayback,
  getVideo,
  saveProgress,
  updateVideo,
} from "../api/client";
import VideoPlayer from "../components/player/VideoPlayer";
import VideoTagPicker from "../components/VideoTagPicker";

import BasicVideoActions from "../components/BasicVideoActions";
import EnhanceVideoActions from "../components/EnhanceVideoActions";
import { cn } from "../lib/cn";
import { readPlaybackPreferences, savePlaybackPreferences } from "../lib/playbackPreferences";
import {
  formatDate,
  formatDuration,
  formatResolution,
  formatSize,
} from "../lib/format";

export default function PlayerPage() {
  const { id: idParam } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const compatible = searchParams.get("compatible") === "1";
  const [compatibilityStart, setCompatibilityStart] = useState({
    id: 0,
    position: 0,
  });
  const librarySearch =
    typeof location.state?.librarySearch === "string" &&
    location.state.librarySearch.startsWith("?")
      ? location.state.librarySearch
      : "";
  const libraryReturnState = {
    restoreLibraryKey:
      typeof location.state?.libraryEntryKey === "string"
        ? location.state.libraryEntryKey
        : undefined,
  };
  const id = Number(idParam);
  const qc = useQueryClient();
  const [cinema, setCinema] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const automaticRefresh = useRef("");
  const [actionToolbar, setActionToolbar] = useState<HTMLDivElement | null>(null);
  const [playbackPreferences, setPlaybackPreferences] = useState(readPlaybackPreferences);
  const autoplay = playbackPreferences.autoplayVideos;

  const video = useQuery({
    queryKey: ["video", id],
    queryFn: () => getVideo(id),
    enabled: !!id,
  });

  const playback = useQuery({
    queryKey: ["playback", id, refreshKey, compatible],
    queryFn: () => getPlaybackInfo(id, compatible),
    refetchInterval: (query) =>
      ["queued", "preparing"].includes(
        query.state.data?.preparation_state ?? "",
      )
        ? 1000
        : false,
    enabled: !!id && !!video.data && video.data.media_type !== "photo",
  });

  const preparationRetry = useMutation({
    mutationFn: () => retryPlayback(id, compatible),
    onSuccess: (data) =>
      qc.setQueryData(["playback", id, refreshKey, compatible], data),
  });

  // 进度上报用 mutation 但故意忽略错误：网络抖动不该打断播放
  const { mutate: reportProgress } = useMutation({
    mutationFn: (body: {
      position_ms: number;
      played_ms: number;
      finished: boolean;
    }) => saveProgress(id, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["videos"], refetchType: "none" }),
    onError: () => {
      /* 静默 */
    },
  });

  const patchMutation = useMutation({
    mutationFn: (patch: {
      is_favorite?: boolean;
      title?: string;
      rating?: number;
    }) => updateVideo(id, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["video", id] });
      qc.invalidateQueries({ queryKey: ["videos"] });
    },
  });

  const handleProgress = useCallback(
    (positionMs: number, playedMs: number, finished: boolean) => {
      reportProgress({
        position_ms: positionMs,
        played_ms: playedMs,
        finished,
      });
    },
    [reportProgress],
  );

  if (video.isLoading) {
    return (
      <div className="grid h-full place-items-center text-muted">加载中…</div>
    );
  }

  if (video.isError || !video.data) {
    return (
      <div className="grid h-full place-items-center">
        <div className="text-center">
          <div className="mb-3 text-red-400">
            加载失败：{(video.error as Error)?.message ?? "视频不存在"}
          </div>
          <Link
            to={`/${librarySearch}`}
            state={libraryReturnState}
            className="btn-outline"
          >
            返回视频库
          </Link>
        </div>
      </div>
    );
  }

  const v = video.data;
  if (v.media_type === "photo") return <Navigate to={`/photos/${id}`} replace state={location.state} />;

  const metaRows: [string, string][] = [
    ["时长", formatDuration(v.duration_ms)],
    ["分辨率", formatResolution(v.width, v.height)],
    ["帧率", v.fps ? `${v.fps} fps` : "—"],
    ["容器", v.container ?? "—"],
    [
      "视频编码",
      [v.video_codec, v.video_profile].filter(Boolean).join(" / ") || "—",
    ],
    [
      "音频编码",
      [v.audio_codec, v.audio_channels ? `${v.audio_channels}ch` : null]
        .filter(Boolean)
        .join(" / ") || "—",
    ],
    ["像素格式", v.pix_fmt ?? "—"],
    [
      "码率",
      v.bitrate_bps ? `${(v.bitrate_bps / 1_000_000).toFixed(2)} Mbps` : "—",
    ],
    ["大小", formatSize(v.size_bytes)],
    ["添加时间", formatDate(v.created_at)],
  ];

  return (
    <div className={cn("page player-page", cinema && "player-cinema")}>
      <div className="player-heading">
        <Link
          to={`/${librarySearch}`}
          state={libraryReturnState}
          className="btn-ghost mt-0.5"
        >
          <Icon name="back" size={17} />
          返回
        </Link>
        <div>
          <p className="eyebrow">
            <span /> NOW SHOWING · 放映室
          </p>
          <h1>{v.title}</h1>
        </div>
        <div className="player-action-row">
          <div className="player-basic-action-slot" ref={setActionToolbar} />
          <MoveVideoButton key={v.id} video={v} />
          <DeleteVideoSourceButton key={`delete-source-${v.id}`} video={v} onDeleted={result => {
            if (result.video_removed) {
              navigate(`/${librarySearch}`, { replace: true, state: {
                ...libraryReturnState,
                removalNotice: result.warning ?? `已永久删除「${v.title}」的磁盘源文件，并移除收录。`,
              } });
            } else {
              setRefreshKey(key => key + 1);
            }
          }} />
          <RemoveVideoButton key={`remove-${v.id}`} video={v} onRemoved={() => {
            navigate(`/${librarySearch}`, { replace: true, state: {
              ...libraryReturnState,
              removalNotice: `已移除「${v.title}」的收录，原文件已保留；再次扫描对应存储源可重新收录。`,
            } });
          }} />
          <button
            className="btn-outline"
            aria-pressed={cinema}
            onClick={() => setCinema(!cinema)}
          >
            <Icon name="film" size={16} />
            {cinema ? "退出宽屏" : "宽屏模式"}
          </button>
          <button
            className={cn(
              "btn-outline",
              v.is_favorite && "border-amber-400/60 text-amber-400",
            )}
            disabled={patchMutation.isPending}
            aria-pressed={v.is_favorite}
            onClick={() =>
              patchMutation.mutate({ is_favorite: !v.is_favorite })
            }
          >
            <Icon name="star" size={16} />
            {v.is_favorite ? "已收藏" : "收藏"}
          </button>
        </div>
      </div>
      {patchMutation.isError && (
        <div className="inline-alert player-feedback" role="alert">
          收藏保存失败：{patchMutation.error.message}
        </div>
      )}
      {preparationRetry.isError && (
        <div className="inline-alert mb-4" role="alert">
          重试失败：{preparationRetry.error.message}
        </div>
      )}
      <div className="player-grid">
        {/* ---------- 左：播放器 + 来源 ---------- */}
        <div className="flex flex-col gap-4">
          {playback.data ? (
            <VideoPlayer
              autoplay={autoplay}
              autoCropBlackBars={playbackPreferences.autoCropBlackBars}
              onAutoCropBlackBarsChange={enabled => setPlaybackPreferences(current =>
                savePlaybackPreferences({ ...current, autoCropBlackBars: enabled }))}
              cinema={cinema}
              onCinema={() => setCinema(c => !c)}
              key={`${id}-${refreshKey}`}
              info={{
                ...playback.data,
                start_position_ms:
                  compatibilityStart.id === id &&
                  compatibilityStart.position > 0
                    ? compatibilityStart.position
                    : playback.data.start_position_ms,
              }}
              onProgress={handleProgress}
              onNeedRefresh={(automatic) => {
                const scope = `${id}-${compatible}`;
                if (automatic && automaticRefresh.current === scope) return;
                if (automatic) automaticRefresh.current = scope;
                setCompatibilityStart({ id: 0, position: 0 });
                setRefreshKey((k) => k + 1);
              }}
              onRetryPreparation={() => preparationRetry.mutate()}
              onNeedCompatibility={(position) => {
                setCompatibilityStart({ id, position });
                const next = new URLSearchParams(searchParams);
                next.set("compatible", "1");
                setSearchParams(next, { replace: true, state: location.state });
              }}
              retrying={preparationRetry.isPending}
            />
          ) : playback.isError ? (
            <div className="card grid aspect-video place-items-center p-6 text-center">
              <div>
                <div className="mb-1 text-red-400">无法获取播放信息</div>
                <div className="text-sm text-muted">
                  {(playback.error as Error).message}
                </div>
                <button
                  className="btn-outline mt-4"
                  onClick={() => playback.refetch()}
                >
                  重新加载
                </button>
              </div>
            </div>
          ) : (
            <div className="card grid aspect-video place-items-center text-muted">
              准备播放…
            </div>
          )}

          <BasicVideoActions
            key={`basic-actions-${v.id}`}
            toolbar={actionToolbar}
            video={v}
            onReplaced={() => setRefreshKey((k) => k + 1)}
          />
          <EnhanceVideoActions key={`enhance-${v.id}`} video={v} toolbar={actionToolbar} />


          {v.description && (
            <div className="card p-4">
              <div className="mb-2 text-sm font-medium text-muted">简介</div>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">
                {v.description}
              </p>
            </div>
          )}

          <div className="card p-4">
            <div className="mb-3 text-sm font-medium text-muted">
              来源（{v.sources.length}）
            </div>
            <div className="flex flex-col gap-2">
              {v.sources.map((s) => (
                <div
                  key={s.id}
                  className="flex items-center gap-2 rounded border border-border bg-panel2 px-3 py-2 text-xs"
                >
                  <span
                    className={cn(
                      "chip",
                      s.source_type === "local" &&
                        "bg-emerald-500/15 text-emerald-400",
                      s.source_type === "openlist" &&
                        "bg-sky-500/15 text-sky-400",
                      s.source_type === "url" &&
                        "bg-purple-500/15 text-purple-400",
                    )}
                  >
                    {s.source_type}
                  </span>
                  {s.is_primary && (
                    <span className="chip bg-accent/20 text-accent">主</span>
                  )}
                  {!s.available && (
                    <span className="chip bg-red-500/15 text-red-400">
                      不可用
                    </span>
                  )}
                  <span
                    className="min-w-0 flex-1 truncate font-mono text-muted"
                    title={s.uri}
                  >
                    {s.uri}
                  </span>
                  <span className="shrink-0 text-muted">
                    {formatSize(s.size_bytes)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ---------- 右：元数据 + 标签 ---------- */}
        <div className="player-sidebar flex flex-col gap-4">
          <div className="card p-4">
            <div className="mb-3 text-sm font-medium text-muted">媒体信息</div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              {metaRows.map(([k, val]) => (
                <div key={k} className="contents">
                  <dt className="text-muted">{k}</dt>
                  <dd className="break-words text-right">{val}</dd>
                </div>
              ))}
            </dl>
          </div>

          <VideoTagPicker key={v.id} video={v} />

          {v.meta && Object.keys(v.meta).length > 0 && (
            <details className="card p-4">
              <summary className="cursor-pointer text-sm font-medium text-muted">
                原始探测数据
              </summary>
              <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all text-[11px] text-muted">
                {JSON.stringify(v.meta, null, 2)}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
