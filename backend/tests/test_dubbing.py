"""Isolated API/worker tests with real ffmpeg timing, mixing and output validation."""
import asyncio
import json
import subprocess

import httpx
import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import (
    Base,
    DubbingProject,
    Job,
    JobState,
    Storage,
    StorageType,
    SubtitleTrack,
    Video,
    VideoSource,
)
from app.services import dubbing, dubbing_config, subtitles
from app.services.dubbing_audio import estimate
from app.workers import tasks


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "test.db"))
    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def dependency():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    for module in (dubbing, subtitles, tasks):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "work_dir", tmp_path / "work")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    source = tmp_path / "original.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=4",
                    "-f", "lavfi", "-i", "sine=frequency=120:duration=4:sample_rate=16000",
                    "-c:v", "libx264", "-threads", "1", "-c:a", "aac", "-shortest", str(source)], check=True)
    cues = [{"start_ms": 200, "end_ms": 1400, "text": "Hello."},
            {"start_ms": 2200, "end_ms": 3400, "text": "Welcome."}]
    async with factory() as s:
        storage = Storage(name="Fixture", type=StorageType.local, config={"root": str(tmp_path)})
        video = Video(title="Dubbing fixture", media_type="video")
        s.add_all([storage, video]); await s.flush()
        item = VideoSource(video_id=video.id, storage_id=storage.id, source_type=StorageType.local,
                           uri=str(source), local_path=str(source), is_primary=True, available=True)
        track = SubtitleTrack(video_id=video.id, label="English", language="en", engine="import", cues=cues)
        s.add_all([item, track]); await s.commit()
        ids = {"video": video.id, "source": item.id, "track": track.id}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        yield client, factory, tmp_path, ids, source
    app.dependency_overrides.clear()
    await engine.dispose()


async def progress(*_):
    pass


async def prepared(env, mode="auto"):
    _, _, _, ids, _ = env
    return await dubbing.prepare({"video_id": ids["video"], "track_id": ids["track"],
                                 "target_language": "en-US", "voice_mode": mode}, progress)


def test_pitch_estimates_and_silence():
    t = np.arange(16000) / 16000
    assert 110 < estimate((np.sin(2 * np.pi * 120 * t) * 10000).astype("<i2"), 16000) < 130
    assert 225 < estimate((np.sin(2 * np.pi * 240 * t) * 10000).astype("<i2"), 16000) < 250
    assert estimate(np.zeros(16000, dtype="<i2"), 16000) is None


async def test_prepare_edit_validation_and_secret_settings(env):
    client, factory, _, ids, _ = env
    result = await prepared(env)
    response = await client.get("/api/dubbing/projects/" + str(result["project_id"]))
    assert response.status_code == 200, response.text
    project = response.json()
    assert len(project["cues"]) == 2 and all(c["gender"] == "male" for c in project["cues"])
    project["cues"][0]["gender"] = "female"
    project["cues"][0]["text"] = "A corrected sentence."
    response = await client.put("/api/dubbing/projects/" + str(project["id"]),
                                json={"version": 1, "cues": project["cues"]})
    assert response.status_code == 200 and response.json()["version"] == 2
    assert (await client.put("/api/dubbing/projects/" + str(project["id"]),
                             json={"version": 1, "cues": project["cues"]})).status_code == 409
    project["cues"][0]["start_ms"] = 0
    assert (await client.put("/api/dubbing/projects/" + str(project["id"]),
                             json={"version": 2, "cues": project["cues"]})).status_code == 422
    settings_body = (await client.get("/api/dubbing/settings")).json()
    settings_body["provider"] = "api"
    settings_body["api"]["api_key"] = "fixture-private-key"
    response = await client.put("/api/dubbing/settings", json=settings_body)
    assert response.status_code == 200 and "fixture-private-key" not in response.text
    saved = response.json()
    assert saved["api"]["has_api_key"]
    assert (await client.put("/api/dubbing/settings", json=saved)).status_code == 200
    saved["api"]["base_url"] = "https://example.test/v1"
    assert (await client.put("/api/dubbing/settings", json=saved)).status_code == 422
    assert (await client.post("/api/videos/" + str(ids["video"]) + "/dubbing/prepare",
                               json={"target_language": "../../bad"})).status_code == 422
    async with factory() as s:
        config = await dubbing_config.read_config(s)
        assert config.api.api_key == "fixture-private-key"


async def fake_synthesis(config, text, voice, output):
    await asyncio.to_thread(subprocess.run, [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=" + ("120" if voice == "male-test" else "240") + ":duration=1.8",
        "-c:a", "libmp3lame", str(output)], check=True)


async def test_real_mux_voice_assignment_timing_download_and_source_unchanged(env, monkeypatch):
    client, factory, _, ids, source = env
    before = source.read_bytes()
    result = await prepared(env)
    async with factory() as s:
        project = await s.get(DubbingProject, result["project_id"])
        project.cues = [{**c, "gender": "male" if i == 0 else "female"} for i, c in enumerate(project.cues)]
        await s.commit()
    used = []
    async def synthesis(config, text, voice, output):
        used.append(voice)
        await fake_synthesis(config, text, voice, output)
    async def voices(*_):
        return {"male": "male-test", "female": "female-test"}
    monkeypatch.setattr(dubbing_config, "resolve_voices", voices)
    monkeypatch.setattr(dubbing_config, "synthesize", synthesis)
    payload = {"video_id": ids["video"], "project_id": result["project_id"],
               "version": 1, "keep_original": True}
    updates = []
    async def report(done, total, message):
        if done is not None:
            updates.append(done / total)
    output = await dubbing.render(payload, report)
    assert used == ["male-test", "female-test"]
    assert source.read_bytes() == before
    path = dubbing.output_path(output["output_key"])
    raw = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams",
                                              "-show_format", "-of", "json", str(path)]))
    audio = [s for s in raw["streams"] if s["codec_type"] == "audio"]
    assert len(audio) == 2 and audio[0]["disposition"]["default"] == 1
    assert audio[1]["disposition"]["default"] == 0
    assert abs(float(raw["format"]["duration"]) - 4) < 0.1
    assert max(updates) == 0.99 and all(0 <= p <= 1 for p in updates)
    async with factory() as s:
        job = Job(kind="dub_render", video_id=ids["video"], state=JobState.done,
                  payload=payload, result=output)
        s.add(job); await s.commit(); job_id = job.id
    response = await client.get("/api/jobs/" + str(job_id) + "/dubbing-file?download=true")
    assert response.status_code == 200 and response.content == path.read_bytes()
    assert "attachment;" in response.headers["content-disposition"]
    response = await client.get("/api/jobs/" + str(job_id) + "/dubbing-file", headers={"Range": "bytes=0-99"})
    assert response.status_code == 206 and len(response.content) == 100


async def test_cancel_cleanup_stale_version_and_changed_source(env, monkeypatch):
    _, _, directory, ids, source = env
    project = await prepared(env, "female")
    payload = {"project_id": project["project_id"], "version": 2}
    with pytest.raises(ValueError, match="修改"):
        await dubbing.render(payload, progress)
    payload["version"] = 1
    async def voices(*_):
        return {"male": "male-test", "female": "female-test"}
    async def canceled(*_):
        raise tasks.JobCanceled()
    monkeypatch.setattr(dubbing_config, "resolve_voices", voices)
    monkeypatch.setattr(dubbing_config, "synthesize", canceled)
    with pytest.raises(tasks.JobCanceled):
        await dubbing.render(payload, progress)
    assert not list((directory / "data" / "dubbing" / "outputs").glob("*/result.mp4"))
    assert not list((directory / "data" / "dubbing" / "outputs").iterdir())
    source.write_bytes(source.read_bytes() + b"modified")
    with pytest.raises(ValueError, match="原视频已修改"):
        await dubbing.render(payload, progress)


async def test_translation_and_automatic_transcription_are_reused(env, monkeypatch):
    _, factory, _, ids, _ = env
    calls = []
    async def dispatch(kind, payload, report):
        calls.append(kind)
        if kind == "subtitle_generate":
            return {"track_id": ids["track"]}
        async with factory() as s:
            source = await s.get(SubtitleTrack, ids["track"])
            translated = SubtitleTrack(video_id=ids["video"], label="Chinese", language="zh-CN",
                engine="local", cues=[{**c, "text": "译文"} for c in source.cues],
                source_cues=source.cues, source_track_id=source.id)
            s.add(translated); await s.commit()
            return {"track_id": translated.id}
    monkeypatch.setattr(subtitles, "dispatch", dispatch)
    result = await dubbing.prepare({"video_id": ids["video"], "target_language": "zh-CN",
                                    "voice_mode": "male"}, progress)
    assert calls == ["subtitle_generate", "subtitle_translate"]
    async with factory() as s:
        project = await s.get(DubbingProject, result["project_id"])
        assert project.cues[0]["source_text"] == "Hello."
        assert project.cues[0]["text"] == "译文"
