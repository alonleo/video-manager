"""Disposable UI audit app. Never uses the real library DB or external AI services."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT.joinpath('.run/subtitle-validation').mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix='ui-', dir=ROOT / '.run/subtitle-validation'))
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{WORK / "audit.db"}'
os.environ['WORK_DIR'] = str(WORK / 'work')
os.environ['CACHE_DIR'] = str(WORK / 'cache')
sys.path.insert(0, str(ROOT / 'backend'))

from fastapi import Request
from app.main import app
from app.db import engine, SessionLocal
from app.models import Base, Video, VideoSource, Storage, StorageType, Job, JobState, Setting
from app.api import subtitles as api
from app.workers.tasks import run_job
from app.services.subtitle_config import SubtitleConfig

pending = set()
async def enqueue(kind, **kwargs):
    async with SessionLocal() as s:
        job = Job(kind=kind, video_id=kwargs.get('video_id'), payload={k: v for k, v in kwargs.items() if k != 'video_id'}, state=JobState.queued)
        s.add(job)
        await s.commit()
        job_id = job.id
    task = asyncio.create_task(run_job({}, job_id, kind, kwargs))
    pending.add(task)
    task.add_done_callback(pending.discard)
    return job_id
api.enqueue_job = enqueue

async def models():
    return {'data': [{'id': 'audit-translator'}, {'id': 'audit-whisper'}]}
async def chat(request: Request):
    body = await request.json()
    source = json.loads(body['messages'][1]['content'])
    return {'choices': [{'message': {'content': json.dumps({'translations': [{'id': c['id'], 'text': '你好，字幕世界。' if c['id'] == 0 else '双语字幕已准备就绪。'} for c in source]})}}]}
async def speech():
    return {'language': 'en', 'segments': [{'start': 0, 'end': 3, 'text': 'Hello from the audio fixture.'}]}
for path, endpoint, methods in [('/mock/v1/models', models, ['GET']), ('/mock/v1/chat/completions', chat, ['POST']), ('/mock/v1/audio/transcriptions', speech, ['POST'])]:
    app.add_api_route(path, endpoint, methods=methods)
    app.router.routes.insert(0, app.router.routes.pop())

async def seed():
    media = WORK / 'sample.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=0x24372f:s=640x360:r=24', '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '8', '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(media)], check=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        v = Video(title='字幕工作台 · 验证影像', container='mov', video_codec='h264', audio_codec='aac', pix_fmt='yuv420p', duration_ms=8000, width=640, height=360)
        storage = Storage(name='Audit fixture', type=StorageType.local, config={'root': str(WORK)})
        s.add_all([v, storage]); await s.flush()
        s.add(VideoSource(video_id=v.id, storage_id=storage.id, source_type=StorageType.local, uri='local:' + str(media), local_path=str(media), available=True, is_primary=True))
        config = SubtitleConfig()
        config.asr_provider = 'api'; config.asr_api.base_url = 'http://127.0.0.1:8423/mock/v1'; config.asr_api.model = 'audit-whisper'
        config.translation_provider = 'api'; config.translation_api.base_url = 'http://127.0.0.1:8423/mock/v1'; config.translation_api.model = 'audit-translator'; config.translation_api.api_key = 'audit-only-not-a-real-key'
        s.add(Setting(key='subtitle.providers', value=config.model_dump(), is_secret=True)); await s.commit()

if __name__ == '__main__':
    asyncio.run(seed())
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8423)
