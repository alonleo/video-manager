import { useId } from "react";
import {
  captionStyleProperties, DEFAULT_CAPTION_STYLE, useCaptionStyle,
  type CaptionStyle,
} from "../../lib/captionStyle";
import Icon from "../Icon";
import "./caption-style.css";

function Range({ label, value, min, max, step = 1, unit = "", onChange }: {
  label: string; value: number; min: number; max: number;
  step?: number; unit?: string; onChange: (value: number) => void;
}) {
  const id = useId();
  return <div className="caption-style-field">
    <label htmlFor={id}>{label}<output htmlFor={id} aria-hidden="true">{value}{unit}</output></label>
    <input id={id} type="range" min={min} max={max} step={step} value={value}
      aria-valuetext={value + unit} onChange={e => onChange(Number(e.target.value))} />
  </div>;
}
function Color({ label, value, onChange }: {
  label: string; value: string; onChange: (value: string) => void;
}) {
  return <label className="caption-style-field caption-color-field">
    <span>{label}</span><span className="caption-color-value">
      <input type="color" value={value} onChange={e => onChange(e.target.value)} />
      <span aria-hidden="true">{value.toUpperCase()}</span>
    </span>
  </label>;
}
const presets: { name: string; style: CaptionStyle }[] = [
  { name: "经典", style: DEFAULT_CAPTION_STYLE },
  { name: "影院", style: { ...DEFAULT_CAPTION_STYLE, backgroundOpacity: 0, outlineWidth: 1, bold: true } },
  { name: "醒目", style: { ...DEFAULT_CAPTION_STYLE, color: "#ffe066", backgroundOpacity: 75, bold: true } },
];
export default function CaptionStyleEditor({ compact = false }: { compact?: boolean }) {
  const { style: s, updateStyle: update, resetStyle, storageError } = useCaptionStyle();
  const controls = <>
    <div className="caption-style-presets" role="group" aria-label="字幕样式预设">
      {presets.map(p => <button type="button" key={p.name}
        aria-pressed={Object.keys(p.style).every(key => s[key as keyof CaptionStyle] === p.style[key as keyof CaptionStyle])}
        onClick={() => update(p.style)}>{p.name}</button>)}
    </div>
    <div className="caption-style-fields">
      <Range label="字幕字号" value={s.fontSize} min={16} max={54} onChange={fontSize => update({ fontSize })} />
      <label className="caption-style-field">字幕字体<select value={s.fontFamily}
        onChange={e => update({ fontFamily: e.target.value as CaptionStyle["fontFamily"] })}>
        <option value="sans">无衬线 · 清晰</option><option value="serif">衬线 · 宋体</option><option value="mono">等宽字体</option>
      </select></label>
      <Color label="文字颜色" value={s.color} onChange={color => update({ color })} />
      <Color label="背景颜色" value={s.backgroundColor} onChange={backgroundColor => update({ backgroundColor })} />
      <Range label="背景不透明度" value={s.backgroundOpacity} min={0} max={100} unit="%" onChange={backgroundOpacity => update({ backgroundOpacity })} />
      <Range label="描边粗细" value={s.outlineWidth} min={0} max={3} step={0.5} onChange={outlineWidth => update({ outlineWidth })} />
      <Color label="描边颜色" value={s.outlineColor} onChange={outlineColor => update({ outlineColor })} />
      <Range label="字幕行距" value={s.lineHeight} min={1} max={2} step={0.1} unit=" 倍" onChange={lineHeight => update({ lineHeight })} />
      <label className="caption-style-field">显示位置<select value={s.position}
        onChange={e => update({ position: e.target.value as CaptionStyle["position"] })}>
        <option value="bottom">画面底部</option><option value="top">画面顶部</option>
      </select></label>
      <Range label={s.position === "bottom" ? "距底部" : "距顶部"} value={s.offset}
        min={0} max={30} unit="%" onChange={offset => update({ offset })} />
      <label className="caption-style-field">文字对齐<select value={s.align}
        onChange={e => update({ align: e.target.value as CaptionStyle["align"] })}>
        <option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option>
      </select></label>
      <div className="caption-style-toggles">
        <label><input type="checkbox" checked={s.bold} onChange={e => update({ bold: e.target.checked })} />粗体</label>
        <label><input type="checkbox" checked={s.shadow} onChange={e => update({ shadow: e.target.checked })} />文字阴影</label>
      </div>
    </div>
    <div className="caption-style-footer">
      <p role="status">{storageError ? "当前样式已生效，但浏览器禁止保存，刷新后可能丢失。" : "自动保存 · 当前浏览器所有视频共用"}</p>
      <button type="button" onClick={resetStyle}><Icon name="refresh" size={14} />恢复默认样式</button>
    </div>
  </>;
  if (compact) return <div className="caption-style-editor is-compact">{controls}</div>;
  return <section id="subtitle-style" className="card caption-style-editor" aria-label="字幕样式设置">
    <div className="caption-style-heading"><div>
      <p className="eyebrow">CAPTION DESIGN · 字幕外观</p>
      <h2>字幕样式</h2><p>调整你喜欢的阅读方式，预览与播放器即时生效。</p>
    </div><Icon name="captions" size={28} /></div>
    <div className="caption-style-layout">
      <div className="caption-preview-column">
        <div className="caption-style-preview" role="img" aria-label="字幕样式实时预览"
          style={captionStyleProperties(s)}>
          <div className="caption-preview-scene" aria-hidden="true"><i /><b /></div>
          <span className="caption-preview-label">LIVE PREVIEW</span>
          <div className="wasm-captions" data-position={s.position}><span>{"让每一帧，都有自己的声音。\nEvery frame has a voice of its own."}</span></div>
        </div>
        <p className="caption-style-help">字号随画面尺寸缩放。底部字幕会自动避让播放控制栏。这里的样式用于播放显示，不会改写字幕文件或已烧录进视频的文字。</p>
      </div>
      <div>{controls}</div>
    </div>
  </section>;
}
