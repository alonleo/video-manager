import { useState } from "react";
import {
  readPlaybackPreferences,
  savePlaybackPreferences,
  type PlaybackPreferences,
} from "../lib/playbackPreferences";

export default function PlaybackSettingsPage() {
  const [preferences, setPreferences] = useState(readPlaybackPreferences);
  const [saved, setSaved] = useState(false);

  const update = (patch: Partial<PlaybackPreferences>) => {
    setPreferences(current => savePlaybackPreferences({ ...current, ...patch }));
    setSaved(true);
  };

  return <section className="card playback-settings-card" aria-labelledby="playback-settings-title">
    <div>
      <h2 id="playback-settings-title">播放与图片轮播</h2>
      <p>这些偏好保存在当前浏览器中，并在下次打开播放器或照片预览时自动应用。</p>
    </div>
    <label className="playback-setting-row">
      <span><strong>视频自动播放</strong><small>打开视频详情后，播放器准备完成便自动开始。</small></span>
      <input type="checkbox" role="switch" checked={preferences.autoplayVideos}
        onChange={event => update({ autoplayVideos: event.target.checked })} />
    </label>
    <label className="playback-setting-row">
      <span><strong>图片自动轮播</strong><small>在照片浏览模式中自动切换到下一张，到达末尾后回到第一张。</small></span>
      <input type="checkbox" role="switch" checked={preferences.autoplayPhotos}
        onChange={event => update({ autoplayPhotos: event.target.checked })} />
    </label>
    <label className="playback-interval-field">
      <span><strong>每张图片等待时间</strong><small>可设置 1–60 秒。</small></span>
      <span className="playback-interval-input"><input className="input" type="number" min="1" max="60" step="1"
        disabled={!preferences.autoplayPhotos} value={preferences.photoIntervalSeconds}
        onChange={event => update({ photoIntervalSeconds: Number(event.target.value) })} /><span>秒</span></span>
    </label>
    {saved && <p className="playback-settings-saved" role="status">设置已保存</p>}
  </section>;
}
