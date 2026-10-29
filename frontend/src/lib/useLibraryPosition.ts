import { useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { getLibraryPosition, rememberLibraryPosition } from "./libraryPosition";

/** Restore after data and layout are ready; never fight a user's scroll gesture. */
export function useLibraryPosition(ready: boolean, page: number) {
  const location = useLocation();
  const root = useRef<HTMLDivElement>(null);
  const readyRef = useRef(ready);
  readyRef.current = ready;
  const reconcile = useRef<(() => void) | null>(null);
  const mounted = useRef(false);
  const previousPage = useRef(page);

  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const focusSearch = new URLSearchParams(location.search).has("focus");
    const saved = focusSearch
      ? null
      : (getLibraryPosition(location.key, location.search) ??
        getLibraryPosition(location.state?.restoreLibraryKey, location.search));
    let restoring = !!saved;
    let frame = 0;
    let scrollTimer = 0;
    let settleTimer = 0;
    let deadline = 0;
    let observer: ResizeObserver | undefined;
    let disposed = false;
    const capture = () => {
      if (!restoring && readyRef.current && !disposed)
        rememberLibraryPosition(location.key, location.search);
    };
    const finish = () => {
      if (!restoring) return;
      restoring = false;
      delete element.dataset.positionRestoring;
      observer?.disconnect();
      window.clearTimeout(settleTimer);
      window.clearTimeout(deadline);
      capture();
    };
    const restore = () => {
      frame = 0;
      if (!restoring || !saved || !readyRef.current || disposed) return;
      const card = saved.videoId
        ? element.querySelector<HTMLElement>(
            `[data-video-id="${saved.videoId}"]`,
          )
        : null;
      // Anchor survives layout changes; raw distance is the fallback if it was removed.
      const offset = card
        ? Math.max(
            -card.offsetHeight + 24,
            Math.min(saved.offset, innerHeight - 64),
          )
        : 0;
      const target = card
        ? window.scrollY + card.getBoundingClientRect().top - offset
        : saved.y;
      const maximum = Math.max(
        0,
        document.documentElement.scrollHeight - innerHeight,
      );
      window.scrollTo({
        top: Math.max(0, Math.min(target, maximum)),
        behavior: "instant",
      });
      card?.focus({ preventScroll: true });
      window.clearTimeout(settleTimer);
      settleTimer = window.setTimeout(finish, 700);
    };
    const schedule = () => {
      if (restoring && !frame) frame = requestAnimationFrame(restore);
    };
    const onScroll = () => {
      if (restoring) return;
      window.clearTimeout(scrollTimer);
      scrollTimer = window.setTimeout(capture, 100);
    };
    const interrupt = () => {
      if (!restoring) return;
      finish();
    };
    const onKey = (event: KeyboardEvent) => {
      if (
        [
          "ArrowUp",
          "ArrowDown",
          "PageUp",
          "PageDown",
          "Home",
          "End",
          " ",
        ].includes(event.key)
      )
        interrupt();
    };
    reconcile.current = () => {
      if (restoring) {
        window.clearTimeout(settleTimer);
        if (readyRef.current) schedule();
      } else capture();
    };

    if (saved) {
      element.dataset.positionRestoring = "true";
      // The entry animation changes bounding boxes during restoration.
      element.style.animation = "none";
      observer = new ResizeObserver(schedule);
      observer.observe(element);
      deadline = window.setTimeout(finish, 15000);
      restore();
    } else {
      // Pagination starts at the collection heading, below the page intro.
      // Saved history/detail returns above still restore the previous card.
      if (!focusSearch && !location.hash) {
        if (mounted.current && previousPage.current !== page) {
          element.querySelector("#collection")?.scrollIntoView({
            block: "start",
            behavior: "instant",
          });
        } else if (!mounted.current) {
          window.scrollTo({ top: 0, behavior: "instant" });
        }
      }
      capture();
    }
    mounted.current = true;
    previousPage.current = page;
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("pagehide", capture);
    window.addEventListener("popstate", capture);
    window.addEventListener("wheel", interrupt, { passive: true });
    window.addEventListener("touchstart", interrupt, { passive: true });
    window.addEventListener("pointerdown", interrupt, { passive: true });
    window.addEventListener("keydown", onKey);
    element.addEventListener("click", capture, true);
    return () => {
      disposed = true;
      reconcile.current = null;
      observer?.disconnect();
      cancelAnimationFrame(frame);
      clearTimeout(scrollTimer);
      clearTimeout(settleTimer);
      clearTimeout(deadline);
      delete element.dataset.positionRestoring;
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("pagehide", capture);
      window.removeEventListener("popstate", capture);
      window.removeEventListener("wheel", interrupt);
      window.removeEventListener("touchstart", interrupt);
      window.removeEventListener("pointerdown", interrupt);
      window.removeEventListener("keydown", onKey);
      element.removeEventListener("click", capture, true);
    };
  }, [location.key, location.search, location.hash, location.state, page]);

  useLayoutEffect(() => {
    reconcile.current?.();
  }, [ready, location.key]);
  return root;
}
