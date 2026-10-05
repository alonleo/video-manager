// Actual HTTP/Chrome validation. Refuses to write progress outside the isolated test database.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '..');
const out = path.join(root, '.run/ts-validation');
const base = process.env.TS_AUDIT_URL || 'http://127.0.0.1:8421';
const health = await (await fetch(base + '/api/health')).json();
assert.ok(health.detail?.database_url?.includes('/.run/ts-validation/'), 'Only an isolated validation database is allowed');
const fixtures = JSON.parse(await readFile(path.join(out, 'fixtures.json'), 'utf8'));
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/google-chrome' });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const report = { checks: [], videos: [], errors: [], accessibility: [] };
const check = (ok, name) => { assert.ok(ok, name); report.checks.push(name); console.log('PASS', name); };
page.on('pageerror', e => report.errors.push(e.message));
let states = [];
page.on('response', async response => {
  if (response.url().includes('/playback-info') && response.ok()) {
    try { states.push((await response.json()).preparation_state); } catch {}
  }
});
const axe = await readFile(path.join(root, '.run/design-audit/tooling/node_modules/axe-core/axe.min.js'), 'utf8');
async function a11y(name) {
  await page.addScriptTag({ content: axe });
  const result = await page.evaluate(async () => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa'] } }));
  const violations = result.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.failureSummary) }));
  report.accessibility.push({name, violations});
  check(!violations.length, `${name}: automated accessibility`);
}
try {
  for (const item of fixtures.videos.filter(v => v.title !== 'corrupt')) {
    states = [];
    await page.goto(`${base}/videos/${item.id}`);
    if (item.title === 'mpeg2-ac3') {
      try {
        await page.getByRole('progressbar', { name: '播放准备进度' }).waitFor({timeout: 2000});
        check(true, 'Preparation progress appears');
        await page.screenshot({ path: path.join(out, 'ts-preparing.png'), animations: 'disabled' });
      } catch {
        const cached = await (await fetch(`${base}/api/videos/${item.id}/playback-info`)).json();
        check(cached.preparation_state === 'ready', 'Existing playback cache is immediately ready');
      }
    }
    await page.waitForFunction(() => { const v = document.querySelector('video'); return v?.readyState >= 2 && v.currentSrc.includes('/stream?version='); }, { timeout: 45000 });
    const info = await (await fetch(`${base}/api/videos/${item.id}/playback-info`)).json();
    check(info.preparation_state === 'ready', `${item.title}: prepared successfully`);
    const video = page.locator('video');
    await video.evaluate(v => { v.muted = true; return v.play(); });
    await page.waitForTimeout(250);
    check(await video.evaluate(v => !v.paused && v.currentTime > 0), `${item.title}: actually plays`);
    await video.evaluate(v => { v.currentTime = 8; });
    await page.waitForFunction(() => document.querySelector('video')?.currentTime >= 7.9);
    await video.evaluate(v => v.pause());
    await page.waitForFunction(async ({base,id}) => {
      const r = await (await fetch(`${base}/api/videos/${id}/progress`)).json();
      return r?.position_ms >= 7900;
    }, {base, id: item.id});
    check(true, `${item.title}: seek and progress saved`);
    await page.reload();
    await page.waitForFunction(() => document.querySelector('video')?.currentTime >= 7.9);
    check(true, `${item.title}: reload resumes saved position`);
    const range = await fetch(info.stream_url, { headers: { Range: 'bytes=0-127' } });
    check(range.status === 206 && (await range.arrayBuffer()).byteLength === 128, `${item.title}: real HTTP Range 206`);
    report.videos.push({ ...item, mode: info.mode, states: [...states] });
    if (item.title === 'h264-aac') {
      await page.screenshot({path:path.join(out,'ts-playing-desktop.png'), fullPage:true, animations:'disabled'});
      await a11y('TS ready desktop');
      await page.setViewportSize({width:390,height:844});
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'TS player fits mobile');
      await page.screenshot({path:path.join(out,'ts-playing-mobile.png'), fullPage:true, animations:'disabled'});
      await a11y('TS ready mobile');
      await page.setViewportSize({width:1440,height:1000});
    }
  }
  const bad = fixtures.videos.find(v => v.title === 'corrupt');
  await page.goto(`${base}/videos/${bad.id}`);
  await page.getByRole('heading',{name:'播放准备失败'}).waitFor();
  check(await page.getByRole('button',{name:'重新准备',exact:true}).isVisible(), 'Corrupt TS shows explicit retry');
  const retry = page.waitForResponse(r => r.url().includes('/playback-retry') && r.request().method() === 'POST');
  await page.getByRole('button',{name:'重新准备',exact:true}).click();
  check((await retry).ok(), 'User can retry preparation');
  await page.getByRole('heading',{name:'播放准备失败'}).waitFor();
  const failedState = (await (await fetch(`${base}/api/videos/${bad.id}/playback-info`)).json()).preparation_state;
  check(failedState === 'error', 'Failed preparation is stable until user retries');
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(out,'ts-error-mobile.png'),fullPage:true,animations:'disabled'});
  await a11y('TS error mobile');
  check(!report.errors.length, 'No uncaught browser errors');
} catch (error) {
  report.failure = error.stack; console.error(error); process.exitCode = 1;
} finally {
  await writeFile(path.join(out,'browser-results.json'), JSON.stringify(report,null,2));
  await browser.close();
  console.log('Checks:', report.checks.length);
}
