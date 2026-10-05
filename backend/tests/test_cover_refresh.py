"""Recycled video IDs, short clips and download publication must not reuse posters."""
import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Base, Job, JobState, Video
from app.services import probe
from app.services import downloads as service
from test_downloads import env, media, create, execute, status  # isolated download fixtures


@pytest.fixture
def short_clips(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    clips = []
    for color in ("red", "blue"):
        path = tmp_path / f"{color}.mp4"
        subprocess.run([
            settings.ffmpeg_bin, "-v", "error", "-f", "lavfi",
            "-i", f"color=c={color}:s=160x90:r=10", "-t", "0.5",
            "-c:v", "libx264", "-threads", "1", "-y", str(path),
        ], check=True)
        clips.append(path)
    return clips


@pytest.mark.asyncio
async def test_recycled_id_gets_fresh_images_even_for_subsecond_clips(short_clips):
    pairs = [await probe.extract_thumbnails(str(clip), 17, 500) for clip in short_clips]
    assert all(cover and thumb for cover, thumb in pairs)
    assert pairs[0] != pairs[1]
    for index in (0, 1):
        first, second = (settings.data_dir / pair[index] for pair in pairs)
        assert first.read_bytes() != second.read_bytes()


@pytest.mark.asyncio
async def test_success_without_a_frame_never_accepts_legacy_images(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    for kind in ("covers", "thumbs"):
        (tmp_path / kind).mkdir()
        (tmp_path / kind / "17.jpg").write_bytes(b"old video image")
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
    monkeypatch.setattr(probe.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    assert await probe.extract_thumbnails("empty.mp4", 17, 500) == (None, None)
    assert not list(tmp_path.glob("*/*-*.jpg"))


@pytest.mark.asyncio
async def test_thumbnail_failure_does_not_return_a_partial_image(short_clips, monkeypatch):
    original = probe.asyncio.create_subprocess_exec
    async def process(*cmd, **kwargs):
        if str(cmd[-1]).startswith(str(settings.data_dir / "thumbs")):
            Path(cmd[-1]).write_bytes(b"partial")
            return SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", b"failed")))
        return await original(*cmd, **kwargs)
    monkeypatch.setattr(probe.asyncio, "create_subprocess_exec", process)
    cover, thumb = await probe.extract_thumbnails(str(short_clips[0]), 17, 500)
    assert cover and thumb is None
    assert not list((settings.data_dir / "thumbs").iterdir())


@pytest.mark.asyncio
async def test_recycled_id_cover_api_serves_new_images_and_revalidates(short_clips, tmp_path):
    pairs = [await probe.extract_thumbnails(str(clip), 17, 500) for clip in short_clips]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/api.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def dependency():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    try:
        async with sessions() as session:
            old = Video(title="old", cover_path=pairs[0][0], thumb_path=pairs[0][1])
            session.add(old)
            await session.commit()
            video_id = old.id
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            old_image = await client.get(f"/api/videos/{video_id}/cover")
            assert old_image.status_code == 200
            assert (await client.delete(f"/api/videos/{video_id}")).status_code == 204
            async with sessions() as session:
                new = Video(title="new", cover_path=pairs[1][0], thumb_path=pairs[1][1])
                session.add(new)
                await session.commit()
                assert new.id == video_id
            for route, index in (("cover", 0), ("thumb", 1)):
                response = await client.get(f"/api/videos/{video_id}/{route}", params={"v": pairs[1][index]})
                assert response.status_code == 200
                assert response.content == (settings.data_dir / pairs[1][index]).read_bytes()
                assert response.headers["cache-control"] == "private, no-cache"
                assert response.content != old_image.content
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_download_completion_includes_the_generated_cover(env, monkeypatch):
    async def prepare(*_args):
        async with env.sessions() as session:
            job = await session.scalar(select(Job))
            assert job.state == JobState.running
            assert await session.scalar(select(Video)) is None
        for relative in ("covers/new-test.jpg", "thumbs/new-test.jpg"):
            path = settings.data_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"new image")
        return "covers/new-test.jpg", "thumbs/new-test.jpg"
    monkeypatch.setattr(service.probe, "extract_thumbnails", prepare)
    job = await create(env)
    result = await execute(env, job)
    assert (await status(env, job))["state"] == "done"
    video = (await env.client.get(f"/api/videos/{result['video_id']}")).json()
    assert video["cover_path"] == "covers/new-test.jpg"
    assert video["thumb_path"] == "thumbs/new-test.jpg"


@pytest.mark.asyncio
async def test_cancel_during_cover_preparation_does_not_import_or_leave_images(env, monkeypatch):
    async def prepare(*_args):
        path = settings.data_dir / "covers/canceled.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unused")
        async with env.sessions() as session:
            job = await session.scalar(select(Job))
            job.state = JobState.canceled
            await session.commit()
        return "covers/canceled.jpg", None
    monkeypatch.setattr(service.probe, "extract_thumbnails", prepare)
    job = await create(env)
    assert (await execute(env, job))["canceled"]
    assert not (settings.data_dir / "covers/canceled.jpg").exists()
    assert (await env.client.get("/api/videos")).json()["total"] == 0
