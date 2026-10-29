"""Player track catalog and saved-setting generation, with real muxed audio."""
import json
import subprocess

from test_dubbing import env as env
from test_dubbing import fake_synthesis, prepared, progress
from test_dubbing_references import upload

from app.api import dubbing as api
from app.models import Job, JobState
from app.services import dubbing
from app.services import dubbing_config as cfg
from app.workers import tasks


async def test_generate_track_from_saved_settings_and_stream_single_replacement_audio(env, monkeypatch):
    client, factory, _, ids, source = env
    before = source.read_bytes()
    config = (await client.get("/api/dubbing/settings")).json()
    config.update(provider="api", target_language="en-US", male_voice="male-test", female_voice="female-test")
    assert (await client.put("/api/dubbing/settings", json=config)).status_code == 200
    monkeypatch.setattr(cfg, "synthesize", fake_synthesis)
    submitted = []
    async def enqueue(kind, payload, session):
        submitted.append((kind, payload))
        job = Job(kind=kind, video_id=payload["video_id"], state=JobState.queued, payload=payload)
        session.add(job)
        await session.commit()
        return {"job_id": job.id}
    monkeypatch.setattr(api, "enqueue_unique", enqueue)
    url = "/api/videos/" + str(ids["video"]) + "/dubbing"
    response = await client.post(url + "/generate", json={"track_id": ids["track"]})
    assert response.status_code == 202, response.text
    kind, payload = submitted[0]
    assert kind == "dub_generate" and payload["target_language"] == "en-US"
    assert payload["keep_original"] is False and "api_key" not in payload
    assert (await client.get(url + "/tracks")).json() == []
    result = await tasks.run_job({}, response.json()["job_id"], kind, payload)
    tracks = (await client.get(url + "/tracks")).json()
    assert len(tracks) == 1 and tracks[0]["language"] == "en-US"
    assert tracks[0]["id"] == response.json()["job_id"]
    assert "source_fingerprint" not in tracks[0]
    path = dubbing.output_path(result["output_key"])
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
    assert len([s for s in probe["streams"] if s["codec_type"] == "audio"]) == 1
    assert abs(float(probe["format"]["duration"]) - 4) < 0.1
    streamed = await client.get(tracks[0]["stream_url"], headers={"Range": "bytes=0-99"})
    assert streamed.status_code == 206 and len(streamed.content) == 100
    assert source.read_bytes() == before
    async with factory() as session:
        job = await session.get(Job, response.json()["job_id"])
        assert job.state == JobState.done and job.progress == 1
    source.write_bytes(before + b"changed")
    assert (await client.get(url + "/tracks")).json() == []


async def test_existing_exports_appear_but_missing_outputs_and_other_videos_do_not(env, monkeypatch):
    client, factory, _, ids, _ = env
    config = (await client.get("/api/dubbing/settings")).json()
    config["provider"] = "api"
    await client.put("/api/dubbing/settings", json=config)
    monkeypatch.setattr(cfg, "synthesize", fake_synthesis)
    project = await prepared(env)
    result = await dubbing.render({"project_id": project["project_id"], "version": 1}, progress)
    legacy = {k: v for k, v in result.items() if k not in {"source_id", "source_fingerprint"}}
    async with factory() as session:
        job = Job(kind="dub_render", video_id=ids["video"], state=JobState.done, payload={}, result=legacy)
        session.add(job)
        session.add(Job(kind="dub_render", video_id=ids["video"], state=JobState.error, payload={}, result=result))
        session.add(Job(kind="dub_render", video_id=ids["video"], state=JobState.done, payload={},
                        result={**result, "output_key": "0" * 32}))
        await session.commit()
        job_id = job.id
    url = "/api/videos/" + str(ids["video"]) + "/dubbing/tracks"
    response = await client.get(url)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [job_id]
    dubbing.output_path(result["output_key"]).unlink()
    assert (await client.get(url)).json() == []


async def test_quick_task_blocks_reference_deletion_and_rejects_invalid_source_subtitle(env):
    client, factory, _, ids, _ = env
    reference = (await upload(client)).json()
    async with factory() as session:
        session.add(Job(kind="dub_generate", video_id=ids["video"], state=JobState.running, payload={}))
        await session.commit()
    assert (await client.delete("/api/dubbing/references/" + reference["id"])).status_code == 409
    response = await client.post("/api/videos/" + str(ids["video"]) + "/dubbing/generate", json={"track_id": 9999})
    assert response.status_code == 422
