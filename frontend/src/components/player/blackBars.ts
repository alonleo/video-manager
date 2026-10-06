export type BlackBars = { left: number; right: number; top: number; bottom: number };

const DARK_LUMA = 24;
const ACTIVE_PIXEL_RATIO = 0.08;
const MIN_BAR_RATIO = 0.025;
const MIN_CONTENT_RATIO = 0.45;

export function detectBlackBars(image: ImageData): BlackBars | null {
  const { data, width, height } = image;
  const activeRow = (y: number) => {
    let active = 0;
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4;
      if ((data[i] * 3 + data[i + 1] * 6 + data[i + 2]) / 10 > DARK_LUMA) active += 1;
    }
    return active / width >= ACTIVE_PIXEL_RATIO;
  };
  const activeColumn = (x: number) => {
    let active = 0;
    for (let y = 0; y < height; y += 1) {
      const i = (y * width + x) * 4;
      if ((data[i] * 3 + data[i + 1] * 6 + data[i + 2]) / 10 > DARK_LUMA) active += 1;
    }
    return active / height >= ACTIVE_PIXEL_RATIO;
  };
  let top = 0; while (top < height && !activeRow(top)) top += 1;
  let bottom = 0; while (bottom < height - top && !activeRow(height - 1 - bottom)) bottom += 1;
  let left = 0; while (left < width && !activeColumn(left)) left += 1;
  let right = 0; while (right < width - left && !activeColumn(width - 1 - right)) right += 1;
  if ((width - left - right) / width < MIN_CONTENT_RATIO || (height - top - bottom) / height < MIN_CONTENT_RATIO) return null;
  const bars = { left: left / width, right: right / width, top: top / height, bottom: bottom / height };
  if (bars.left < MIN_BAR_RATIO) bars.left = 0;
  if (bars.right < MIN_BAR_RATIO) bars.right = 0;
  if (bars.top < MIN_BAR_RATIO) bars.top = 0;
  if (bars.bottom < MIN_BAR_RATIO) bars.bottom = 0;
  return Object.values(bars).some(Boolean) ? bars : null;
}

const closeTo = (a: BlackBars | null, b: BlackBars | null) => {
  if (!a || !b) return a === b;
  return (Object.keys(a) as (keyof BlackBars)[]).every(key => Math.abs(a[key] - b[key]) < 0.025);
};

const hasVisiblePicture = (image: ImageData) => {
  let active = 0;
  const pixels = image.width * image.height;
  for (let i = 0; i < image.data.length; i += 4) {
    if ((image.data[i] * 3 + image.data[i + 1] * 6 + image.data[i + 2]) / 10 > DARK_LUMA) active += 1;
  }
  return active / pixels >= 0.03;
};

export class AutoBlackBarController {
  private sample = document.createElement("canvas");
  private context = this.sample.getContext("2d", { willReadFrequently: true });
  private timer: ReturnType<typeof setInterval> | null = null;
  private candidate: BlackBars | null = null;
  private confirmations = 0;

  constructor(private surface: HTMLElement) {
    this.sample.width = 160;
    this.sample.height = 90;
  }

  start() {
    if (this.timer) return;
    this.timer = setInterval(() => this.inspect(), 1000);
    this.inspect();
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.candidate = null;
    this.confirmations = 0;
    this.reset();
  }

  private inspect() {
    const canvas = this.surface.querySelector("canvas");
    if (!canvas || !canvas.width || !canvas.height || !this.context) return;
    try {
      this.context.drawImage(canvas, 0, 0, this.sample.width, this.sample.height);
      const image = this.context.getImageData(0, 0, this.sample.width, this.sample.height);
      // A fade-to-black has no trustworthy edge. Keep the last confirmed crop until a visible frame returns.
      if (!hasVisiblePicture(image)) return;
      const detected = detectBlackBars(image);
      if (!closeTo(detected, this.candidate)) {
        this.candidate = detected;
        this.confirmations = 1;
        return;
      }
      this.confirmations += 1;
      if (this.confirmations >= 3) this.apply(canvas, detected);
    } catch {
      // Some browser/GPU combinations do not expose rendered pixels. Playback continues normally.
      this.stop();
    }
  }

  private apply(canvas: HTMLCanvasElement, bars: BlackBars | null) {
    if (!bars) return this.reset();
    const sx = 1 / (1 - bars.left - bars.right);
    const sy = 1 / (1 - bars.top - bars.bottom);
    const x = -bars.left * canvas.clientWidth * sx;
    const y = -bars.top * canvas.clientHeight * sy;
    canvas.style.transformOrigin = "0 0";
    canvas.style.transform = `matrix(${sx}, 0, 0, ${sy}, ${x}, ${y})`;
    this.surface.dataset.blackBars = "cropped";
  }

  private reset() {
    this.surface.querySelectorAll("canvas").forEach(canvas => {
      canvas.style.removeProperty("transform");
      canvas.style.removeProperty("transform-origin");
    });
    delete this.surface.dataset.blackBars;
  }
}
