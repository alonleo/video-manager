/** Sidebar destinations remember each library independently within this browser tab. */
const STORAGE = "frame-archive:library-navigation:v1";
type LibraryLocation = { pathname: string; search: string; key: string };
type Destination = { to: string; state: { restoreLibraryKey: string } };
let memory: Record<string, Destination> | undefined;

function scope(pathname: string, search: string) {
  if (pathname !== "/" && pathname !== "/photos") return null;
  return `${pathname}:${new URLSearchParams(search).get("favorite") === "true" ? "favorites" : "all"}`;
}

function destinations() {
  if (memory) return memory;
  memory = {};
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(STORAGE) || "{}");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      for (const [key, value] of Object.entries(saved)) {
        if (!value || typeof value.to !== "string" || typeof value.state?.restoreLibraryKey !== "string") continue;
        const [pathname, search = ""] = value.to.split("?");
        if (scope(pathname, search) === key && !value.to.includes("#")) memory[key] = value;
      }
    }
  } catch { /* Storage is optional; keep navigation working in memory. */ }
  return memory;
}

function destinationFor(location: LibraryLocation): Destination {
  const query = new URLSearchParams(location.search);
  query.delete("focus");
  const search = query.toString();
  return {
    to: location.pathname + (search ? `?${search}` : ""),
    state: { restoreLibraryKey: location.key },
  };
}

export function rememberLibraryDestination(location: LibraryLocation) {
  const key = scope(location.pathname, location.search);
  if (!key) return;
  destinations()[key] = destinationFor(location);
  try { sessionStorage.setItem(STORAGE, JSON.stringify(destinations())); }
  catch { /* Private mode/quota: retain the current session in memory. */ }
}

export function getLibraryDestination(to: string, current?: LibraryLocation): Destination | { to: string; state?: undefined } {
  const [pathname, search = ""] = to.split("?");
  const key = scope(pathname, search);
  // Only bare module links restore state; explicit list URLs remain authoritative.
  if (!key || (search && search !== "favorite=true")) return { to };
  if (current && scope(current.pathname, current.search) === key) return destinationFor(current);
  return destinations()[key] ?? { to };
}
