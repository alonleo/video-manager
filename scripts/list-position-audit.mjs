// Real navigation/media with isolated API; delayed responses exercise cold list restoration.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..');
const out=path.join(root,'.run/scroll-validation');
const base=process.env.SCROLL_AUDIT_URL||'http://127.0.0.1:18424';
const health=await(await fetch(base+'/api/health')).json();
assert.ok(health.detail?.database_url?.includes('/.run/scroll-validation/'));
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/usr/bin/google-chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const report={checks:[],positions:[],errors:[]};
page.on('pageerror',e=>report.errors.push(e.message));
const check=(ok,name)=>{assert.ok(ok,name);report.checks.push(name);console.log('PASS',name);};
async function library(p=page){
 await p.locator('[data-video-id]').first().waitFor();
 await p.waitForFunction(()=>document.querySelector('.library-page')?.dataset.positionRestoring!=='true'&&document.querySelector('.collection-content')?.getAttribute('aria-busy')==='false',null,{timeout:20000});
 await p.locator('.library-page').evaluate(el=>Promise.all(el.getAnimations().map(a=>a.finished.catch(()=>{}))));
}
async function position(index,p=page){
 await library(p);
 const card=p.locator('[data-video-id]').nth(index);
 await card.evaluate(el=>{window.scrollTo({top:window.scrollY+el.getBoundingClientRect().top-130,behavior:'instant'});el.focus({preventScroll:true});});
 await p.waitForTimeout(150);
 return card.evaluate(el=>({id:Number(el.dataset.videoId),y:scrollY,offset:el.getBoundingClientRect().top,search:location.search}));
}
async function open(snapshot,p=page){
 await p.locator('[data-video-id="'+snapshot.id+'"]').click();
 await p.locator('.player-heading').waitFor();
 await p.waitForFunction(()=>scrollY<3);
}
async function restored(snapshot,name,p=page,exactY=true){
 await library(p);
 const actual=await p.locator('[data-video-id="'+snapshot.id+'"]').evaluate(el=>({y:scrollY,offset:el.getBoundingClientRect().top,search:location.search,focused:el===document.activeElement}));
 report.positions.push({name,expected:snapshot,actual});
 check(Math.abs(actual.offset-snapshot.offset)<4,name+': same card and viewport position');
 if(exactY)check(Math.abs(actual.y-snapshot.y)<4,name+': scroll distance restored');
 check(actual.search===snapshot.search,name+': filters/page/view preserved');
 check(actual.focused,name+': keyboard focus returns to the card');
}
async function delayLists(ms){
 await page.route('**/api/videos?*',async route=>{
   const response=await route.fetch();await new Promise(resolve=>setTimeout(resolve,ms));await route.fulfill({response});
 });
}
const back=()=>page.getByRole('link',{name:'返回',exact:true}).click();
try{
 await page.goto(base+'/');
 const first=await position(18);check(first.y>1000,'Test begins deep in a real grid');
 await open(first);
 await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
 await page.locator('video').evaluate(v=>{v.muted=true;return v.play();});
 await page.waitForTimeout(150);await page.locator('video').evaluate(v=>v.pause());
 check(await page.locator('video').evaluate(v=>v.currentTime>0),'Video actually plays before returning');
 await back();await restored(first,'Page return');
 await page.screenshot({path:path.join(out,'desktop-restored.png'),animations:'disabled'});
 const second=await position(10);await open(second);
 await page.evaluate(()=>window.scrollTo({top:600,behavior:'instant'}));
 await page.goBack();await restored(second,'Browser back');
 await page.goForward();await page.locator('.player-heading').waitFor();
 check(await page.evaluate(()=>scrollY<3),'Forward opens player at the top');
 await page.goBack();await restored(second,'Back after forward');
 // Include directory category when available, plus every normal list criterion.
 const category=await page.locator('.directory-filter select option').evaluateAll(options=>options.find(o=>o.value)?.value||'');
 const query=new URLSearchParams({q:'Scroll',tag:'位置测试',favorite:'true',sort:'title',page:'2',view:'list'});
 if(category)query.set('category',category);
 await page.goto(base+'/?'+query);
 const filtered=await position(16);await open(filtered);
 await page.reload();await page.locator('.player-heading').waitFor();
 await delayLists(1100);await back();await restored(filtered,'Refreshed player and delayed list');
 await page.unroute('**/api/videos?*');
 check(!!category,'Directory category is covered by restoration');
 // The new return entry must retain its own latest position on a full reload.
 const refreshed=await position(6);await page.reload();await restored(refreshed,'List reload');
 // A responsive layout should find the same card even when its document Y changes.
 const resized=await position(12);await open(resized);
 await page.setViewportSize({width:390,height:844});await back();await restored(resized,'Resize while watching',page,false);
 await page.screenshot({path:path.join(out,'mobile-restored.png'),animations:'disabled'});
 const mobile=await position(19);await open(mobile);await page.goBack();await restored(mobile,'Mobile browser back');
 // A user scroll gesture cancels a pending delayed restore.
 const interrupted=await position(15);await open(interrupted);await page.reload();
 await page.locator('.player-heading').waitFor();await delayLists(1300);await back();
 await page.locator('.library-page').waitFor();await page.mouse.wheel(0,260);
 await library();check(await page.evaluate(()=>scrollY<600),'User scrolling cancels pending restoration');
 await page.unroute('**/api/videos?*');
 // If a video leaves the active filter, use distance without getting stuck.
 await page.setViewportSize({width:1440,height:1000});
 await page.goto(base+'/?'+query);const missing=await position(9);await open(missing);
 const patch=await fetch(base+'/api/videos/'+missing.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({is_favorite:false})});
 assert.ok(patch.ok);await page.reload();await page.locator('.player-heading').waitFor();await back();await library();
 check(await page.locator('[data-video-id="'+missing.id+'"]').count()===0,'Removed/filter-excluded anchor is absent');
 check(Math.abs(await page.evaluate(()=>scrollY)-missing.y)<4,'Missing anchor falls back to saved scroll distance');
 await fetch(base+'/api/videos/'+missing.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({is_favorite:true})});
 // An unrelated direct player link has no library snapshot to restore.
 await page.goto(base+'/videos/1');await page.locator('.player-heading').waitFor();await back();await library();
 check(await page.evaluate(()=>scrollY<3),'Direct video link returns to a fresh library normally');
 const privateContext=await browser.newContext({viewport:{width:1440,height:1000}});
 await privateContext.addInitScript(()=>Object.defineProperty(window,'sessionStorage',{get(){throw new DOMException('Disabled','SecurityError');}}));
 const privatePage=await privateContext.newPage();privatePage.on('pageerror',e=>report.errors.push(e.message));
 await privatePage.goto(base+'/');const volatile=await position(15,privatePage);await open(volatile,privatePage);
 await privatePage.getByRole('link',{name:'返回',exact:true}).click();await restored(volatile,'Storage unavailable',privatePage);
 await privateContext.close();
 check(!report.errors.length,'No uncaught browser exceptions');
}catch(error){report.failure=error.stack;console.error(error);await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});process.exitCode=1;}
finally{await writeFile(path.join(out,'browser-results.json'),JSON.stringify(report,null,2));await browser.close();console.log('Checks:',report.checks.length);}
