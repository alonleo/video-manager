"""Build isolated media fixtures; never touches the real library."""
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.run/format-validation'
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(DATABASE_URL=f'sqlite+aiosqlite:///{OUT}/test.db', DATA_DIR=str(OUT/'data'), CACHE_DIR=str(OUT/'cache'), PLAYBACK_MIN_FREE_MB='0', CACHE_MAX_GB='2')
sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'backend/tests')]
from test_all_formats import build_format_samples, CASES
from app.db import engine, SessionLocal
from app.models import Base, Storage, StorageType, Video, VideoSource
from app.services.library import scan_storage
from sqlalchemy import select

async def main():
    samples = build_format_samples(OUT/'samples')
    rv40=OUT/'sources/complete-rv40.rmvb'
    if rv40.is_file(): shutil.copyfile(rv40,samples/'real-rv40.rmvb')
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        storage=(await s.execute(select(Storage).where(Storage.name=='format-validation'))).scalar_one_or_none()
        if not storage:
            storage=Storage(name='format-validation',type=StorageType.local,config={'root':str(samples)})
            s.add(storage);await s.commit()
        stats=await scan_storage(s,storage.id)
        print('scan',stats,flush=True)
        bad=samples/'corrupt.avi';bad.write_bytes(b'not a video')
        if not (await s.execute(select(Video).where(Video.title=='corrupt'))).scalar_one_or_none():
            v=Video(title='corrupt',video_codec='mpeg4',container='avi',duration_ms=12000)
            s.add(v);await s.flush();s.add(VideoSource(video_id=v.id,storage_id=storage.id,source_type='local',uri=str(bad),local_path=str(bad),is_primary=True,available=True));await s.commit()
        rows=(await s.execute(select(Video,VideoSource).join(VideoSource))).all()
        modes={c[0]:c[-1] for c in CASES};modes['real-rv40.rmvb']='transcode';modes['corrupt.avi']='transcode'
        result={'videos':[{'id':v.id,'name':Path(src.local_path).name,'mode':modes[Path(src.local_path).name],'codec':v.video_codec} for v,src in rows]}
        (OUT/'fixtures.json').write_text(json.dumps(result,indent=2))
        print('prepared',len(rows),'isolated videos',flush=True)
    await engine.dispose()
asyncio.run(main())
