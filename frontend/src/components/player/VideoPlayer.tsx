import { useEffect, useRef, useState } from "react";
import type { PlaybackInfo } from "../../api/types";
import Icon from "../Icon";
import PlayerControls from "./PlayerControls";
import { usePlayerCaptions } from "./PlayerCaptions";
import WasmMedia from "./WasmMedia";
import { usePlayerDubbing } from "./PlayerDubbing";
import WasmCaptions from "./WasmCaptions";
import { AutoBlackBarController } from "./blackBars";

interface Props {
  autoplay?: boolean;
  autoCropBlackBars?: boolean;
  onAutoCropBlackBarsChange?: (enabled: boolean) => void;
  cinema?: boolean;
  onCinema?: () => void;
  info: PlaybackInfo;
  onProgress: (positionMs: number, playedMs: number, finished: boolean) => void;
  onNeedRefresh?: (automatic?: boolean) => void;
  onRetryPreparation?: () => void;
  onNeedCompatibility?: (positionMs: number) => void;
  retrying?: boolean;
}

export default function VideoPlayer({ autoplay = false, autoCropBlackBars = false, onAutoCropBlackBarsChange, cinema, onCinema, info, onProgress, onNeedRefresh }: Props) {
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
  const dubbing = usePlayerDubbing(info.video_id);
  const playback = dubbing.track ? { ...info, mode: "wasm" as const,
    stream_url: dubbing.track.stream_url, source_extension: dubbing.track.source_extension || "mp4",
    container: dubbing.track.container || "mp4", video_codec: dubbing.track.video_codec || "h264",
    audio_codec: dubbing.track.audio_codec || "aac" } : info;
  const restore = useRef<{ videoId: number; position: number; playing: boolean; volume: number; muted: boolean; rate: number } | null>(null);
  const releasing = useRef<Promise<unknown>>(Promise.resolve());
  const progressRef = useRef(onProgress);
  progressRef.current = onProgress;
  const startRef = useRef(info.start_position_ms);
  startRef.current = info.start_position_ms;

  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface || !autoCropBlackBars) return;
    const controller = new AutoBlackBarController(surface);
    controller.start();
    return () => controller.stop();
  }, [autoCropBlackBars, info.video_id, attempt]);

  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    // Each effect owns its container; late cleanup cannot remove a newer player.
    const mount = document.createElement("div");
    mount.className = "wasm-engine-mount";
    surface.appendChild(mount);
    const previous = restore.current?.videoId === info.video_id ? restore.current : null;
    const player = new WasmMedia(mount);
    if (previous) {
      player.volume = previous.volume;
      player.muted = previous.muted;
      player.playbackRate = previous.rate;
    }
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
      if ((previous ? previous.playing : autoplay) && !autoplayAttempted) {
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
    if (playback.stream_url && playback.mode === "wasm")
      void releasing.current.then(() => {
        if (active) return player.load(playback.stream_url!, playback.source_extension || undefined,
          previous ? previous.position * 1000 : startRef.current, playback.video_codec);
      });
    else {
      setError(info.mode === "wasm" ? info.reason : "播放服务需要更新，请刷新页面后重试。");
      setBuffering(false);
    }
    return () => {
      active = false;
      if (player.readyState) restore.current = {
        videoId: info.video_id, position: player.currentTime, playing: !player.paused && !player.ended,
        volume: player.volume, muted: player.muted, rate: player.playbackRate,
      };
      report(true);
      listeners.forEach(([name, fn]) => player.removeEventListener(name, fn));
      window.removeEventListener("pagehide", hide);
      if (mediaRef.current === player) mediaRef.current = null;
      // Silence immediately, then fully release the old audio renderer before
      // loading another source. Rapid selections share the same cleanup barrier.
      player.muted = true;
      const stopped = player.destroy().finally(() => mount.remove());
      releasing.current = Promise.allSettled([releasing.current, stopped]);
    };
  }, [info.video_id, playback.stream_url, playback.mode, playback.source_extension, attempt, autoplay]);

  const retry = () => {
    if (onNeedRefresh) onNeedRefresh();
    else setAttempt(value => value + 1);
  };
  return <div className="flex flex-col gap-2">
    <div ref={stageRef} tabIndex={0} role="region"
      aria-label="视频播放区域（空格播放，方向键快进，F 全屏）"
      className="player-stage relative aspect-video w-full overflow-hidden rounded-lg bg-black"
      data-player-engine="wasm" data-audio-track={dubbing.track?.id ?? "original"} data-controls-visible={controlsVisible} data-player-state={error ? "error" : buffering ? "loading" : ready ? "ready" : "idle"}>
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
      <PlayerControls videoRef={mediaRef} media={media} stageRef={stageRef} captions={captions} dubbing={dubbing}
        unavailable={!ready || !!error} cinema={cinema} onCinema={onCinema}
        autoCropBlackBars={autoCropBlackBars} onAutoCropBlackBarsChange={onAutoCropBlackBarsChange}
        onVisibilityChange={setControlsVisible} />
    </div>
    <div className="wasm-player-status">
      <span><i aria-hidden="true" />{dubbing.track ? dubbing.track.label + " · AI 配音" : "独立解码 · 原声播放"}</span>
      <span>{[playback.container?.split(",")[0], playback.video_codec, playback.audio_codec].filter(Boolean).join(" / ").toUpperCase()}</span>
    </div>
  </div>;
}
