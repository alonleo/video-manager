// UI navigation regression with isolated API fixtures; no production data is accessed.
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({headless:true,executablePath:'/usr/bin/google-chrome'});
const page = await browser.newPage({viewport:{width:1440,height:1000}});
page.setDefaultTimeout(8000);
page.on('console',m=>{if(m.type()==='error')console.log(m.text());});
const errors=[];
page.on('pageerror',e=>{errors.push(e.message);console.log('PAGEERROR',e.message);});
await page.route('**/api/**',async route=>{
 const url=new URL(route.request().url());
 if(!url.pathname.startsWith('/api/')) return route.continue();
 let body=[];
 if(url.pathname.endsWith('/device-auth/status')) body={initialized:true,authenticated:true};
 else if(url.pathname==='/api/jobs') body={items:[],total:0};
 else if(url.pathname==='/api/health') body={database:true,ffmpeg:true,redis:true};
 else if(url.pathname==='/api/videos') {
  const pageNumber=Number(url.searchParams.get('page') || 1);
  body={total:2400,page:pageNumber,page_size:24,items:Array.from({length:24},(_,i)=>({id:(pageNumber-1)*24+i+1,title:'Navigation fixture '+i,media_type:url.searchParams.get('media_type')||'video',is_favorite:true,status:'ready',tags:[],categories:[],created_at:'2026-10-06',duration_ms:12000}))};
 } else if(url.pathname==='/api/categories') body={items:[],roots:[],warnings:[]};
 else if(url.pathname.includes('/notifications')) body={items:[],cursor:0,pending:[],has_more:false};
 await route.fulfill({json:body});
});
const base=process.env.LIBRARY_AUDIT_URL||'http://127.0.0.1:18425';
const nav=name=>page.getByRole('navigation',{name:'主导航'}).getByRole('link',{name,exact:true});
async function ready(){await page.locator('[data-video-id]').first().waitFor();await page.waitForFunction(()=>document.querySelector('.collection-content')?.getAttribute('aria-busy')==='false'&&document.querySelector('.library-page')?.dataset.positionRestoring!=='true');}
async function roundtrip(expected){await nav('任务中心').click();await nav('视频库').click();await ready();assert.equal(new URL(page.url()).search,expected);}
try {
 await page.goto(base+'/?page=63&sort=title_asc&view=list');await ready();
 await page.waitForTimeout(400);
 await page.locator('[data-video-id]').nth(15).evaluate(el=>window.scrollTo(0,scrollY+el.getBoundingClientRect().top-140));
 const before=await page.evaluate(()=>scrollY);
 await roundtrip('?page=63&sort=title_asc&view=list');
 assert.ok(Math.abs(await page.evaluate(()=>scrollY)-before)<4,'Scroll position restored');
 console.log('PASS: page 63, sorting, view and scroll survive module navigation');
 await nav('任务中心').click();await page.reload();await nav('视频库').click();await ready();
 assert.equal(new URL(page.url()).search,'?page=63&sort=title_asc&view=list');
 console.log('PASS: module refresh retains the library destination');
 await nav('照片库').click();await ready();assert.equal(new URL(page.url()).pathname,'/photos');assert.equal(new URL(page.url()).search,'');
 await page.goto(base+'/photos?page=7');await ready();await roundtrip('?page=63&sort=title_asc&view=list');
 await nav('照片库').click();await ready();assert.equal(new URL(page.url()).search,'?page=7');
 console.log('PASS: photo and video libraries retain independent pages');
 await nav('我的收藏').click();await ready();assert.equal(new URL(page.url()).search,'?favorite=true');
 await page.goto(base+'/?favorite=true&page=9');await ready();await roundtrip('?page=63&sort=title_asc&view=list');
 await nav('我的收藏').click();await ready();assert.equal(new URL(page.url()).search,'?favorite=true&page=9');
 console.log('PASS: favorites retain an independent page');
 await page.goto(base+'/?page=2');await ready();await roundtrip('?page=2');
 console.log('PASS: explicit URLs take precedence over remembered pages');
 assert.deepEqual(errors,[]);
} catch(e) {console.log(await page.locator('body').innerText());throw e;} finally {await browser.close();}
