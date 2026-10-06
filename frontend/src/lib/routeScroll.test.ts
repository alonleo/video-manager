import { describe, expect, it, vi } from "vitest";
import { focusWithoutScroll, shouldResetRouteScroll } from "./routeScroll";

describe("route scroll behavior", () => {
  it("keeps the document position while switching between photo details", () => {
    expect(
      shouldResetRouteScroll("/photos/17", "/photos/18", ""),
    ).toBe(false);
  });

  it("still resets the document position when entering a detail page", () => {
    expect(
      shouldResetRouteScroll("/photos", "/photos/18", ""),
    ).toBe(true);
  });

  it("focuses the photo viewport without scrolling it", () => {
    const focus = vi.fn();

    focusWithoutScroll({ focus });

    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });
});
