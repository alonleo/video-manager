import { useEffect, useRef, useState } from "react";
import type { PlaybackInfo } from "../../api/types";
import Icon from "../Icon";
import PlayerControls from "./PlayerControls";
import { usePlayerCaptions } from "./PlayerCaptions";
import WasmMedia from "./WasmMedia";
import WasmCaptions from "./WasmCaptions";

interface Props {
  autoplay?: boolean;
  cinema?: boolean;
  onCinema?: () => void;
  info: PlaybackInfo;
  onProgress: (positionMs: number, playedMs: number, finished: boolean) => void;
  onNeedRefresh?: (automatic?: boolean) => void;
  onRetryPreparation?: () => void;
  onNeedCompatibility?: (positionMs: number) => void;
  retrying?: boolean;
}

export default function VideoPlayer({ autoplay = false, cinema, onCinema, info, onProgress, onNeedRefresh }: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const mediaRef = useRef<WasmMedia | null>(null);
  const [media, setMedia] = useState<WasmMedia | null>(null);
  const [ready, setReady] = useState(false);
  const [started, setStarted] = useState(false);
  const [buffering, setBuffering] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [controlsVisible, setControlsVisible] = useState(true);
  const captions = usePlayerCaptions(info.video_id);
  const progressRef = useRef(onProgress);
  progressRef.current = onProgress;
  const startRef = useRef(info.start_position_ms);
  startRef.current = info.start_position_ms;

  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    // Each effect owns its container; late cleanup cannot remove a newer player.
    const mount = document.createElement("div");
    mount.className = "wasm-engine-mount";
    surface.appendChild(mount);
    const player = new WasmMedia(mount);
    mediaRef.current = player;
    setMedia(player);
    setReady(false); setStarted(false); setBuffering(true); setError(null);
    let lastSaved = -1;
    let active = true;
    const report = (force = false) => {
      if (!player.readyState || !player.duration || player.seeking) return;
      const position = Math.floor(player.currentTime * 1000);
      if (!force && Math.abs(position - lastSaved) < 5000) return;
      lastSaved = position;
      progressRef.current(position, position, player.ended || player.currentTime / player.duration > 0.95);
    };
    let autoplayAttempted = false;
    const loaded = () => {
      if (!active) return;
      setReady(true); setBuffering(false);
      if (autoplay && !autoplayAttempted) {
        autoplayAttempted = true;
        void player.play().catch(() => {});
      }
    };
    const waiting = () => { if (active) setBuffering(true); };
    const canplay = () => { if (active) setBuffering(false); };
    const playing = () => { if (active) { setStarted(true); setBuffering(false); } };
    const failed = () => {
      if (active) { setError(player.error?.message || "无法读取此视频，请检查文件是否完整。"); setBuffering(false); }
    };
    const time = () => report();
    const paused = () => report(true);
    const listeners: [string, () => void][] = [
      ["loadedmetadata", loaded], ["waiting", waiting], ["canplay", canplay],
      ["playing", playing], ["error", failed], ["timeupdate", time],
      ["pause", paused], ["seeked", paused], ["ended", paused],
    ];
    listeners.forEach(([name, fn]) => player.addEventListener(name, fn));
    const hide = () => report(true);
    window.addEventListener("pagehide", hide);
    if (info.stream_url && info.mode === "wasm")
      void player.load(info.stream_url, info.source_extension || undefined, startRef.current, info.video_codec);
    else {
      setError(info.mode === "wasm" ? info.reason : "播放服务需要更新，请刷新页面后重试。");
      setBuffering(false);
    }
    return () => {
      active = false;
      report(true);
      listeners.forEach(([name, fn]) => player.removeEventListener(name, fn));
      window.removeEventListener("pagehide", hide);
      if (mediaRef.current === player) mediaRef.current = null;
      void player.destroy().finally(() => mount.remove());
    };
  }, [info.video_id, info.stream_url, info.mode, info.source_extension, attempt, autoplay]);

  const retry = () => {
    if (onNeedRefresh) onNeedRefresh();
    else setAttempt(value => value + 1);
  };
  return <div className="flex flex-col gap-2">
    <div ref={stageRef} tabIndex={0} role="region"
      aria-label="视频播放区域（空格播放，方向键快进，F 全屏）"
      className="player-stage relative aspect-video w-full overflow-hidden rounded-lg bg-black"
      data-player-engine="wasm" data-controls-visible={controlsVisible} data-player-state={error ? "error" : buffering ? "loading" : ready ? "ready" : "idle"}>
      <div ref={surfaceRef} data-player-surface className="wasm-player-surface" />
      <WasmCaptions media={media} captions={captions} />
      {!error && buffering && <div className="wasm-player-overlay" role="status">
        <span className="wasm-loading-ring" aria-hidden="true" />
        <p>{ready ? "正在解码…" : "正在读取视频与解码组件…"}</p>
      </div>}
      {!error && ready && !started && !buffering && <div className="wasm-player-start">
        <button type="button" aria-label="开始播放" onClick={() => { void mediaRef.current?.play().catch(() => {}); }}>
          <Icon name="play" size={34} />
        </button>
        <span>点击播放</span>
      </div>}
      {error && <div className="wasm-player-overlay" role="alert">
        <Icon name="film" size={30} />
        <h3>暂时无法播放</h3>
        <p>{error.slice(0, 240)}</p>
        <button type="button" className="wasm-retry" onClick={retry}>重新加载</button>
      </div>}
      <PlayerControls videoRef={mediaRef} stageRef={stageRef} captions={captions}
        unavailable={!ready || !!error} cinema={cinema} onCinema={onCinema} onVisibilityChange={setControlsVisible} />
    </div>
    <div className="wasm-player-status">
      <span><i aria-hidden="true" />独立解码 · 原文件播放</span>
      <span>{[info.container?.split(",")[0], info.video_codec, info.audio_codec].filter(Boolean).join(" / ").toUpperCase()}</span>
    </div>
  </div>;
}
