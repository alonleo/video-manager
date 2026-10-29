"""Reference import ASR jobs, original-language text, and correction races."""

import asyncio
import json
import wave
from pathlib import Path

import pytest
from fastapi import HTTPException
from test_dubbing import env as env
from test_dubbing_references import audio_bytes

from app.api import dubbing as api
from app.models import Job, JobState
from app.services import dubbing_reference_asr as asr
from app.services import dubbing_references as refs
from app.services import subtitle_config as cfg
from app.workers import tasks


def setup(monkeypatch, factory):
    monkeypatch.setattr(asr, "SessionLocal", factory)

    async def enqueue(kind, payload, session):
        job = Job(kind=kind, state=JobState.queued, payload=payload)
        session.add(job)
        await session.commit()
        return {"job_id": job.id}

    monkeypatch.setattr(api, "enqueue_unique", enqueue)


async def upload(client, gender="male", fmt="wav"):
    response = await client.post(
        "/api/dubbing/references",
        data={"name": gender + " sample", "gender": gender},
        files={"file": ("recording." + fmt, audio_bytes(fmt=fmt), "audio/" + fmt)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def run_latest(client, factory, key):
    jobs = (await client.get("/api/dubbing/references/jobs")).json()["items"]
    job = next(j for j in jobs if j["reference_id"] == key)
    async with factory() as session:
        payload = (await session.get(Job, job["id"])).payload
    return await tasks.run_job({}, job["id"], job["kind"], payload)


async def online(client):
    config = (await client.get("/api/subtitle-settings")).json()
    config.update(asr_provider="api")
    config["asr_api"].update(
        base_url="https://asr.example/v1", model="current-asr", api_key="private-asr-key"
    )
    assert (await client.put("/api/subtitle-settings", json=config)).status_code == 200


async def test_import_without_text_separate_voices_current_api_and_correction(env, monkeypatch):
    client, factory, _, _, _ = env
    setup(monkeypatch, factory)
    await online(client)
    male = await upload(client)
    female = await upload(client, "female", "webm")
    assert male["text"] == female["text"] == ""
    assert male["id"] != female["id"]
    jobs_response = await client.get("/api/dubbing/references/jobs")
    assert len(jobs_response.json()["items"]) == 2
    assert "private-asr-key" not in jobs_response.text
    assert "payload" not in jobs_response.text
    assert (
        await client.post("/api/dubbing/references/" + male["id"] + "/transcribe")
    ).status_code == 409
    assert (await client.delete("/api/dubbing/references/" + male["id"])).status_code == 409
    seen = []

    async def request(method, url, **kwargs):
        assert method == "POST" and url.endswith("/audio/transcriptions")
        assert kwargs["headers"]["Authorization"] == "Bearer private-asr-key"
        assert kwargs["data"] == {"model": "current-asr", "response_format": "json"}
        with wave.open(kwargs["files"]["file"][1]) as audio:
            assert (audio.getnchannels(), audio.getframerate()) == (1, 16000)
        seen.append(True)
        return {"text": "这是男声的原文。" if len(seen) == 1 else "This is the female voice."}

    monkeypatch.setattr(cfg, "api_request", request)
    await run_latest(client, factory, male["id"])
    await run_latest(client, factory, female["id"])
    assert refs.get(male["id"]).text == "这是男声的原文。"
    assert refs.get(female["id"]).text == "This is the female voice."
    body = (await client.get("/api/dubbing/settings")).json()
    body.update(male_reference=male["id"], female_reference=female["id"])
    saved = await client.put("/api/dubbing/settings", json=body)
    assert saved.status_code == 200
    response = await client.put(
        "/api/dubbing/references/" + male["id"], json={"text": "校对后的男声原文。", "version": 2}
    )
    assert response.status_code == 200 and response.json()["version"] == 3
    assert refs.get(female["id"]).text == "This is the female voice."
    assert (
        await client.put(
            "/api/dubbing/references/" + male["id"], json={"text": "stale", "version": 2}
        )
    ).status_code == 409


async def test_late_asr_never_overwrites_manual_correction(env, monkeypatch):
    client, factory, _, _, _ = env
    setup(monkeypatch, factory)
    await online(client)
    row = await upload(client)

    async def request(*_, **kwargs):
        async with refs.LOCK:
            refs.write_text(row["id"], "用户在识别期间校对的原文", 1)
        return {"text": "晚到的自动识别结果"}

    monkeypatch.setattr(cfg, "api_request", request)
    result = await run_latest(client, factory, row["id"])
    assert result["applied"] is False
    assert refs.get(row["id"]).text == "用户在识别期间校对的原文"


async def test_local_decoder_inference_progress_and_offline_model(env, monkeypatch):
    client, factory, _, _, _ = env
    setup(monkeypatch, factory)
    row = await upload(client, "female")
    monkeypatch.setattr(cfg, "model_ready", lambda model: model == "small")
    original = asr.subtitles.subprocess_job

    async def child(args, progress, **kwargs):
        if "transcribe" not in args:
            return await original(args, progress, **kwargs)
        assert args[-1] == "auto"
        assert args[-4].endswith("/whisper/small")
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        with wave.open(args[-3]) as audio:
            assert audio.getframerate() == 16000
        await progress(2, 4, "识别中")
        Path(args[-2]).write_text(json.dumps({"cues": [{"text": "本地原文"}]}))

    monkeypatch.setattr(asr.subtitles, "subprocess_job", child)
    result = await run_latest(client, factory, row["id"])
    assert result["applied"] is True
    assert refs.get(row["id"]).text == "本地原文"


async def test_cancellation_reaps_request_and_keeps_audio_for_retry(env, monkeypatch):
    client, factory, work, _, _ = env
    setup(monkeypatch, factory)
    await online(client)
    row = await upload(client)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def request(*_, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(cfg, "api_request", request)
    task = asyncio.create_task(run_latest(client, factory, row["id"]))
    await asyncio.wait_for(started.wait(), 5)
    jobs = (await client.get("/api/dubbing/references/jobs")).json()["items"]
    assert (await client.post("/api/jobs/" + str(jobs[0]["id"]) + "/cancel")).status_code == 200
    result = await asyncio.wait_for(task, 5)
    assert result == {"canceled": True} and stopped.is_set()
    assert refs.get(row["id"]).text == ""
    assert not list((work / "work").glob("reference-asr-*"))
    assert (
        await client.post("/api/dubbing/references/" + row["id"] + "/transcribe")
    ).status_code == 202


@pytest.mark.parametrize(
    "response", [{"text": ""}, {}, {"text": "x" * 2001}], ids=["no-speech", "invalid", "too-long"]
)
async def test_bad_asr_result_preserves_original_audio(env, monkeypatch, response):
    client, factory, _, _, _ = env
    setup(monkeypatch, factory)
    await online(client)
    row = await upload(client)
    before = (refs.directory(row["id"]) / "audio.wav").read_bytes()

    async def request(*_, **kwargs):
        return response

    monkeypatch.setattr(cfg, "api_request", request)
    with pytest.raises(ValueError):
        await run_latest(client, factory, row["id"])
    assert refs.get(row["id"]).text == ""
    assert (refs.directory(row["id"]) / "audio.wav").read_bytes() == before


async def test_missing_model_and_queue_failure_allow_retry_or_manual_text(env, monkeypatch):
    client, factory, _, _, _ = env
    setup(monkeypatch, factory)
    row = await upload(client)
    with pytest.raises(ValueError, match="下载所选识别模型"):
        await run_latest(client, factory, row["id"])

    async def unavailable(*_):
        raise HTTPException(503, "队列不可用")

    monkeypatch.setattr(api, "enqueue_unique", unavailable)
    extra = await upload(client, "female")
    assert extra["transcription_error"] == "队列不可用"
    assert (
        await client.put(
            "/api/dubbing/references/" + extra["id"], json={"text": "手动补充的原文", "version": 1}
        )
    ).status_code == 200
    assert refs.payload(extra["id"])["text"] == "手动补充的原文"
