import { useEffect, useRef, useState } from "react";
import type WasmMedia from "./WasmMedia";
import type { PlayerCaptionsState } from "./PlayerCaptions";
import { captionStyleProperties } from "../../lib/captionStyle";
import "./caption-style.css";

type Cue = { start: number; end: number; text: string };
function timestamp(value: string) {
  return value.split(":").reduce((total, part) => total * 60 + Number(part), 0);
}
export function parseVtt(source: string): Cue[] {
  return source.replace(/\r/g, "").split(/\n\s*\n/).flatMap(block => {
    if (/^(WEBVTT|NOTE|STYLE|REGION)/.test(block.trim())) return [];
    const lines = block.split("\n");
    const index = lines.findIndex(line => line.includes("-->"));
    if (index < 0) return [];
    const match = lines[index].match(/([\d:.]+)\s+-->\s+([\d:.]+)/);
    if (!match) return [];
    const start = timestamp(match[1]), end = timestamp(match[2]);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return [];
    // Text-only rendering: imported subtitles never inject markup into the page.
    const doc = new DOMParser().parseFromString(lines.slice(index + 1).join("\n").replace(/<[^>]*>/g, ""), "text/html");
    return [{ start, end, text: doc.body.textContent || "" }];
  });
}
export default function WasmCaptions({ media, captions }: { media: WasmMedia | null; captions: PlayerCaptionsState }) {
  const [cues, setCues] = useState<Cue[]>([]);
  const [time, setTime] = useState(0);
  const [failed, setFailed] = useState(false);
  const lastIdentity = useRef("");
  useEffect(() => {
    const controller = new AbortController();
    const identity = captions.url?.replace(/([?&])version=\d+/, "") || "";
    const changed = identity !== lastIdentity.current;
    if (changed) setCues([]);
    lastIdentity.current = identity;
    setFailed(false);
    if (captions.url) {
      fetch(captions.url, { signal: controller.signal }).then(response => {
        if (!response.ok) throw new Error("字幕加载失败");
        return response.text();
      }).then(text => { if (!controller.signal.aborted) setCues(parseVtt(text)); })
        .catch(() => { if (!controller.signal.aborted && changed) setFailed(true); });
    }
    return () => controller.abort();
  }, [captions.url]);
  useEffect(() => {
    if (!media) return;
    const sync = () => setTime(media.currentTime);
    media.addEventListener("timeupdate", sync);
    media.addEventListener("loadedmetadata", sync);
    sync();
    return () => {
      media.removeEventListener("timeupdate", sync);
      media.removeEventListener("loadedmetadata", sync);
    };
  }, [media]);
  if (failed) return <p className="player-notice" role="alert">字幕加载失败，请重新选择字幕。</p>;
  if (!captions.url) return null;
  const text = cues.filter(cue => cue.start <= time && cue.end > time).map(cue => cue.text).join("\n");
  return text ? <div className="wasm-captions" data-position={captions.style.position}
    style={captionStyleProperties(captions.style)} aria-live="off"><span>{text}</span></div> : null;
}
