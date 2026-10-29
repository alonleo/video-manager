import { useState } from "react";
import { InputNumber, Switch } from "@arco-design/web-react";
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
      <Switch checked={preferences.autoplayVideos}
        onChange={checked => update({ autoplayVideos: checked })} />
    </label>
    <label className="playback-setting-row">
      <span><strong>自动检测并拉伸黑边</strong><small>播放时自动识别画面四周的黑边，稳定确认后拉伸有效画面以填满播放区域。</small></span>
      <Switch checked={preferences.autoCropBlackBars}
        onChange={checked => update({ autoCropBlackBars: checked })} />
    </label>
    <label className="playback-setting-row">
      <span><strong>图片自动轮播</strong><small>在照片浏览模式中自动切换到下一张，到达末尾后回到第一张。</small></span>
      <Switch checked={preferences.autoplayPhotos}
        onChange={checked => update({ autoplayPhotos: checked })} />
    </label>
    <label className="playback-interval-field">
      <span><strong>每张图片等待时间</strong><small>可设置 1–60 秒。</small></span>
      <span className="playback-interval-input">
        <InputNumber mode="button" min={1} max={60} step={1}
          disabled={!preferences.autoplayPhotos} value={preferences.photoIntervalSeconds}
          onChange={value => update({ photoIntervalSeconds: Number(value) })} />
        <span>秒</span></span>
    </label>
    {saved && <p className="playback-settings-saved" role="status">设置已保存</p>}
  </section>;
}
