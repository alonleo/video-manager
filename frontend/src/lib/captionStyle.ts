import { useSyncExternalStore, type CSSProperties } from "react";

export interface CaptionStyle {
  fontSize: number;
  fontFamily: "sans" | "serif" | "mono";
  color: string;
  backgroundColor: string;
  backgroundOpacity: number;
  outlineColor: string;
  outlineWidth: number;
  bold: boolean;
  shadow: boolean;
  lineHeight: number;
  position: "bottom" | "top";
  offset: number;
  align: "left" | "center" | "right";
}
export const DEFAULT_CAPTION_STYLE: CaptionStyle = {
  fontSize: 26, fontFamily: "sans", color: "#ffffff",
  backgroundColor: "#000000", backgroundOpacity: 60,
  outlineColor: "#000000", outlineWidth: 0, bold: false, shadow: true,
  lineHeight: 1.5, position: "bottom", offset: 6, align: "center",
};
export const CAPTION_STYLE_KEY = "frame-caption-style";
const fonts = {
  sans: 'system-ui, "Noto Sans CJK SC", "Microsoft YaHei", sans-serif',
  serif: '"Noto Serif CJK SC", "Songti SC", SimSun, serif',
  mono: '"Cascadia Mono", "Noto Sans Mono", monospace',
};
const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
export function normalizeCaptionStyle(value: unknown): CaptionStyle {
  const p = object(value), d = DEFAULT_CAPTION_STYLE;
  const number = (key: keyof CaptionStyle, min: number, max: number) =>
    typeof p[key] === "number" && Number.isFinite(p[key])
      ? Math.min(max, Math.max(min, p[key] as number)) : d[key] as number;
  const color = (key: "color" | "backgroundColor" | "outlineColor") =>
    typeof p[key] === "string" && /^#[0-9a-f]{6}$/i.test(p[key] as string)
      ? (p[key] as string).toLowerCase() : d[key];
  return {
    fontSize: Math.round(number("fontSize", 16, 54)),
    fontFamily: ["sans", "serif", "mono"].includes(p.fontFamily as string) ? p.fontFamily as CaptionStyle["fontFamily"] : d.fontFamily,
    color: color("color"), backgroundColor: color("backgroundColor"),
    backgroundOpacity: Math.round(number("backgroundOpacity", 0, 100)),
    outlineColor: color("outlineColor"), outlineWidth: number("outlineWidth", 0, 3),
    bold: typeof p.bold === "boolean" ? p.bold : d.bold,
    shadow: typeof p.shadow === "boolean" ? p.shadow : d.shadow,
    lineHeight: number("lineHeight", 1, 2),
    position: p.position === "top" ? "top" : "bottom",
    offset: Math.round(number("offset", 0, 30)),
    align: ["left", "center", "right"].includes(p.align as string) ? p.align as CaptionStyle["align"] : d.align,
  };
}
function readStyle(): CaptionStyle {
  try {
    const saved = localStorage.getItem(CAPTION_STYLE_KEY);
    if (saved) return normalizeCaptionStyle(JSON.parse(saved));
    const legacy = object(JSON.parse(localStorage.getItem("frame-player-captions") || "{}"));
    return { ...DEFAULT_CAPTION_STYLE, fontSize: legacy.size === "small" ? 20 : legacy.size === "large" ? 34 : 26 };
  } catch { return { ...DEFAULT_CAPTION_STYLE }; }
}
type Snapshot = { style: CaptionStyle; storageError: boolean };
let snapshot: Snapshot | undefined;
const listeners = new Set<() => void>();
const getSnapshot = () => snapshot ??= { style: readStyle(), storageError: false };
const notify = () => listeners.forEach(listener => listener());
function receiveStorage(event: StorageEvent) {
  if (event.key !== CAPTION_STYLE_KEY && event.key !== null) return;
  snapshot = { style: readStyle(), storageError: false };
  notify();
}
function subscribe(listener: () => void) {
  if (!listeners.size) window.addEventListener("storage", receiveStorage);
  listeners.add(listener);
  // A change in another tab may have occurred while this view was unmounted.
  const current = readStyle();
  if (JSON.stringify(current) !== JSON.stringify(getSnapshot().style) && !getSnapshot().storageError) {
    snapshot = { style: current, storageError: false };
    notify();
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) window.removeEventListener("storage", receiveStorage);
  };
}
function updateStyle(patch: Partial<CaptionStyle>) {
  const style = normalizeCaptionStyle({ ...getSnapshot().style, ...patch });
  let storageError = false;
  try { localStorage.setItem(CAPTION_STYLE_KEY, JSON.stringify(style)); }
  catch { storageError = true; }
  snapshot = { style, storageError };
  notify();
}
export function useCaptionStyle() {
  const current = useSyncExternalStore(subscribe, getSnapshot);
  return { ...current, updateStyle, resetStyle: () => updateStyle(DEFAULT_CAPTION_STYLE) };
}
export function captionStyleProperties(style: CaptionStyle): CSSProperties {
  const color = style.backgroundColor;
  const channels = [1, 3, 5].map(start => parseInt(color.slice(start, start + 2), 16));
  const w = style.outlineWidth;
  const shadows = w ? [
    [-w, 0], [w, 0], [0, -w], [0, w],
    [-w, -w], [w, w], [-w, w], [w, -w],
  ].map(([x, y]) => `${x}px ${y}px 0 ${style.outlineColor}`) : [];
  if (style.shadow) shadows.push("0 2px 4px #000d");
  return {
    "--caption-size": `clamp(${style.fontSize * 0.65}px, ${style.fontSize / 9}cqw, ${style.fontSize * 1.6}px)`,
    "--caption-font": fonts[style.fontFamily],
    "--caption-color": style.color,
    "--caption-background": `rgba(${channels.join(",")}, ${style.backgroundOpacity / 100})`,
    "--caption-shadow": shadows.join(", ") || "none",
    "--caption-weight": style.bold ? "700" : "500",
    "--caption-line-height": style.lineHeight,
    "--caption-offset": style.offset + "%",
    "--caption-align": style.align,
  } as CSSProperties;
}
