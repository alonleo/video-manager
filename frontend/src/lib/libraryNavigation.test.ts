import { beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  const saved = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (key: string, value: string) => saved.set(key, value),
  });
});

describe("library module navigation", () => {
  it("returns to page 63 with filters and the scroll entry after visiting jobs", async () => {
    const nav = await import("./libraryNavigation");
    nav.rememberLibraryDestination({ pathname: "/", search: "?page=63&q=movie&view=list", key: "library" });
    nav.rememberLibraryDestination({ pathname: "/jobs", search: "", key: "jobs" });
    expect(nav.getLibraryDestination("/")).toEqual({ to: "/?page=63&q=movie&view=list", state: { restoreLibraryKey: "library" } });
  });
  it("persists destinations across reloads", async () => {
    (await import("./libraryNavigation")).rememberLibraryDestination({ pathname: "/", search: "?page=63", key: "library" });
    vi.resetModules();
    expect((await import("./libraryNavigation")).getLibraryDestination("/").to).toBe("/?page=63");
  });
  it("keeps video, photo and favorite libraries separate", async () => {
    const nav = await import("./libraryNavigation");
    for (const [pathname, search] of [["/", "?page=63"], ["/photos", "?page=7"], ["/", "?favorite=true&page=9"]])
      nav.rememberLibraryDestination({ pathname, search, key: search });
    expect(nav.getLibraryDestination("/").to).toBe("/?page=63");
    expect(nav.getLibraryDestination("/photos").to).toBe("/photos?page=7");
    expect(nav.getLibraryDestination("/?favorite=true").to).toBe("/?favorite=true&page=9");
  });
  it("honors current and explicit URLs, removing the one-time focus flag", async () => {
    const nav = await import("./libraryNavigation");
    nav.rememberLibraryDestination({ pathname: "/", search: "?page=63", key: "old" });
    expect(nav.getLibraryDestination("/", { pathname: "/", search: "?page=64&focus=search", key: "new" }).to).toBe("/?page=64");
    expect(nav.getLibraryDestination("/?page=2").to).toBe("/?page=2");
    expect(nav.getLibraryDestination("/jobs").to).toBe("/jobs");
  });
  it("works when browser storage is unavailable", async () => {
    vi.stubGlobal("sessionStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } });
    const nav = await import("./libraryNavigation");
    nav.rememberLibraryDestination({ pathname: "/", search: "?page=63", key: "library" });
    expect(nav.getLibraryDestination("/").to).toBe("/?page=63");
  });
  it("ignores saved external destinations", async () => {
    sessionStorage.setItem("frame-archive:library-navigation:v1", JSON.stringify({ "/:all": { to: "https://example.com", state: { restoreLibraryKey: "bad" } } }));
    expect((await import("./libraryNavigation")).getLibraryDestination("/")).toEqual({ to: "/" });
  });
});
