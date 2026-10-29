export type PlaybackCapacity = {
  cores?: number;
  memoryGb?: number;
  effectiveType?: string;
  saveData?: boolean;
};

export function choosePreloadSeconds(capacity: PlaybackCapacity) {
  if (capacity.saveData || (capacity.cores ?? 4) <= 2 || (capacity.memoryGb ?? 4) <= 2 || capacity.effectiveType === "2g") return 2;
  if (capacity.effectiveType === "3g" || (capacity.cores ?? 4) <= 4 || (capacity.memoryGb ?? 4) <= 4) return 4;
  return 8;
}

export function browserPlaybackCapacity(): PlaybackCapacity {
  const nav = navigator as Navigator & {
    deviceMemory?: number;
    connection?: { effectiveType?: string; saveData?: boolean };
  };
  return {
    cores: nav.hardwareConcurrency,
    memoryGb: nav.deviceMemory,
    effectiveType: nav.connection?.effectiveType,
    saveData: nav.connection?.saveData,
  };
}

export function estimateBufferedSeconds(
  stats: { videoPacketQueueLength?: number; videoRenderFramerate?: number },
  maximum: number,
) {
  const fps = stats.videoRenderFramerate && stats.videoRenderFramerate > 0 ? stats.videoRenderFramerate : 25;
  return Math.min(maximum, Math.max(0, (stats.videoPacketQueueLength ?? 0) / fps));
}
