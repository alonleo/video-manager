import { describe, expect, it } from "vitest";

import { choosePreloadSeconds, estimateBufferedSeconds } from "./playbackBuffer";

describe("dynamic playback buffering", () => {
  it("uses a larger cache on capable devices and fast connections", () => {
    expect(choosePreloadSeconds({ cores: 8, memoryGb: 8, effectiveType: "4g", saveData: false })).toBe(8);
  });

  it("limits cache pressure on constrained devices and data saver connections", () => {
    expect(choosePreloadSeconds({ cores: 2, memoryGb: 2, effectiveType: "3g", saveData: true })).toBe(2);
  });

  it("estimates decoded packet headroom and clamps it to the configured window", () => {
    expect(estimateBufferedSeconds({ videoPacketQueueLength: 150, videoRenderFramerate: 30 }, 8)).toBe(5);
    expect(estimateBufferedSeconds({ videoPacketQueueLength: 900, videoRenderFramerate: 30 }, 8)).toBe(8);
  });
});
