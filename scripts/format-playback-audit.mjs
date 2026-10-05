// Real HTTP/media validation against an isolated SQLite database only.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '..');
const out = path.join(root, '.run/format-validation');
const base = process.env.FORMAT_AUDIT_URL || 'http://127.0.0.1:8422';
const health = await (await fetch(`${base}/api/health`)).json();
assert.ok(health.detail?.database_url?.includes('/.run/format-validation/'), 'Only isolated validation databases are allowed');
const fixtures = JSON.parse(await readFile(path.join(out, 'fixtures.json'), 'utf8'));
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/google-chrome' });
const page = await browser.newPage({viewport:{width:1440,height:1000}});
const report = {checks:[], videos:[], errors:[], accessibility:[]};
const check = (ok,name) => { assert.ok(ok,name); report.checks.push(name); console.log('PASS',name); };
page.on('pageerror', e => report.errors.push(e.message));
const axe = await readFile(path.join(root,'.run/design-audit/tooling/node_modules/axe-core/axe.min.js'),'utf8');
async function a11y(name){
  await page.addScriptTag({content:axe});
  const result=await page.evaluate(async()=>window.axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  const violations=result.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.failureSummary)}));
  report.accessibility.push({name,violations}); check(!violations.length,`${name}: automated accessibility`);
}
async function playable(){
  await page.waitForFunction(()=>{const v=document.querySelector('video');return v?.readyState>=2&&!v.error;},null,{timeout:60000});
}
try {
  for(const item of fixtures.videos.filter(v=>v.name!=='corrupt.avi')){
    await page.goto(`${base}/videos/${item.id}`);
    await playable();
    const info=await(await fetch(`${base}/api/videos/${item.id}/playback-info`)).json();
    check(info.mode===item.mode && !!info.stream_url,`${item.name}: correct playback path`);
    const video=page.locator('video');
    await video.evaluate(v=>{v.muted=true;return v.play();});
    await page.waitForTimeout(250);
    check(await video.evaluate(v=>!v.paused&&v.currentTime>0&&!v.error),`${item.name}: actually plays`);
    await video.evaluate(v=>{v.currentTime=7;});
    await page.waitForFunction(()=>{const v=document.querySelector('video');return v?.currentTime>=6.9&&!v.seeking&&v.readyState>=2;});
    await video.evaluate(v=>v.pause());
    await page.waitForFunction(async({base,id})=>(await(await fetch(`${base}/api/videos/${id}/progress`)).json())?.position_ms>=6900,{base,id:item.id});
    check(true,`${item.name}: seek and save progress`);
    await page.reload(); await playable();
    await page.waitForFunction(()=>document.querySelector('video')?.currentTime>=6.9);
    check(true,`${item.name}: reload resumes`);
    const range=await fetch(info.stream_url,{headers:{Range:'bytes=0-127'}});
    check(range.status===206&&(await range.arrayBuffer()).byteLength===128,`${item.name}: HTTP Range 206`);
    report.videos.push({...item,mode:info.mode,state:info.preparation_state});
    if(item.name==='sample.wmv'){
      await page.screenshot({path:path.join(out,'wmv-desktop.png'),fullPage:true,animations:'disabled'});
      await a11y('Converted WMV desktop');
      await page.setViewportSize({width:390,height:844});
      check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Player fits mobile');
      await page.screenshot({path:path.join(out,'wmv-mobile.png'),fullPage:true,animations:'disabled'});
      await a11y('Converted WMV mobile');
      await page.setViewportSize({width:1440,height:1000});
    }
  }
  check(new Set(report.videos.map(v=>path.extname(v.name))).size===17,'All 17 extensions played in Chrome');
  const direct=fixtures.videos.find(v=>v.name==='sample.mp4');
  await page.goto(`${base}/videos/${direct.id}`); await playable();
  await page.getByRole('button',{name:'兼容播放',exact:true}).click();
  await page.waitForURL(/compatible=1/); await playable();
  check(await page.locator('video').evaluate(v=>v.currentSrc.includes('compatible=true')&&v.currentTime>=6.9),'Manual compatibility preserves position');
  await page.reload(); await playable();
  check(await page.locator('video').evaluate(v=>v.currentSrc.includes('compatible=true')),'Compatibility choice survives reload');
  // Deliberately inject one unsupported direct-media response to verify automatic recovery.
  const fallback=fixtures.videos.find(v=>v.name==='sample.m4v');
  const route=`${base}/api/videos/${fallback.id}/stream`;
  await page.route(route,r=>r.fulfill({status:200,contentType:'application/octet-stream',body:'invalid-media-for-recovery-check'}),{times:1});
  await page.goto(`${base}/videos/${fallback.id}`);
  await page.waitForURL(/compatible=1/); await playable();
  await page.locator('video').evaluate(v=>{v.muted=true;return v.play();});
  await page.waitForTimeout(200);
  check(await page.locator('video').evaluate(v=>v.currentSrc.includes('compatible=true')&&!v.error&&!v.paused),'Unsupported direct media automatically converts and plays');
  const bad=fixtures.videos.find(v=>v.name==='corrupt.avi');
  await page.goto(`${base}/videos/${bad.id}`);
  await page.getByRole('heading',{name:'播放准备失败'}).waitFor();
  check(await page.getByRole('button',{name:'重新准备',exact:true}).isVisible(),'Corrupt AVI has explicit retry');
  const retry=page.waitForResponse(r=>r.url().includes('/playback-retry')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'重新准备',exact:true}).click();check((await retry).ok(),'Failed conversion can be retried');
  await page.getByRole('heading',{name:'播放准备失败'}).waitFor();
  await page.setViewportSize({width:390,height:844}); await a11y('Conversion error mobile');
  await page.screenshot({path:path.join(out,'format-error-mobile.png'),fullPage:true,animations:'disabled'});
  check(!report.errors.length,'No uncaught browser errors');
}catch(error){report.failure=error.stack;console.error(error);process.exitCode=1;}
finally{await writeFile(path.join(out,'browser-results.json'),JSON.stringify(report,null,2));await browser.close();console.log('Checks:',report.checks.length);}
