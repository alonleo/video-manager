import path from 'node:path';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
import { readFile, writeFile } from 'node:fs/promises';
const root = path.resolve(import.meta.dirname, '..');
const fixtures = JSON.parse(await readFile(root + '/.run/wasm-validation/fixtures.json', 'utf8'));
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/google-chrome', args: ['--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const messages = [];
page.on('console', m => { if (['error','warning'].includes(m.type())) messages.push(m.text().slice(0, 600)); });
page.on('pageerror', e => messages.push('PAGEERROR ' + e.message));
page.on('requestfailed', r => messages.push('FAILED '+r.url()+' '+r.failure()?.errorText));
page.on('response', r => { if (r.status() >= 400) messages.push('HTTP '+r.status()+' '+r.url()); });
await page.addInitScript(() => {
  window.__nativeDecodeCalls = [];
  for (const name of ['VideoDecoder', 'AudioDecoder']) {
    if (window[name]) {
      const Original = window[name];
      window[name] = class extends Original { constructor(...args) { window.__nativeDecodeCalls.push(name); throw new Error('Native decoder forbidden in Wasm test'); } };
    }
  }
  HTMLMediaElement.prototype.play = function() { window.__nativeDecodeCalls.push('HTMLMediaElement.play'); throw new Error('Native media playback forbidden'); };
});
const name = process.env.WASM_SAMPLE || 'sample.mp4';
const sample = fixtures.videos.find(v => v.name === name);
try {
  await page.goto('http://127.0.0.1:8427/videos/' + sample.id);
  await page.waitForFunction(() => ['ready','error'].includes(document.querySelector('[data-player-state]')?.dataset.playerState), null, { timeout: 45000 });
  console.log('streams', await page.evaluate(async () => { const m = await import('/player-engine/libmedia-1.3.1/avplayer.js'); return m.default.Instances.map(p => p.getStreams().map(s => ({ mediaType:s.mediaType, codecType:s.codecparProxy.codecType, codecId:s.codecparProxy.codecId }))); }));
  console.log('state', await page.locator('[data-player-engine]').getAttribute('data-player-state'));
  console.log('text', await page.locator('[data-player-engine]').innerText());
  if (await page.getByRole('button', {name:'开始播放',exact:true}).count()) {
    await page.getByRole('button', {name:'开始播放',exact:true}).click();
    await page.waitForTimeout(4000);
    if (process.env.WASM_SEEK) { const slider=page.getByRole('slider',{name:'播放进度'});const rect=await slider.boundingBox();await slider.click({position:{x:rect.width/4,y:rect.height/2}});await page.waitForTimeout(2000); }
    console.log('after play', await page.locator('[data-player-engine]').innerText());
    console.log('stats', await page.evaluate(async () => { const m = await import('/player-engine/libmedia-1.3.1/avplayer.js'); return m.default.Instances.map(p => ({time:Number(p.currentTime), status:p.getStatus(), frames:Number(p.getStats().videoFrameRenderCount), audio:Number(p.getStats().audioFrameRenderCount), context:m.default.audioContext?.state})); }));
    console.log('canvas', await page.locator('canvas').count(), 'native calls', await page.evaluate(() => window.__nativeDecodeCalls));
    console.log('time', await page.getByRole('slider', {name:'播放进度'}).inputValue());
  }
  await page.screenshot({ path: root + '/.run/wasm-validation/smoke.png', fullPage: true });
} catch(e) { console.log('ERROR', e.message); }
finally {
  console.log(JSON.stringify(messages, null, 2));
  await writeFile(root + '/.run/wasm-validation/smoke-log.json', JSON.stringify(messages, null, 2));
  await browser.close();
}
