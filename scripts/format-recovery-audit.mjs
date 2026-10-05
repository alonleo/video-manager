// Regression: automatic retry budget must never disable the user's reload button.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..');
const out=path.join(root,'.run/format-validation');
const base=process.env.FORMAT_AUDIT_URL||'http://127.0.0.1:8422';
const health=await(await fetch(base+'/api/health')).json();
assert.ok(health.detail?.database_url?.includes('/.run/format-validation/'));
const fixtures=JSON.parse(await readFile(path.join(out,'fixtures.json'),'utf8'));
const item=fixtures.videos.find(v=>v.name==='sample.wmv');
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/usr/bin/google-chrome'});
const page=await browser.newPage();
const report={checks:[],errors:[]};let blocked=0;
page.on('pageerror',e=>report.errors.push(e.message));
const check=(ok,name)=>{assert.ok(ok,name);report.checks.push(name);console.log('PASS',name);};
try{
  await page.route('**/api/videos/'+item.id+'/stream?*',async route=>{
    blocked++; await route.fulfill({status:200,contentType:'video/mp4',body:'invalid-media-for-retry-budget'});
  });
  await page.goto(base+'/videos/'+item.id);
  await page.waitForFunction(()=>document.body.textContent.includes('无法播放'));
  await page.waitForTimeout(1000);
  check(blocked===2,'Automatic refresh is bounded after two failed loads');
  await page.unroute('**/api/videos/'+item.id+'/stream?*');
  await page.getByRole('button',{name:'重新加载',exact:true}).click();
  await page.waitForFunction(()=>{const v=document.querySelector('video');return v?.readyState>=2&&!v.error;},null,{timeout:30000});
  await page.locator('video').evaluate(v=>{v.muted=true;return v.play();});
  check(await page.locator('video').evaluate(v=>!v.paused&&!v.error),'Manual reload succeeds after automatic budget is exhausted');
  check(!report.errors.length,'No browser exceptions during repeated-failure recovery');
}catch(error){report.failure=error.stack;console.error(error);process.exitCode=1;}
finally{await writeFile(path.join(out,'recovery-results.json'),JSON.stringify(report,null,2));await browser.close();}
