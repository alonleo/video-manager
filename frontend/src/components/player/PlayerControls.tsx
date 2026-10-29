import { useCallback, useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import { createPortal } from "react-dom";
import Icon from "../Icon";
import type WasmMedia from "./WasmMedia";
import PlayerCaptions, { type PlayerCaptionsState } from "./PlayerCaptions";
import PlayerDubbing, { type PlayerDubbingState } from "./PlayerDubbing";
import { formatTime } from "../../lib/format";
const playerTime = (ms: number) => ms > 0 ? formatTime(ms) : "0:00";

type Props = {
  videoRef: RefObject<WasmMedia | null>;
  media: WasmMedia | null;
  stageRef: RefObject<HTMLDivElement | null>;
  captions: PlayerCaptionsState;
  dubbing: PlayerDubbingState;
  unavailable: boolean;
  cinema?: boolean;
  onCinema?: () => void;
  autoCropBlackBars?: boolean;
  onAutoCropBlackBarsChange?: (enabled: boolean) => void;
  onVisibilityChange: (visible: boolean) => void;
};
export default function PlayerControls({ videoRef, media, stageRef, captions, dubbing, unavailable, cinema, onCinema, autoCropBlackBars, onAutoCropBlackBarsChange, onVisibilityChange }: Props) {
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [rate, setRate] = useState(1);
  const [fullscreen, setFullscreen] = useState(false);
  const [visible, setVisible] = useState(true);
  const [focused, setFocused] = useState(false);
  const [menu, setMenu] = useState<"captions" | "dubbing" | "speed" | "volume" | null>(null);
  const [notice, setNotice] = useState("");
  const controls = useRef<HTMLDivElement>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const [portrait, setPortrait] = useState(() => window.matchMedia("(max-width: 700px) and (orientation: portrait)").matches);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 700px) and (orientation: portrait)");
    const update = () => setPortrait(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const state = useRef({ playing, menu });
  state.current = { playing, menu };
  const reveal = useCallback(() => {
    setVisible(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (state.current.playing && !state.current.menu) setVisible(false);
    }, 3000);
  }, []);
  const togglePlay = useCallback(async () => {
    const v = videoRef.current;
    if (!v || unavailable) return;
    setNotice("");
    if (!v.paused) v.pause();
    else try { await v.play(); } catch { setNotice("播放未能开始，请重试。"); }
    reveal();
  }, [videoRef, unavailable, reveal]);
  const toggleFullscreen = useCallback(async () => {
    const stage = stageRef.current;
    if (!stage) return;
    try {
      if (document.fullscreenElement === stage) await document.exitFullscreen();
      else if (stage.requestFullscreen) await stage.requestFullscreen();
      else setNotice("当前浏览器不支持全屏，请使用宽屏模式。");
    } catch { setNotice("暂时无法进入全屏，请重试。"); }
    reveal();
  }, [stageRef, reveal]);
  useEffect(() => {
    const v = videoRef.current, stage = stageRef.current;
    if (!v || !stage) return;
    const resize = new ResizeObserver(() => stage.style.setProperty("--player-height", stage.clientHeight + "px"));
    resize.observe(stage);
    const sync = () => {
      setPlaying(!v.paused && !v.ended);
      setTime(v.currentTime || 0);
      setDuration(Number.isFinite(v.duration) ? v.duration : 0);
      setVolume(v.volume); setMuted(v.muted); setRate(v.playbackRate);
      let end = 0;
      for (let i = 0; i < v.buffered.length; i++) {
        if (v.buffered.start(i) <= v.currentTime && v.buffered.end(i) >= v.currentTime) end = v.buffered.end(i);
      }
      setBuffered(end);
    };
    const events = ["loadedmetadata", "durationchange", "timeupdate", "progress", "play", "pause", "ended", "volumechange", "ratechange", "emptied"];
    events.forEach(e => v.addEventListener(e, sync));
    const full = () => {
      const active = document.fullscreenElement === stage;
      v.setFullscreen(active);
      setFullscreen(active);
      setMenu(null);
      reveal();
    };
    const click = () => { void togglePlay(); };
    const double = () => { void toggleFullscreen(); };
    const keys = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("button,input,select,textarea,a,[contenteditable]")) return;
      if (e.altKey || e.ctrlKey || e.metaKey || unavailable) return;
      const key = e.key.toLowerCase();
      if (![" ", "k", "m", "f", "arrowleft", "arrowright", "arrowup", "arrowdown"].includes(key)) return;
      e.preventDefault(); reveal();
      if (key === " " || key === "k") void togglePlay();
      else if (key === "m") v.muted = !v.muted;
      else if (key === "f") void toggleFullscreen();
      else if (key === "arrowleft" || key === "arrowright") {
        if (Number.isFinite(v.duration)) v.currentTime = Math.min(v.duration, Math.max(0, v.currentTime + (key === "arrowright" ? 5 : -5)));
      } else { v.volume = Math.min(1, Math.max(0, v.volume + (key === "arrowup" ? 0.1 : -0.1))); v.muted = false; }
    };
    stage.addEventListener("pointermove", reveal);
    stage.addEventListener("pointerdown", reveal);
    stage.addEventListener("keydown", keys);
    stage.addEventListener("focusin", reveal);
    const surface = stage.querySelector("[data-player-surface]");
    surface?.addEventListener("click", click); surface?.addEventListener("dblclick", double);
    document.addEventListener("fullscreenchange", full);
    full();
    sync();
    return () => {
      resize.disconnect();
      events.forEach(e => v.removeEventListener(e, sync));
      stage.removeEventListener("pointermove", reveal);
      stage.removeEventListener("pointerdown", reveal);
      stage.removeEventListener("keydown", keys);
      stage.removeEventListener("focusin", reveal);
      surface?.removeEventListener("click", click); surface?.removeEventListener("dblclick", double);
      document.removeEventListener("fullscreenchange", full);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [videoRef, media, stageRef, togglePlay, toggleFullscreen, reveal, unavailable]);
  useEffect(() => { reveal(); }, [playing, menu, reveal]);
  useEffect(() => {
    if (!menu) return;
    const outside = (e: PointerEvent) => {
      if (!controls.current?.contains(e.target as Node) && !sheet.current?.contains(e.target as Node)) setMenu(null);
    };
    const escape = (e: KeyboardEvent) => {
      if (portrait && e.key === "Tab" && sheet.current) {
        const elements = Array.from(sheet.current.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]'));
        const first = elements[0], last = elements.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
      if (e.key === "Escape") {
        e.preventDefault(); e.stopPropagation();
        controls.current?.querySelector<HTMLButtonElement>('[aria-expanded="true"]')?.focus();
        setMenu(null);
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape, true); };
  }, [menu, portrait]);
  useEffect(() => {
    if (!menu || !portrait) return;
    const trigger = controls.current?.querySelector<HTMLButtonElement>('[aria-expanded="true"]');
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    sheet.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    return () => {
      document.body.style.overflow = previous;
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, [menu, portrait]);
  const shown = visible || !playing || !!menu || focused;
  useEffect(() => { onVisibilityChange(shown); }, [shown, onVisibilityChange]);
  const toggleMenu = (next: typeof menu) => setMenu(v => v === next ? null : next);
  const percent = duration ? Math.min(100, time / duration * 100) : 0;
  const menuLabel = menu === "captions" ? "字幕菜单" : menu === "dubbing" ? "语音配音菜单" : menu === "speed" ? "播放速度" : "音量设置";
  const menuPanel = menu && <div id="player-control-menu" className={`player-control-menu menu-${menu}`} role="region"
        aria-label={menu === "captions" ? "字幕菜单" : menu === "dubbing" ? "语音配音菜单" : menu === "speed" ? "播放速度" : "音量设置"}>
        {menu === "captions" ? <PlayerCaptions captions={captions} /> : menu === "dubbing" ? <PlayerDubbing dubbing={dubbing} subtitleId={captions.track?.id} onSelect={() => setMenu(null)} /> : menu === "speed" ? <>
          <div className="player-menu-title"><strong>播放速度</strong></div>
          <div className="player-speed-options">
            {[0.5, 0.75, 1, 1.25, 1.5, 2].map(value => <button type="button" key={value} aria-pressed={rate === value}
              onClick={() => { if (videoRef.current) videoRef.current.playbackRate = value; setMenu(null); }}>
              {value === 1 ? "正常" : value + "×"}
            </button>)}
          </div>
        </> : <>
          <div className="player-menu-title"><strong>音量</strong><span>{muted ? 0 : Math.round(volume * 100)}%</span></div>
          <div className="player-volume-row">
            <button type="button" aria-label={muted ? "取消静音" : "静音"} onClick={() => { if (videoRef.current) videoRef.current.muted = !muted; }}>
              <Icon name={muted || volume === 0 ? "muted" : "volume"} />
            </button>
            <input type="range" aria-label="音量" min="0" max="1" step="0.05" value={muted ? 0 : volume}
              onChange={e => { if (videoRef.current) { videoRef.current.volume = Number(e.target.value); videoRef.current.muted = false; } }} />
          </div>
        </>}
      </div>;
  return <>
    {menu && portrait && createPortal(
      <div className="player-settings-backdrop" onClick={event => { if (event.target === event.currentTarget) setMenu(null); }}>
        <div ref={sheet} className="player-settings-sheet" role="dialog" aria-modal="true" aria-label={menuLabel}>
          <div className="player-settings-sheet-header">
            <span>{menuLabel}</span>
            <button type="button" aria-label="关闭播放设置" onClick={() => setMenu(null)}><Icon name="close" size={20} /></button>
          </div>
          {menuPanel}
        </div>
      </div>, document.fullscreenElement || document.body)}
    {notice && <div className="player-notice" role="status">{notice}<button type="button" aria-label="关闭提示" onClick={() => setNotice("")}>×</button></div>}
    <div ref={controls} className={`custom-player-controls ${shown ? "is-visible" : ""}`}
      onFocusCapture={e => setFocused((e.target as HTMLElement).matches(":focus-visible"))}
      onBlurCapture={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false); }}
      aria-label="播放控制栏">
      {!portrait && menuPanel}
      <div className="player-seek-row" style={{ "--played": percent + "%", "--buffered": (duration ? buffered / duration * 100 : 0) + "%" } as CSSProperties}>
        <input type="range" aria-label="播放进度" aria-valuetext={`${playerTime(time * 1000)} / ${playerTime(duration * 1000)}`}
          min="0" max={duration || 0} step="0.1" value={Math.min(time, duration)} disabled={unavailable || !duration}
          onChange={e => { if (videoRef.current) videoRef.current.currentTime = Number(e.target.value); setTime(Number(e.target.value)); reveal(); }} />
      </div>
      <div className="player-control-row">
        <button type="button" className="player-control-button" aria-label={playing ? "暂停" : "播放"} title={playing ? "暂停（空格）" : "播放（空格）"}
          disabled={unavailable} onClick={() => void togglePlay()}><Icon name={playing ? "pause" : "play"} size={22} /></button>
        <span className="player-time"><span>{playerTime(time * 1000)}</span><i>/</i>{playerTime(duration * 1000)}</span>
        <div className="player-control-spacer" />
        <button type="button" className="player-control-button" aria-label="音量设置" title="音量（M 静音）"
          aria-expanded={menu === "volume"} aria-controls={menu === "volume" ? "player-control-menu" : undefined} onClick={() => toggleMenu("volume")}>
          <Icon name={muted || volume === 0 ? "muted" : "volume"} size={20} /></button>
        <button type="button" className="player-control-button player-speed-button" aria-label="倍速" title="播放速度" disabled={unavailable}
          aria-expanded={menu === "speed"} aria-controls={menu === "speed" ? "player-control-menu" : undefined} onClick={() => toggleMenu("speed")}>{rate === 1 ? "倍速" : rate + "×"}</button>
        <button type="button" className={`player-control-button ${captions.url ? "captions-active" : ""}`} aria-label="字幕" title="字幕与语言"
          aria-expanded={menu === "captions"} aria-controls={menu === "captions" ? "player-control-menu" : undefined} onClick={() => toggleMenu("captions")}>
          <Icon name="captions" size={22} /></button>
        <button type="button" className={`player-control-button player-dubbing-button ${dubbing.track ? "player-control-active" : ""}`} aria-label="语音配音" title="语音配音与音轨选择"
          aria-expanded={menu === "dubbing"} aria-controls={menu === "dubbing" ? "player-control-menu" : undefined} onClick={() => toggleMenu("dubbing")}>配音</button>
        {onAutoCropBlackBarsChange && <button type="button" className={`player-control-button ${autoCropBlackBars ? "player-control-active" : ""}`}
          aria-label={autoCropBlackBars ? "关闭自动去黑边" : "开启自动去黑边"} aria-pressed={autoCropBlackBars}
          title={autoCropBlackBars ? "自动去黑边：已开启" : "自动去黑边：已关闭"}
          onClick={() => onAutoCropBlackBarsChange(!autoCropBlackBars)}><Icon name="crop" size={20} /></button>}
        {onCinema && <button type="button" className="player-control-button player-cinema-button" aria-label={cinema ? "退出宽屏" : "宽屏模式"}
          aria-pressed={cinema} title="宽屏模式" onClick={onCinema}><Icon name="film" size={19} /></button>}
        <button type="button" className="player-control-button" aria-label={fullscreen ? "退出全屏" : "全屏"} title="全屏（F）"
          onClick={() => void toggleFullscreen()}><Icon name={fullscreen ? "fullscreenExit" : "fullscreen"} size={20} /></button>
      </div>
    </div>
  </>;
}
