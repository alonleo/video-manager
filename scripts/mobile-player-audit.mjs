// Real playback and portrait UI checks against an isolated validation server.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.MOBILE_PLAYER_AUDIT_URL||'http://127.0.0.1:8428';
assert.ok(['8428'].includes(new URL(base).port),'Use the isolated mobile player server');
const out='.run/mobile-portrait-fix';await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/usr/bin/google-chrome',args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
// The isolated server has no device-auth routes; do not touch real device registrations.
await page.route('**/api/device-auth/status',r=>r.fulfill({json:{initialized:true,authenticated:true}}));
async function open(){
 await page.goto(base+'/videos/6');
 await page.getByRole('button',{name:'开始播放',exact:true}).waitFor();
 await page.locator('.player-stage').scrollIntoViewIfNeeded();
}
try {
 for(const width of [320,390,430]) {
  await page.setViewportSize({width,height:844});await open();
  await page.getByRole('button',{name:'倍速',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'播放速度',exact:true});
  await dialog.waitFor({timeout:3000});
  const slow=await page.getByRole('button',{name:'2×',exact:true}).boundingBox();
  assert.ok(slow&&slow.y+slow.height<=844,'Both rows of speed options fit');
  await page.screenshot({path:`${out}/speed-after-${width}.png`});
  await page.getByRole('button',{name:'2×',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  const rate=await page.evaluate(async()=>{const {default:P}=await import('/player-engine/libmedia-1.3.1/avplayer.js');return P.Instances.at(-1).getPlaybackRate();});
  assert.equal(rate,2,'Selection reaches the decoder');
  await page.getByRole('button',{name:'字幕',exact:true}).click();
  const captions=page.getByRole('dialog',{name:'字幕菜单',exact:true});await captions.waitFor();
  const box=await captions.boundingBox();assert.ok(box.height>320,'Subtitle settings have usable height');
  assert.ok(box.x>=0&&box.x+box.width<=width&&box.y>=0&&box.y+box.height<=844,'Sheet fits portrait viewport');
  await page.screenshot({path:`${out}/captions-after-${width}.png`});
  await page.keyboard.press('Shift+Tab');
  assert.ok(await captions.evaluate(el=>el.contains(document.activeElement)),'Keyboard focus stays inside the modal');
  await page.getByRole('button',{name:'关闭播放设置',exact:true}).click();
  await captions.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'字幕',exact:true}).click();
  await page.touchscreen.tap(2,2);
  await captions.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'音量设置',exact:true}).click();
  await page.getByRole('button',{name:'静音',exact:true}).click();
  await page.getByRole('button',{name:'取消静音',exact:true}).waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('dialog').count(),0);
  assert.equal(await page.locator('body').evaluate(el=>el.style.overflow),'');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  console.log('PASS',width,'portrait: speed, captions, volume, dismissal, viewport');
 }
 await page.getByRole('button',{name:'倍速',exact:true}).click();
 await page.setViewportSize({width:844,height:390});
 await page.getByRole('dialog').waitFor({state:'hidden'});
 assert.equal(await page.locator('body').evaluate(el=>el.style.overflow),'');
 await page.setViewportSize({width:430,height:844});
 await page.getByRole('button',{name:'关闭播放设置',exact:true}).click();
 await page.getByRole('button',{name:'开始播放',exact:true}).click();
 await page.waitForFunction(async()=>{const {default:P}=await import('/player-engine/libmedia-1.3.1/avplayer.js');return Number(P.Instances.at(-1).getStats().videoFrameRenderCount)>2;});
 await page.getByRole('button',{name:'暂停',exact:true}).click();
 await page.evaluate(async()=>{
  const {default:P}=await import('/player-engine/libmedia-1.3.1/avplayer.js');
  const player=P.Instances.at(-1), original=player.setRenderMode.bind(player);
  window.__renderModes=[];
  player.setRenderMode=mode=>{window.__renderModes.push(mode);return original(mode);};
 });
 await page.getByRole('button',{name:'全屏',exact:true}).click();
 await page.waitForTimeout(500);
 assert.deepEqual(await page.evaluate(()=>window.__renderModes),[0],'Portrait fullscreen fits the whole frame');
 await page.screenshot({path:`${out}/fullscreen-fit-after-430.png`});
 await page.getByRole('button',{name:'字幕',exact:true}).click();
 assert.ok(await page.evaluate(()=>document.fullscreenElement?.contains(document.querySelector('.player-settings-sheet'))),'Sheet remains visible in fullscreen');
 await page.getByRole('button',{name:'关闭播放设置',exact:true}).click();
 await page.getByRole('button',{name:'退出全屏',exact:true}).click();
 console.log('PASS real Wasm playback and portrait fullscreen');
 await page.setViewportSize({width:844,height:390});await open();
 await page.getByRole('button',{name:'倍速',exact:true}).click();
 assert.equal(await page.getByRole('dialog').count(),0,'Landscape keeps the inline menu');
 await page.setViewportSize({width:1440,height:1000});await open();
 await page.getByRole('button',{name:'倍速',exact:true}).click();
 assert.equal(await page.getByRole('dialog').count(),0,'Desktop keeps the inline menu');
 assert.deepEqual(errors,[]);console.log('PASS landscape, desktop, no uncaught errors');
} finally {await browser.close();}
