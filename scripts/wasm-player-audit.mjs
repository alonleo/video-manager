// Real media validation in an isolated database. Native media decoders are disabled.
import path from 'node:path';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
import { readFile, writeFile, readdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const root = path.resolve(import.meta.dirname, '..');
const base = process.env.WASM_AUDIT_URL || 'http://127.0.0.1:8427';
const reportName = process.env.WASM_REPORT_NAME || 'matrix';
const fixtures = JSON.parse(await readFile(root + '/.run/wasm-validation/fixtures.json', 'utf8'));
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/google-chrome', args: ['--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.addInitScript(() => {
  window.__nativeDecodeCalls = [];
  for (const name of ['VideoDecoder', 'AudioDecoder']) {
    if (window[name]) {
      const Original = window[name];
      window[name] = class extends Original { constructor(...args) { window.__nativeDecodeCalls.push(name); throw new Error('Native decoder forbidden'); } };
    }
  }
  HTMLMediaElement.prototype.play = function() { window.__nativeDecodeCalls.push('HTMLMediaElement.play'); throw new Error('Native media playback forbidden'); };
});
const report = { videos: [], errors: [], requests: [] };
page.on('pageerror', error => report.errors.push(error.message));
page.on('response', response => { if (response.status() >= 400) report.requests.push(response.status() + ' ' + response.url()); });
const state = () => page.locator('[data-player-engine]').getAttribute('data-player-state');
const engine = async (action, value) => page.evaluate(async ({action, value}) => {
  const {default: Player} = await import('/player-engine/libmedia-1.3.1/avplayer.js');
  const player = Player.Instances.at(-1);
  if (action === 'seek') await player.seek(BigInt(value));
  if (action === 'stats') {
    return { time: Number(player.currentTime - player.getMinStartPTS()), mse: player.isMSE(),
      videoFrames: Number(player.getStats().videoFrameRenderCount), audioFrames: Number(player.getStats().audioFrameDecodeCount),
      hardware: player.getOptions().enableHardware, webCodecs: player.getOptions().enableWebCodecs };
  }
  return Number(player.currentTime - player.getMinStartPTS());
}, {action, value});
try {
  for (const item of fixtures.videos.filter(v => !process.env.WASM_SAMPLE || v.name === process.env.WASM_SAMPLE)) {
    const result = {name: item.name, codec: item.codec, id:item.id};
    try {
      await fetch(base + '/api/videos/' + item.id + '/progress', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({position_ms:0,finished:false})});
      const info = await (await fetch(base + '/api/videos/' + item.id + '/playback-info?engine=wasm')).json();
      assert.equal(info.mode, 'wasm'); assert.equal(info.preparation_state, 'none');
      const bytes = await fetch(base + info.stream_url, {headers:{Range:'bytes=0-127'}});
      assert.equal(bytes.status,206);
      assert.ok((await bytes.arrayBuffer()).byteLength > 0);
      await page.goto(base + '/videos/' + item.id);
      await page.waitForFunction(() => ['ready','error'].includes(document.querySelector('[data-player-state]')?.dataset.playerState), null, {timeout:25000});
      if (await state() === 'error') {
        result.status = 'unsupported';
        result.detail = await page.locator('[data-player-engine] [role=alert]').innerText();
      } else {
        await page.getByRole('button', {name:'开始播放',exact:true}).click();
        await page.getByRole('button', {name:'暂停',exact:true}).waitFor({timeout:25000});
        await page.waitForFunction(async () => {
          const {default: Player} = await import('/player-engine/libmedia-1.3.1/avplayer.js');
          const p = Player.Instances.at(-1);
          return p && Number(p.currentTime - p.getMinStartPTS()) > 500 && Number(p.getStats().videoFrameRenderCount) > 2 && document.querySelector('[data-rendered=true]');
        }, null, {timeout:25000});
        await page.waitForTimeout(1000);
        result.stats = await engine('stats');
        assert.equal(result.stats.mse,false);
        assert.ok(result.stats.videoFrames > 0,'Actual rendered video frames');
        if(info.audio_codec) assert.ok(result.stats.audioFrames > 0,'Actual decoded audio frames');
        assert.equal(result.stats.webCodecs,false);
        assert.equal(await page.locator('video').count(),0);
        assert.deepEqual(await page.evaluate(() => window.__nativeDecodeCalls),[]);
        const slider = page.getByRole('slider', {name:'播放进度'});
        const rect = await slider.boundingBox();
        await slider.click({position:{x:rect.width / 4,y:rect.height / 2}});
        await page.waitForFunction(async () => {
          const {default: P} = await import('/player-engine/libmedia-1.3.1/avplayer.js');
          const t = Number(P.Instances.at(-1).currentTime - P.Instances.at(-1).getMinStartPTS());
          return t >= 2800 && t < 6000;
        }, null, {timeout:15000});
        await page.waitForTimeout(400);
        await page.getByRole('button', {name:'暂停',exact:true}).click();
        await page.getByRole('button', {name:'播放',exact:true}).waitFor();
        await page.waitForTimeout(500);
        result.seekMs = await engine('time');
        assert.ok(result.seekMs >= 2800 && result.seekMs < 6500, 'Seek reached target: ' + result.seekMs);
        result.status = 'passed';
        if (item.name === 'hevc.mp4') await page.screenshot({path:root+'/.run/wasm-validation/hevc.png',fullPage:true});
      }
    } catch (error) {result.status='failed';result.detail=error.message;}
    report.videos.push(result);
    console.log(JSON.stringify(result));
  }
  report.cacheFiles = await readdir(root+'/.run/wasm-validation/cache').catch(()=>[]);
} finally {
  await writeFile(root+'/.run/wasm-validation/'+reportName+'.json',JSON.stringify(report,null,2));
  await browser.close();
}
console.log('SUMMARY',JSON.stringify({passed:report.videos.filter(x=>x.status==='passed').length,total:report.videos.length,errors:report.errors,cacheFiles:report.cacheFiles}));

if (report.videos.some(v=>v.status==='failed') || report.errors.length) process.exitCode=1;
