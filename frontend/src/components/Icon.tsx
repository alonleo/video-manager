import type { CSSProperties } from "react";

export type IconName =
  | "photo"
  | "trash"
  | "download"
  | "pause"
  | "volume"
  | "muted"
  | "captions"
  | "fullscreen"
  | "fullscreenExit"
  | "enhance"
  | "library"
  | "star"
  | "storage"
  | "activity"
  | "search"
  | "plus"
  | "arrow"
  | "top"
  | "play"
  | "grid"
  | "list"
  | "close"
  | "check"
  | "refresh"
  | "rotateLeft"
  | "rotateRight"
  | "folder"
  | "film"
  | "chevron"
  | "back"
  | "sidebarClose"
  | "sidebarOpen"
  | "edit"
  | "convert"
  | "crop"
  | "settings";
const paths: Record<IconName, string> = {
  photo: "M3 4h18v16H3Z M3 16l5-5 4 4 3-3 6 6 M15 8h.01",
  trash: "M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7",
  enhance: "m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4Z M20 2v4 M18 4h4",
  settings: "M4 7h16 M4 17h16 M8 4v6 M16 14v6",
  fullscreenExit: "M3 8h5V3 M21 8h-5V3 M3 16h5v5 M21 16h-5v5",
  fullscreen: "M8 3H3v5 M16 3h5v5 M3 16v5h5 M21 16v5h-5",
  captions: "M3 5h18v14H3Z M10 10H7v4h3 M18 10h-3v4h3",
  muted: "M11 4 6 8H3v8h3l5 4Z M16 9l6 6 M22 9l-6 6",
  volume: "M11 4 6 8H3v8h3l5 4Z M15 8a6 6 0 0 1 0 8 M18 4a11 11 0 0 1 0 16",
  pause: "M8 5v14 M16 5v14",
  library: "M4 4h6v16H4z M14 4h6v16h-6z M4 8h6 M14 8h6",
  star: "m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9Z",
  storage: "M4 4h16v6H4z M4 14h16v6H4z M7 7h.01 M7 17h.01 M15 7h2 M15 17h2",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  search: "M20 20l-5-5 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0",
  plus: "M12 5v14 M5 12h14",
  arrow: "M5 12h14 m-6-6 6 6-6 6",
  top: "M5 4h14 M12 20V8 m-5 5 5-5 5 5",
  play: "m9 5 11 7-11 7Z",
  grid: "M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z",
  list: "M9 5h11 M9 12h11 M9 19h11 M4 5h.01 M4 12h.01 M4 19h.01",
  close: "m6 6 12 12 M6 18 18 6",
  check: "m5 12 4 4L19 6",
  refresh:
    "M20 7v5h-5 M4 17v-5h5 M5 8a7 7 0 0 1 12-3l3 7 M4 12l3 7a7 7 0 0 0 12-3",
  rotateLeft: "M4 8V3m0 0h5M4 3l4 4 M5.6 13a7 7 0 1 0 2-5",
  rotateRight: "M20 8V3m0 0h-5m5 0-4 4 M18.4 13a7 7 0 1 1-2-5",
  folder: "M3 7V5h7l2 2h9v13H3Z",
  film: "M4 3h16v18H4z M8 3v18 M16 3v18 M4 8h4 M4 16h4 M16 8h4 M16 16h4",
  chevron: "m9 5 7 7-7 7",
  back: "M19 12H5 m6-6-6 6 6 6",
  sidebarClose: "M4 4h16v16H4z M9 4v16 M16 9l-3 3 3 3",
  sidebarOpen: "M4 4h16v16H4z M9 4v16 M13 9l3 3-3 3",
  edit: "m16 4 4 4 M4 20l4-1L20 7a2.12 2.12 0 0 0-3-3L4 17v3Z",
  download: "M12 3v12m-5-5 5 5 5-5 M4 15v6h16v-6",
  convert: "M4 7h16m-4-4 4 4-4 4 M20 17H4m4-4-4 4 4 4",
  crop: "M6 3v15a2 2 0 0 0 2 2h13 M3 6h15a2 2 0 0 1 2 2v13 M10 10h10v10H10Z",
};
export default function Icon({
  name,
  size = 20,
  className = "",
  style,
}: {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
      style={style}
    >
      <path d={paths[name]} />
    </svg>
  );
}
