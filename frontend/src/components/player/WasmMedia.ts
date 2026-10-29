/**
 * Media controls backed by independent Wasm decoders, not HTMLMediaElement.
 * Async engine operations are serialized; superseded seeks are coalesced.
 */
const RENDER_MODE_FIT = 0; // Preserve the whole frame, including in portrait fullscreen.
const ENGINE_ROOT = "/player-engine/libmedia-1.3.1";
import { browserPlaybackCapacity, choosePreloadSeconds, estimateBufferedSeconds } from "./playbackBuffer";
type Stream = { mediaType: string; codecparProxy: { codecId: number; codecType: number }; metadata?: Record<string, unknown> };
type Engine = {
  currentTime: bigint;
  load(source: string, options: { ext?: string }): Promise<void>;
  play(options: { audio: boolean; video: boolean; subtitle: boolean }): Promise<void>;
  playNextFrame(): Promise<void>;
  pause(): Promise<void>;
  seek(ms: bigint): Promise<void>;
  resume(): Promise<void>;
  destroy(): Promise<void>;
  stop(): Promise<void>;
  getDuration(): bigint;
  getMinStartPTS(): bigint;
  getStreams(): Stream[];
  getStats(): { videoFrameRenderCount?: number; videoPacketQueueLength?: number; videoRenderFramerate?: number };
  setVolume(volume: number): void;
  setPlaybackRate(rate: number): void;
  setSubtitleEnable(enabled: boolean): void;
  setRenderMode(mode: number): void;
  resize(width: number, height: number): void;
  isMSE(): boolean;
  on(event: string, callback: (...args: unknown[]) => void): void;
  off(): void;
};
type EngineModule = {
  default: {
    new(options: Record<string, unknown>): Engine;
    setLogLevel(level: number): void;
    Util: { browser: { wasmPlayerSupported?: boolean }; support?: { wasmPlayerSupported?: boolean } };
  };
  AVPlayerSupportedCodecs: number[];
};
let engineModule: Promise<EngineModule> | undefined;
async function loadEngine() {
  engineModule ??= import(/* @vite-ignore */ ENGINE_ROOT + "/avplayer.js").catch(error => {
    engineModule = undefined;
    throw error;
  });
  return engineModule;
}

export default class WasmMedia extends EventTarget {
  paused = true;
  ended = false;
  readyState = 0;
  seeking = false;
  duration = 0;
  error: Error | null = null;
  private bufferedEnd = 0;
  readonly buffered = { length: 0, start: (_i: number) => 0, end: (_i: number) => this.bufferedEnd };
  private engine: Engine | null = null;
  private chain: Promise<void> = Promise.resolve();
  private disposed = false;
  private initialized = false;
  private stoppingOnError = false;
  private position = 0;
  private timelineOffset = 0;
  private gain = 1;
  private silent = false;
  private speed = 1;
  private pendingSeek: number | null = null;
  private seekQueued = false;
  private observer: ResizeObserver | null = null;
  private loading: Promise<void> | null = null;
  private preloadSeconds = 4;
  constructor(private surface: HTMLDivElement) { super(); }
  private emit(name: string) { if (!this.disposed) this.dispatchEvent(new Event(name)); }
  private fail(error: unknown) {
    if (this.disposed) return;
    const detail = error instanceof Error ? error.message : String(error);
    console.warn("Independent decoder:", detail);
    this.error = new Error(detail.includes("[fatal]")
      ? "无法解码此文件：文件可能不完整，或当前独立解码器尚不支持它的封装与编码组合。"
      : /fetch|WebAssembly|CompileError|module script/i.test(detail)
        ? "解码组件加载失败，请检查连接后重新加载。" : detail);
    this.seeking = false;
    this.pendingSeek = null;
    this.paused = true;
    if (!this.stoppingOnError && this.engine) {
      this.stoppingOnError = true;
      void this.engine.stop().catch(() => {});
    }
    this.emit("error");
    this.emit("pause");
  }
  private enqueue(operation: () => Promise<void>) {
    const job = this.chain.then(async () => { if (!this.disposed) await operation(); });
    this.chain = job.catch(error => this.fail(error));
    return job;
  }
  get currentTime() { return this.pendingSeek ?? this.position; }
  set currentTime(value: number) {
    if (!Number.isFinite(value) || !this.readyState) return;
    this.seeking = true;
    this.pendingSeek = Math.min(Math.max(0, value), Math.max(0, this.duration - 0.05));
    this.emit("timeupdate");
    if (this.seekQueued) return;
    this.seekQueued = true;
    void this.enqueue(async () => {
      try {
        while (this.pendingSeek !== null && !this.disposed) {
          const target = this.pendingSeek;
          this.pendingSeek = null;
          if (!this.initialized) {
            // Save a pre-play seek without starting audio.
            this.position = target;
          } else {
            this.emit("waiting");
            await this.engine!.seek(BigInt(Math.round((target + this.timelineOffset) * 1000)));
            this.position = target;
            this.ended = false;
            this.emit("canplay");
          }
          this.emit("timeupdate");
          this.emit("seeked");
        }
      } finally { this.seekQueued = false; this.seeking = false; this.emit("seeked"); }
    }).catch(() => {});
  }
  get volume() { return this.gain; }
  set volume(value: number) {
    this.gain = Math.max(0, Math.min(1, value));
    this.engine?.setVolume(this.silent ? 0 : this.gain);
    this.emit("volumechange");
  }
  get muted() { return this.silent; }
  set muted(value: boolean) {
    this.silent = value;
    this.engine?.setVolume(value ? 0 : this.gain);
    this.emit("volumechange");
  }
  get playbackRate() { return this.speed; }
  set playbackRate(value: number) {
    this.speed = Math.max(0.25, Math.min(4, value));
    this.engine?.setPlaybackRate(this.speed);
    this.emit("ratechange");
  }
  load(url: string, extension?: string, startMs = 0, codecHint?: string | null) {
    this.loading = this.enqueue(async () => {
      this.emit("waiting");
      const module = await loadEngine();
      if (this.disposed) return;
      module.default.setLogLevel(2);
      this.preloadSeconds = choosePreloadSeconds(browserPlaybackCapacity());
      const engine = new module.default({
        container: this.surface,
        wasmBaseUrl: new URL(ENGINE_ROOT, window.location.href).href,
        enableHardware: false,
        enableWebCodecs: false,
        enableWebGPU: false,
        checkUseMSE: () => false,
        enableWorker: true,
        enableAudioWorklet: !!window.isSecureContext,
        preLoadTime: this.preloadSeconds,
      });
      this.engine = engine;
      engine.setRenderMode(RENDER_MODE_FIT);
      // The pinned engine may otherwise force MSE in unsupported environments.
      // Refuse that fallback: this project requires independent decoding.
      Object.defineProperty(engine, "checkUseMSE", { value: async () => false });
      engine.on("error", error => this.fail(error));
      engine.on("time", () => {
        if (!this.seeking && this.pendingSeek === null) this.position = Math.max(0, Number(engine.currentTime) / 1000 - this.timelineOffset);
        const ahead = estimateBufferedSeconds(engine.getStats(), this.preloadSeconds);
        this.bufferedEnd = Math.min(this.duration, this.position + ahead);
        this.buffered.length = ahead > 0 ? 1 : 0;
        this.emit("progress");
        this.emit("timeupdate");
      });
      engine.on("firstVideoRendered", () => {
        this.surface.dataset.rendered = "true";
        this.emit("canplay");
      });
      engine.on("ended", () => {
        this.ended = true; this.paused = true;
        this.position = this.duration;
        this.emit("timeupdate"); this.emit("ended");
      });
      const aliases: Record<string, string> = { vob: "mpeg", ogv: "ogg", m4v: "mp4", "3gp": "mp4" };
      await engine.load(new URL(url, window.location.href).href, { ext: aliases[extension || ""] || extension });
      if (this.disposed) return;
      const streams = engine.getStreams();
      const video = streams.find(s => s.codecparProxy.codecType === 0);
      if (!video) throw new Error("文件中没有可播放的视频轨道。");
      // libmedia 1.3.1 omits the s263 sample-entry mapping. The existing ffprobe
      // metadata supplies this one missing ID; dimensions/packets remain demuxed locally.
      if (video.codecparProxy.codecId === 0 && extension === "3gp" && codecHint === "h263")
        video.codecparProxy.codecId = 4;
      // The FLV demuxer reports Sorenson Spark as H.263, but that decoder cannot play it.
      if (extension === "flv" && codecHint === "flv1")
        throw new Error("当前独立解码器暂不支持 FLV1（Sorenson Spark）；FLV 中的 H.264 可直接播放。");
      if (!module.AVPlayerSupportedCodecs.includes(video.codecparProxy.codecId))
        throw new Error("此视频编码尚未包含在独立解码器中，请在视频信息中查看编码。");
      const audio = streams.find(s => s.codecparProxy.codecType === 1);
      const audioCodec = audio?.codecparProxy.codecId;
      if (audioCodec !== undefined && !module.AVPlayerSupportedCodecs.includes(audioCodec)
          && !(audioCodec >= 65536 && audioCodec <= 65572)
          && !(audioCodec >= 69632 && audioCodec <= 69683))
        throw new Error("此音频编码尚未包含在独立解码器中。");
      this.timelineOffset = Math.max(0, Number(engine.getMinStartPTS()) / 1000);
      this.duration = Math.max(0, Number(engine.getDuration()) / 1000);
      this.position = startMs > 0 && startMs / 1000 < this.duration - 1 ? startMs / 1000 : 0;
      this.readyState = 1;
      engine.setSubtitleEnable(false); // App subtitle tracks are rendered independently.
      engine.setVolume(this.silent ? 0 : this.gain);
      engine.setPlaybackRate(this.speed);
      this.observer = new ResizeObserver(() => {
        if (!this.disposed && this.initialized)
          engine.resize(this.surface.clientWidth, this.surface.clientHeight);
      });
      this.observer.observe(this.surface);
      this.emit("loadedmetadata");
      // Build the video decoder and render the first frame before the user seeks.
      // This does not start audio and keeps the player paused.
      await this.withDeadline(engine.playNextFrame(), engine, 12000);
      this.bufferedEnd = Math.min(this.duration, this.position + this.preloadSeconds);
      this.buffered.length = this.bufferedEnd > this.position ? 1 : 0;
      this.emit("progress");
      this.emit("canplay");
    }).catch(error => { this.fail(error); });
    return this.loading;
  }
  private async withDeadline(operation: Promise<void>, engine: Engine, timeoutMs = 30000) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([operation, new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          void engine.stop().catch(() => {});
          reject(new Error("解码超时：此文件的编码可能暂不支持，或设备解码速度不足。"));
        }, timeoutMs);
      })]);
    } finally { if (timer) clearTimeout(timer); }
  }
  play() {
    // Resume during the user's gesture, before awaiting queued work.
    const resume = this.engine?.resume();
    resume?.catch(() => {});
    return this.enqueue(async () => {
      if (!this.engine || !this.readyState || this.error) throw this.error ?? new Error("播放器尚未准备完成");
      if (!this.paused) return;
      this.emit("waiting");
      const start = this.position;
      this.seeking = !this.initialized && start > 0;
      await resume;
      await this.withDeadline(this.engine.play({ audio: true, video: true, subtitle: false }), this.engine);
      if (this.error) throw this.error;
      if (this.engine.isMSE()) throw new Error("独立解码不可用，已停止浏览器解码回退。");
      if (!this.initialized && start > 0) await this.engine.seek(BigInt(Math.round((start + this.timelineOffset) * 1000)));
      this.seeking = false;
      this.initialized = true; this.paused = false; this.ended = false;
      this.readyState = 2;
      this.engine.resize(this.surface.clientWidth, this.surface.clientHeight);
      this.emit("play"); this.emit("playing");
    });
  }
  pause() {
    void this.enqueue(async () => {
      if (this.engine && this.initialized && !this.paused) await this.engine.pause();
      if (this.engine && this.initialized) this.position = Math.max(0, Number(this.engine.currentTime) / 1000 - this.timelineOffset);
      this.paused = true; this.emit("timeupdate"); this.emit("pause");
    }).catch(() => {});
  }
  setFullscreen(_fullscreen: boolean) {
    this.engine?.setRenderMode(RENDER_MODE_FIT);
  }
  async destroy() {
    if (this.disposed) return;
    this.disposed = true;
    this.observer?.disconnect();
    // Abort the source fetch promptly on navigation, even if load is pending.
    // Engine stop is the documented cancellation operation.
    const engine = this.engine as (Engine & { stop(): Promise<void> }) | null;
    if (engine) await engine.stop().catch(() => {});
    await this.chain;
    if (this.engine) {
      this.engine.off();
      await this.engine.destroy().catch(() => {});
    }
    this.engine = null;
    this.surface.replaceChildren();
  }
}
