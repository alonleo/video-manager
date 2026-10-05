/** Per-history-entry positions: explicit return, browser back and reload share one snapshot. */
const STORAGE = "frame-archive:library-positions:v1";
const MAX_ENTRIES = 80;
export interface LibraryPosition {
  search: string;
  y: number;
  videoId: number | null;
  offset: number;
  at: number;
}
let memory: Record<string, LibraryPosition> | null = null;

function queryKey(search: string) {
  const query = new URLSearchParams(search);
  query.delete("focus");
  query.sort();
  return query.toString();
}

function positions() {
  if (memory) return memory;
  memory = {};
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(STORAGE) || "{}");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      for (const [key, value] of Object.entries(saved)) {
        if (
          value &&
          typeof value.search === "string" &&
          Number.isFinite(value.y) &&
          value.y >= 0 &&
          Number.isFinite(value.offset) &&
          Number.isFinite(value.at) &&
          (value.videoId === null ||
            (Number.isSafeInteger(value.videoId) && value.videoId > 0))
        )
          memory[key] = value;
      }
    }
  } catch {
    /* Storage is optional; memory still supports navigation. */
  }
  return memory;
}

export function getLibraryPosition(
  entry: unknown,
  search: string,
): LibraryPosition | null {
  if (typeof entry !== "string") return null;
  const value = positions()[entry];
  return value?.search === queryKey(search) ? value : null;
}

export function rememberLibraryPosition(
  entry: string,
  search: string,
  videoId?: number,
) {
  const root = document.querySelector<HTMLElement>(".library-page");
  if (!root || root.dataset.positionRestoring === "true") return;
  const cards = Array.from(
    root.querySelectorAll<HTMLElement>("[data-video-id]"),
  );
  const visible = (card: HTMLElement) =>
    card.getBoundingClientRect().bottom > 20 &&
    card.getBoundingClientRect().top < innerHeight;
  const focused =
    document.activeElement instanceof HTMLElement
      ? document.activeElement.closest<HTMLElement>("[data-video-id]")
      : null;
  const previous = getLibraryPosition(entry, search);
  const prior =
    previous && Math.abs(previous.y - scrollY) < 2
      ? cards.find(
          (card) =>
            Number(card.dataset.videoId) === previous.videoId && visible(card),
        )
      : null;
  const anchor =
    (videoId
      ? cards.find((card) => Number(card.dataset.videoId) === videoId)
      : null) ??
    (focused && root.contains(focused) && visible(focused) ? focused : null) ??
    prior ??
    cards.find(visible) ??
    null;
  const all = positions();
  all[entry] = {
    search: queryKey(search),
    y: Math.max(0, window.scrollY),
    videoId: anchor ? Number(anchor.dataset.videoId) : null,
    offset: anchor?.getBoundingClientRect().top ?? 0,
    at: Date.now(),
  };
  const keys = Object.keys(all).sort((a, b) => all[b].at - all[a].at);
  for (const key of keys.slice(MAX_ENTRIES)) delete all[key];
  try {
    sessionStorage.setItem(STORAGE, JSON.stringify(all));
  } catch {
    /* Quota/private mode. */
  }
}
