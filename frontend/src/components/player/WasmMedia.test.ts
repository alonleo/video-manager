import { describe, expect, it, vi } from "vitest";

import WasmMedia from "./WasmMedia";

describe("WasmMedia fullscreen rendering", () => {
  it("fills the screen in fullscreen and restores fit mode afterwards", () => {
    const media = new WasmMedia({ replaceChildren: vi.fn() } as unknown as HTMLDivElement);
    const setRenderMode = vi.fn();
    (media as unknown as { engine: { setRenderMode: (mode: number) => void } }).engine = {
      setRenderMode,
    };

    media.setFullscreen(true);
    media.setFullscreen(false);

    expect(setRenderMode.mock.calls).toEqual([[1], [0]]);
  });
});
