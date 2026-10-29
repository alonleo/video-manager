"""Local mode compatibility, batching, integrity and cancellation without model downloads."""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from test_dubbing import env as env
from test_dubbing import prepared, progress
from test_dubbing_references import audio_bytes, upload

from app.models import DubbingProject, Job, JobState
from app.services import dubbing
from app.services import dubbing_config as cfg
from app.services import dubbing_local as local
from app.services import dubbing_local_runner as runner
from app.services import dubbing_references as refs
from app.workers import tasks


async def test_mode_switch_preserves_keys_without_exposing_them(env, monkeypatch):
    client, factory, _, _, _ = env
    body = (await client.get("/api/dubbing/settings")).json()
    body.update(provider="api", male_voice="custom-male", female_voice="custom-female")
    body["api"]["api_key"] = "existing-api-secret"
    body["reference_api"]["api_key"] = "existing-reference-secret"
    saved = (await client.put("/api/dubbing/settings", json=body)).json()
    saved["provider"] = "local"
    response = await client.put("/api/dubbing/settings", json=saved)
    assert response.status_code == 200
    assert "existing-api-secret" not in response.text and "existing-reference-secret" not in response.text
    monkeypatch.setattr(local, "ensure_ready", lambda _: None)
    async with factory() as s:
        current = await cfg.read_config(s)
        assert current.api.api_key == "existing-api-secret"
        assert current.reference_api.api_key == "existing-reference-secret"
        current.api.api_key = current.reference_api.api_key = ""
        assert await cfg.resolve_voices(current, "zh-CN") == {"male": "uncle_fu", "female": "serena"}
        with pytest.raises(ValueError, match="男女声"):
            await cfg.resolve_voices(current, "zh-CN", male="serena")
        with pytest.raises(ValueError, match="本地语音模型支持"):
            await cfg.resolve_voices(current, "ar-SA")
    response = await client.get("/api/dubbing/voices")
    assert len(response.json()) == 9
    switched = (await client.put("/api/dubbing/settings", json={**saved, "provider": "api"})).json()
    assert switched["male_voice"] == "custom-male" and switched["api"]["has_api_key"]


async def test_local_reference_preview_needs_no_api_key_and_uses_local_sample(env, monkeypatch):
    client, _, directory, _, _ = env
    reference = (await upload(client)).json()
    body = (await client.get("/api/dubbing/settings")).json()
    body.update(provider="local", local_model="qwen3-tts-0.6b-base", male_reference=reference["id"])
    assert (await client.put("/api/dubbing/settings", json=body)).status_code == 200
    monkeypatch.setattr(local, "ensure_ready", lambda _: None)
    seen = []
    async def subprocess_job(command, report, **kwargs):
        manifest = json.loads(Path(command[-1]).read_text())
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert manifest["kind"] == "reference"
        item = manifest["items"][0]
        assert item["ref_audio"] == str(refs.directory(reference["id"]) / "audio.wav")
        assert item["ref_text"] == reference["text"]
        assert item["language"] == "Chinese"
        seen.append(item)
        Path(item["output"]).write_bytes(audio_bytes(1, fmt="mp3"))
    monkeypatch.setattr(local.subtitles, "subprocess_job", subprocess_job)
    response = await client.post("/api/dubbing/reference-preview",
        json={"reference_id": reference["id"], "text": "本地参考音色试听"})
    assert response.status_code == 200 and response.headers["content-type"] == "audio/mpeg"
    assert len(seen) == 1
    assert not list((directory / "work").glob("tts-local-*"))


async def test_local_render_batches_both_genders_once_and_cleans_cancellation(env, monkeypatch):
    client, factory, directory, _, source = env
    original = source.read_bytes()
    body = (await client.get("/api/dubbing/settings")).json()
    body.update(provider="local", target_language="zh-CN")
    assert (await client.put("/api/dubbing/settings", json=body)).status_code == 200
    result = await prepared(env)
    async with factory() as s:
        project = await s.get(DubbingProject, result["project_id"])
        project.cues = [{**c, "gender": "male" if i == 0 else "female"} for i, c in enumerate(project.cues)]
        await s.commit()
    monkeypatch.setattr(local, "ensure_ready", lambda _: None)
    batches = []
    async def batch(config, items, report):
        batches.append(items)
        for item in items:
            Path(item["output"]).write_bytes(audio_bytes(1, fmt="mp3"))
    monkeypatch.setattr(local, "synthesize_many", batch)
    payload = {"project_id": result["project_id"], "version": 1, "keep_original": True}
    output = await dubbing.render(payload, progress)
    assert len(batches) == 1
    assert [i["voice"] for i in batches[0]] == ["uncle_fu", "serena"]
    assert all(i["language"] == "en-US" for i in batches[0])
    assert dubbing.output_path(output["output_key"]).is_file()
    assert source.read_bytes() == original
    before = set((directory / "data/dubbing/outputs").iterdir())
    async def canceled(*_):
        raise tasks.JobCanceled()
    monkeypatch.setattr(local, "synthesize_many", canceled)
    with pytest.raises(tasks.JobCanceled):
        await dubbing.render(payload, progress)
    assert set((directory / "data/dubbing/outputs").iterdir()) == before


def test_model_readiness_rejects_partial_or_wrong_revision(tmp_path, monkeypatch):
    model = "qwen3-tts-0.6b-custom"
    monkeypatch.setattr(local, "model_dir", lambda _: tmp_path)
    monkeypatch.setitem(local.MODELS, model, {"revision": "pinned", "files": {"weights": (4, None)}})
    (tmp_path / ".ready").write_text("pinned")
    (tmp_path / "weights").write_bytes(b"bad")
    assert not local.model_ready(model)
    (tmp_path / "weights").write_bytes(b"good")
    assert local.model_ready(model)
    (tmp_path / ".ready").write_text("stale")
    assert not local.model_ready(model)


def test_download_resumes_and_checks_hash_before_install(tmp_path, monkeypatch):
    data = b"verified model fixture"
    (tmp_path / "weights.partial").write_bytes(data[:5])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"root": str(tmp_path), "repository": "official/model",
        "revision": "pinned", "files": {"weights": [len(data), hashlib.sha256(data).hexdigest()]}}))
    original = httpx.Client
    requests = []
    def handle(request):
        requests.append(request)
        assert request.headers["range"] == "bytes=5-"
        return httpx.Response(206, headers={"content-range": "bytes 5-21/22"}, content=data[5:])
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(handle)))
    runner.download(manifest)
    assert (tmp_path / "weights").read_bytes() == data
    assert not (tmp_path / "weights.partial").exists()
    assert not (tmp_path / ".ready").exists()
    runner.download(manifest)
    assert len(requests) == 1


async def test_cancel_local_subprocess_removes_manifest(env, monkeypatch):
    _, _, directory, _, _ = env
    monkeypatch.setattr(local, "ensure_ready", lambda _: None)
    started = asyncio.Event()
    async def wait(*_, **__):
        started.set()
        await asyncio.Future()
    monkeypatch.setattr(local.subtitles, "subprocess_job", wait)
    task = asyncio.create_task(local.synthesize(cfg.DubbingConfig(provider="local"), "你好", "serena", directory / "out.mp3"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list((directory / "work").glob("tts-local-*"))

async def test_model_job_status_exposes_model_id_without_private_payload(env):
    client, factory, _, _, _ = env
    async with factory() as session:
        session.add(Job(kind="dub_model_download", state=JobState.running, progress=0.4,
                        payload={"model": "qwen3-tts-0.6b-custom", "private": "do-not-expose"}))
        session.add(Job(kind="dub_render", state=JobState.running, payload={}))
        await session.commit()
    response = await client.get("/api/dubbing/local-models/jobs")
    assert response.status_code == 200
    assert "do-not-expose" not in response.text
    rows = response.json()["items"]
    assert len(rows) == 1 and rows[0]["model"] == "qwen3-tts-0.6b-custom"
    assert rows[0]["state"] == "running" and rows[0]["progress"] == 0.4
