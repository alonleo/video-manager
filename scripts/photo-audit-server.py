"""Isolated photo fixtures and UI audit server. Does not scan the real library."""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.run/photo-audit'
OUT.mkdir(exist_ok=True, parents=True)
os.environ.update(DATABASE_URL=f'sqlite+aiosqlite:///{OUT}/audit.db', DATA_DIR=str(OUT/'data'), CACHE_DIR=str(OUT/'cache'), WORK_DIR=str(OUT/'work'), DOWNLOADS_DIR=str(OUT/'downloads'))
sys.path.insert(0, str(ROOT/'backend'))
from PIL import Image, ImageDraw
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.db import engine, SessionLocal
from app.models import Base, Storage, StorageType
from app.services.library import scan_storage
from app.api import photos, videos, categories, misc, storages, operations, jobs

async def prepare():
    if (OUT/'audit.db').exists(): return
    root=OUT/'media'
    root.mkdir(exist_ok=True)
    for n in range(27):
        im=Image.new('RGB',(600,900) if n%3==0 else (1200,800),(85+n*5,90+n*3,70+n*4))
        draw=ImageDraw.Draw(im)
        draw.ellipse((im.width*.3,80,im.width*.65,im.width*.35+80),fill=(241,210,150))
        draw.polygon([(0,im.height),(im.width*.55,im.height*.45),(im.width,im.height)], fill=(50+n*3,70+n*2,60))
        im.save(root/f'Moment-{n:02}.jpg')
    (root/'Only-video.mp4').write_bytes(b'video-fixture'*100)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        storage=Storage(name='Photo validation',type=StorageType.local,config={'root':str(root)})
        session.add(storage);await session.commit()
        stats=await scan_storage(session,storage.id,do_probe=False)
        print(stats.as_dict())

app=FastAPI()
@app.get('/api/health')
def health(): return {'database':True,'ffmpeg':True,'redis':True,'openlist':None,'vaapi':None,'status':'ok','detail':{}}
for module in [photos,videos,categories,misc,storages,operations,jobs]:app.include_router(module.router)
dist=ROOT/'.run/photo-dist'
app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
@app.get('/{path:path}')
async def index(path:str):return FileResponse(dist/'index.html')
if __name__=='__main__':
    asyncio.run(prepare())
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8439)
