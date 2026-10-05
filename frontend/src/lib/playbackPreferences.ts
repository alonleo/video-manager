export type PlaybackPreferences = {
  autoplayVideos: boolean;
  autoplayPhotos: boolean;
  photoIntervalSeconds: number;
};

export const PLAYBACK_PREFERENCES_KEY = "frame-archive-playback-preferences";

export const DEFAULT_PLAYBACK_PREFERENCES: PlaybackPreferences = {
  autoplayVideos: false,
  autoplayPhotos: false,
  photoIntervalSeconds: 5,
};

const clampPhotoInterval = (value: unknown) => {
  const seconds = Number(value);
  return Number.isFinite(seconds) ? Math.min(60, Math.max(1, Math.round(seconds))) : 5;
};

export function readPlaybackPreferences(): PlaybackPreferences {
  try {
    const saved = JSON.parse(localStorage.getItem(PLAYBACK_PREFERENCES_KEY) || "{}");
    return {
      autoplayVideos: saved.autoplayVideos === true,
      autoplayPhotos: saved.autoplayPhotos === true,
      photoIntervalSeconds: clampPhotoInterval(saved.photoIntervalSeconds),
    };
  } catch {
    return DEFAULT_PLAYBACK_PREFERENCES;
  }
}

export function savePlaybackPreferences(value: PlaybackPreferences) {
  const preferences = {
    ...value,
    photoIntervalSeconds: clampPhotoInterval(value.photoIntervalSeconds),
  };
  try {
    localStorage.setItem(PLAYBACK_PREFERENCES_KEY, JSON.stringify(preferences));
  } catch {
    // Persistence is optional; the settings still apply during this page session.
  }
  return preferences;
}
