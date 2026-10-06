const PHOTO_DETAIL_PATH = /^\/photos\/[^/]+\/?$/;

export function shouldResetRouteScroll(
  previousPathname: string | null,
  pathname: string,
  search: string,
) {
  const query = new URLSearchParams(search);
  if (pathname === "/" || pathname === "/photos" || query.has("focus") || query.has("add")) {
    return false;
  }
  return !(
    previousPathname !== null &&
    PHOTO_DETAIL_PATH.test(previousPathname) &&
    PHOTO_DETAIL_PATH.test(pathname)
  );
}

export function focusWithoutScroll(
  target: Pick<HTMLElement, "focus"> | null,
) {
  target?.focus({ preventScroll: true });
}
