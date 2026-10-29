import { describe, expect, it } from "vitest";
import type { Job } from "../api/types";
import { isActiveTask, mergeTaskStack } from "./jobNotifications";
const job = (id: number, state: Job["state"] = "running") => ({ id, state, kind: "subtitle_translate", progress: .5 } as Job);
describe("global task stack", () => {
  it("replaces a stale active snapshot with the final result without duplicate cards", () => {
    const items = mergeTaskStack([job(4, "done")], [job(4), job(2)], new Set());
    expect(items.map(item => [item.id, item.state])).toEqual([[4, "done"], [2, "running"]]);
  });
  it("shows the newest three while retaining the waiting tasks for the next slot", () => {
    const items = mergeTaskStack([job(5), job(4)], [job(1), job(2), job(3)], new Set());
    expect(items.slice(0, 3).map(item => item.id)).toEqual([5, 4, 3]);
    expect(mergeTaskStack([], items, new Set([5])).slice(0, 3).map(item => item.id)).toEqual([4, 3, 2]);
  });
  it("does not revive faded cards on a later poll or completion notification", () => {
    expect(mergeTaskStack([job(4, "done")], [job(4)], new Set([4]))).toEqual([]);
  });
  it("treats canceled and failed tasks as ended, so their timers can start", () => {
    expect(["queued", "running", "done", "error", "canceled"].map(state => isActiveTask(job(1, state as Job["state"])))).toEqual([true, true, false, false, false]);
  });
});
