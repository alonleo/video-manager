import { describe, expect, it } from "vitest";

import { detectBlackBars } from "./blackBars";

function frame(width: number, height: number, active: { left: number; top: number; right: number; bottom: number }) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = active.top; y < height - active.bottom; y += 1) {
    for (let x = active.left; x < width - active.right; x += 1) {
      const offset = (y * width + x) * 4;
      data[offset] = 120; data[offset + 1] = 100; data[offset + 2] = 80; data[offset + 3] = 255;
    }
  }
  return { data, width, height } as ImageData;
}

describe("detectBlackBars", () => {
  it("detects symmetric letterbox bars", () => {
    expect(detectBlackBars(frame(100, 60, { left: 0, right: 0, top: 10, bottom: 10 }))).toEqual({
      left: 0, right: 0, top: 10 / 60, bottom: 10 / 60,
    });
  });

  it("detects pillarbox bars", () => {
    expect(detectBlackBars(frame(100, 60, { left: 15, right: 15, top: 0, bottom: 0 }))).toEqual({
      left: 0.15, right: 0.15, top: 0, bottom: 0,
    });
  });

  it("ignores tiny dark edges and implausibly small active pictures", () => {
    expect(detectBlackBars(frame(100, 60, { left: 1, right: 1, top: 1, bottom: 1 }))).toBeNull();
    expect(detectBlackBars(frame(100, 60, { left: 30, right: 30, top: 20, bottom: 20 }))).toBeNull();
  });
});
