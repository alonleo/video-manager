"""Isolated database and mocked AI providers; real ffmpeg audio extraction."""

import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Base, Job, JobState, Storage, StorageType, Video, VideoSource
from app.services import subtitles
from app.services.subtitle_text import bilingual_cues, export_subtitles, parse_subtitles
from app.workers import tasks

SRT = (
    "1\n00:00:00,100 --> 00:00:01,200\nHello world.\n\n"
    "2\n00:00:01,300 --> 00:00:02,000\nTesting captions.\n"
)


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    monkeypatch.setattr(subtitles, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "work_dir", tmp_path / "work")
    monkeypatch.setattr(settings, "llm_api_key", "")
    async with factory() as session:
        session.add(Video(id=1, title="Isolated test video"))
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        yield client, factory, tmp_path
    app.dependency_overrides.clear()
    await engine.dispose()


async def progress(*args):
    pass


async def imported(client):
    r = await client.post(
        "/api/videos/1/subtitles/import", json={"content": SRT, "label": "English"}
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_timed_text_roundtrip_and_escaping():
    cues = parse_subtitles("\ufeff" + SRT.replace("\n", "\r\n"))
    assert cues[0]["start_ms"] == 100
    assert parse_subtitles(export_subtitles(cues)) == cues
    assert parse_subtitles(export_subtitles(cues, "srt")) == cues
    cues[0]["text"] = "<script>alert(1)</script> & test"
    assert "<script>" not in export_subtitles(cues)
    assert "&lt;script&gt;" in export_subtitles(cues)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "plain words",
        "1\n00:00:02,000 --> 00:00:01,000\nOops",
        "1\n00:61:02,000 --> 00:61:03,000\nOops",
    ],
)
def test_invalid_timing_rejected(content):
    with pytest.raises(ValueError):
        parse_subtitles(content)


def test_bilingual_overlapping_boundaries():
    a = [{"start_ms": 0, "end_ms": 2000, "text": "A"}]
    b = [{"start_ms": 1000, "end_ms": 3000, "text": "B"}]
    assert [c["text"] for c in bilingual_cues(b, a)] == ["A", "A\nB", "B"]


async def test_import_edit_export_delete(env):
    client, _, _ = env
    track = await imported(client)
    root = f"/api/subtitles/{track['id']}"
    assert len((await client.get("/api/videos/1/subtitles")).json()) == 1
    bad = await client.put(root, json={"label": "Changed", "version": 0, "cues": track["cues"]})
    assert bad.status_code == 409
    track["cues"][0]["text"] = "Corrected"
    r = await client.put(root, json={"label": "Changed", "version": 1, "cues": track["cues"]})
    assert r.status_code == 200 and r.json()["version"] == 2
    exported = await client.get(root + "/export?format=srt&download=true")
    assert "Corrected" in exported.text and "attachment;" in exported.headers["content-disposition"]
    assert (await client.delete(root)).status_code == 204
    assert (await client.get(root)).status_code == 404
    assert (
        await client.post("/api/videos/999/subtitles/import", json={"content": SRT})
    ).status_code == 404


async def test_secret_masking_preservation_and_endpoint_change(env):
    client, _, _ = env
    config = (await client.get("/api/subtitle-settings")).json()
    config["asr_api"]["api_key"] = "private-secret-for-test"
    r = await client.put("/api/subtitle-settings", json=config)
    assert r.status_code == 200 and "private-secret" not in r.text
    saved = r.json()
    assert saved["asr_api"]["has_api_key"] is True
    assert (await client.put("/api/subtitle-settings", json=saved)).json()["asr_api"]["has_api_key"]
    saved["asr_api"]["base_url"] = "https://another.invalid/v1"
    assert (await client.put("/api/subtitle-settings", json=saved)).status_code == 422
    saved["asr_api"]["api_key"] = ""
    assert not (await client.put("/api/subtitle-settings", json=saved)).json()["asr_api"][
        "has_api_key"
    ]
    assert (
        await client.put(
            "/api/subtitle-settings", json=saved, headers={"Origin": "https://unrelated.invalid"}
        )
    ).status_code == 403


async def test_unknown_or_missing_local_model(env):
    client, _, _ = env
    for model, code in [("small", 409), ("../../secrets", 422)]:
        r = await client.post(
            "/api/videos/1/subtitles/generate", json={"provider": "local", "model": model}
        )
        assert r.status_code == code


@pytest.mark.parametrize("provider", ["api", "ollama"])
@pytest.mark.parametrize("thinking", [False, True])
async def test_translation_snapshot_and_timing(env, monkeypatch, provider, thinking):
    client, factory, _ = env
    track = await imported(client)

    async def request(method, url, **kwargs):
        sent = json.loads(kwargs["json"]["messages"][1]["content"])
        assert [i["id"] for i in sent] == [0, 1]
        content = json.dumps(
            {"translations": [{"id": 1, "text": "测试字幕。"}, {"id": 0, "text": "你好世界。"}]}
        )
        if thinking:
            content = '<think>Check ordering; example {"translations": []} is not the answer.</think>\n```json\n' + content + '\n```'
        return (
            {"message": {"content": content}}
            if provider == "ollama"
            else {"choices": [{"message": {"content": content}}]}
        )

    monkeypatch.setattr(subtitles, "api_request", request)
    result = await subtitles.translate(
        {
            "track_id": track["id"],
            "provider": provider,
            "model": "test-model",
            "target_language": "zh-CN",
            "source_version": 1,
        },
        progress,
    )
    translated = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert translated["cues"][0] == {**track["cues"][0], "text": "你好世界。"}
    assert translated["bilingual"]
    await client.delete(f"/api/subtitles/{track['id']}")
    exported = await client.get(f"/api/subtitles/{result['track_id']}/export?bilingual=true")
    assert "Hello world." in exported.text and "你好世界。" in exported.text


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        '<think>{"translations":[{"id":0,"text":"x"},{"id":1,"text":"y"}]}',
        '<think>Reasoning only.</think>',
        '<think>Reasoning</think>{"translations":[{"id":0,"text":""},{"id":1,"text":"y"}]}',
        '{"translations":[{"id":0,"text":"x"},{"id":0,"text":"y"}]}',
        '{"translations":[{"id":0,"text":""},{"id":1,"text":"y"}]}',
    ],
)
def test_malformed_translation_never_changes_timing(raw):
    with pytest.raises((ValueError, KeyError)):
        subtitles.parse_translation(raw, parse_subtitles(SRT))


async def test_real_extraction_api_asr_and_cleanup(env, monkeypatch):
    client, factory, root = env
    media = root / "voice.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(media),
        ],
        check=True,
        capture_output=True,
    )
    async with factory() as s:
        storage = Storage(name="test", type=StorageType.local, config={})
        s.add(storage)
        await s.flush()
        s.add(
            VideoSource(
                video_id=1,
                storage_id=storage.id,
                source_type=StorageType.local,
                local_path=str(media),
                uri=str(media),
                available=True,
            )
        )
        await s.commit()

    async def request(method, url, **kwargs):
        assert url.endswith("/audio/transcriptions")
        assert kwargs["data"]["response_format"] == "verbose_json"
        assert kwargs["files"]["file"][1].read(4) == b"RIFF"
        return {"language": "en", "segments": [{"start": 0.1, "end": 1.5, "text": "Test audio"}]}

    monkeypatch.setattr(subtitles, "api_request", request)
    result = await subtitles.generate(
        {"video_id": 1, "provider": "api", "model": "test-whisper", "language": "auto"}, progress
    )
    track = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert track["cues"] == [{"start_ms": 100, "end_ms": 1500, "text": "Test audio"}]
    assert not list(settings.work_dir.iterdir())
    assert media.exists()


async def test_canceled_job_never_executes(env, monkeypatch):
    _, factory, _ = env
    async with factory() as s:
        job = Job(kind="subtitle_generate", state=JobState.canceled, payload={})
        s.add(job)
        await s.commit()
        job_id = job.id

    async def forbidden(*args):
        pytest.fail("Canceled task executed")

    monkeypatch.setattr(tasks, "_dispatch", forbidden)
    assert (await tasks.run_job({}, job_id, "subtitle_generate", {}))["canceled"]


async def test_cancel_reaps_child_process(env):
    _, _, root = env
    marker = root / "pid"

    async def cancel(*args):
        raise tasks.JobCanceled()

    with pytest.raises(tasks.JobCanceled):
        await subtitles.subprocess_job(
            [
                sys.executable,
                "-c",
                f'import os,time;open({str(marker)!r},"w").write(str(os.getpid()));time.sleep(30)',
            ],
            cancel,
        )
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_job_states_and_monotonic_completion(env, monkeypatch):
    _, factory, _ = env
    async with factory() as s:
        job = Job(kind="subtitle_translate", state=JobState.queued, payload={})
        s.add(job)
        await s.commit()
        job_id = job.id

    async def fake(kind, payload, cb):
        await cb(1, 2, "half")
        await cb(None, None, "")
        return {"cue_count": 2}

    monkeypatch.setattr(tasks, "_dispatch", fake)
    await tasks.run_job({}, job_id, "subtitle_translate", {})
    async with factory() as s:
        job = await s.get(Job, job_id)
        assert job.state == JobState.done and job.progress == 1


async def test_local_translation_requires_known_language_and_model(env):
    client, _, _ = env
    track = await imported(client)
    url = f"/api/subtitles/{track['id']}/translate"
    body = {"provider": "local", "model": "nllb-600m-int8", "target_language": "zh-CN"}
    assert (await client.post(url, json=body)).status_code == 422  # und is not guessed
    body["source_language"] = "en"
    assert (await client.post(url, json=body)).status_code == 409  # no model in fixture
    body["model"] = "../../anything"
    assert (await client.post(url, json=body)).status_code == 422
    body["model"] = "nllb-600m-int8"
    body["target_language"] = "unknown"
    assert (await client.post(url, json=body)).status_code == 422


async def test_local_translation_preserves_timing_and_snapshot(env, monkeypatch):
    from pathlib import Path

    from app.services import subtitle_translation_local as local

    client, _, _ = env
    track = await imported(client)
    monkeypatch.setattr(local, "runtime_ready", lambda: True)
    monkeypatch.setattr(local, "model_ready", lambda model: True)

    async def child(args, progress, **kwargs):
        data = json.loads(Path(args[-2]).read_text())
        assert data == {
            "texts": [c["text"] for c in track["cues"]],
            "source": "eng_Latn",
            "target": "zho_Hans",
        }
        output = Path(args[-1])
        output.with_suffix(".partial.json").write_text(json.dumps({"indices": [0], "texts": ["你好世界。"]}))
        await progress(1, 2, "partial")
        items = (await client.get("/api/videos/1/subtitles")).json()
        partial = next(item for item in items if item["id"] != track["id"])
        assert partial["partial"] and partial["cue_count"] == 1
        output.write_text(json.dumps(["你好世界。", "测试字幕。"], ensure_ascii=False))
        await progress(2, 2, "done")

    async def forbidden(*args, **kwargs):
        pytest.fail("Local translation must not call an AI API")

    monkeypatch.setattr(subtitles, "subprocess_job", child)
    monkeypatch.setattr(subtitles, "api_request", forbidden)
    result = await subtitles.translate(
        {
            "track_id": track["id"],
            "provider": "local",
            "model": local.MODEL_ID,
            "target_language": "zh-CN",
            "source_language": "en",
            "source_version": 1,
        },
        progress,
    )
    translated = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert translated["engine"] == "local"
    assert translated["model"] == local.MODEL_ID
    assert translated["cues"] == [
        {**track["cues"][0], "text": "你好世界。"},
        {**track["cues"][1], "text": "测试字幕。"},
    ]
    assert not list(settings.work_dir.iterdir())
    await client.delete(f"/api/subtitles/{track['id']}")
    exported = await client.get(f"/api/subtitles/{result['track_id']}/export?bilingual=true")
    assert "Hello world." in exported.text and "你好世界。" in exported.text


def test_long_cues_reach_model_without_truncation(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app.services import subtitle_translation_local as local

    class Tokenizer:
        def encode(self, text, out_type):
            return ["▁" + w for w in text.split()]

        def decode(self, tokens):
            return " ".join(w.removeprefix("▁") for w in tokens)

    class Translator:
        def translate_batch(self, batch, target_prefix, **kwargs):
            assert all(len(row) <= 402 for row in batch)
            assert all(row[0] == "eng_Latn" and row[-1] == "</s>" for row in batch)
            return [
                SimpleNamespace(hypotheses=[prefix + row[1:-1]])
                for row, prefix in zip(batch, target_prefix, strict=True)
            ]

    monkeypatch.setattr(local, "load_model", lambda root: (Translator(), Tokenizer()))
    texts = [" ".join(["a"] * 900 + ["END"]), "second cue"]
    assert local.translate_texts(tmp_path, texts, "en", "zh-CN", lambda *args: None) == texts


@pytest.mark.parametrize(
    "value,expected",
    [
        ("en", "eng_Latn"),
        ("English", "eng_Latn"),
        ("zh", "zho_Hans"),
        ("zh-CN", "zho_Hans"),
        ("zh-TW", "zho_Hant"),
        ("ja", "jpn_Jpan"),
        ("ko", "kor_Hang"),
        ("fr", "fra_Latn"),
        ("de", "deu_Latn"),
        ("es", "spa_Latn"),
    ],
)
def test_local_language_aliases(value, expected):
    from app.services.subtitle_translation_local import language_code

    assert language_code(value) == expected


async def test_cc_saved_parameters_and_provider_changes(env, monkeypatch):
    from app.api import subtitles as api

    client, _, _ = env
    captured = []

    async def enqueue(kind, **payload):
        captured.append((kind, payload))
        return 901

    monkeypatch.setattr(api, "enqueue_job", enqueue)
    monkeypatch.setattr(api.config_svc, "model_ready", lambda model: True)
    options_url = "/api/videos/1/subtitle-options"
    defaults = (await client.get(options_url)).json()
    assert defaults["language"] == "auto" and defaults["target_language"] == "zh-CN"
    options = {
        **defaults, "asr_provider": "local", "asr_model": "base", "language": "ja",
        "translation_provider": "api", "translation_model": "custom-translator",
        "source_language": "en", "target_language": "ja",
    }
    assert (await client.put(options_url, json=options)).status_code == 200
    assert (await client.get(options_url)).json() == options
    response = await client.post("/api/videos/1/subtitles/generate-saved")
    assert response.status_code == 202, response.text
    assert captured[-1] == ("subtitle_generate", {
        "video_id": 1, "provider": "local", "model": "base", "language": "ja",
    })
    config = (await client.get("/api/subtitle-settings")).json()
    config["asr_provider"] = "api"
    config["asr_api"]["model"] = "new-api-speech"
    config["translation_provider"] = "api"
    config["translation_api"]["model"] = "global-translator"
    assert (await client.put("/api/subtitle-settings", json=config)).status_code == 200
    # Old local model override must not leak into the newly selected API provider.
    assert (await client.post("/api/videos/1/subtitles/generate-saved")).status_code == 202
    assert captured[-1][1]["model"] == "new-api-speech"
    assert captured[-1][1]["provider"] == "api"
    track = await imported(client)
    response = await client.post(f"/api/subtitles/{track['id']}/translate-saved")
    assert response.status_code == 202, response.text
    assert captured[-1] == ("subtitle_translate", {
        "video_id": 1, "track_id": track["id"], "source_version": 1,
        "provider": "api", "model": "custom-translator",
        "source_language": "en", "target_language": "ja",
    })
    config["translation_provider"] = "ollama"
    config["ollama_model"] = "saved-ollama-model"
    assert (await client.put("/api/subtitle-settings", json=config)).status_code == 200
    assert (await client.post(f"/api/subtitles/{track['id']}/translate-saved")).status_code == 202
    assert captured[-1][1]["model"] == "saved-ollama-model"
    assert captured[-1][1]["target_language"] == "ja"


async def test_cc_saved_parameters_validation_and_missing_model(env, monkeypatch):
    from app.api import subtitles as api

    client, _, _ = env
    url = "/api/videos/1/subtitle-options"
    assert (await client.get("/api/videos/999/subtitle-options")).status_code == 404
    assert (await client.put("/api/videos/999/subtitle-options", json={})).status_code == 404
    for body in [
        {"asr_provider": "local", "asr_model": "../../secret"},
        {"language": "invalid-language"},
        {"asr_model": "base"},
        {"translation_provider": "local", "translation_model": "unknown"},
    ]:
        assert (await client.put(url, json=body)).status_code == 422
    assert (await client.put(url, json={}, headers={"Origin": "https://other.invalid"})).status_code == 403
    monkeypatch.setattr(api.config_svc, "model_ready", lambda model: False)
    response = await client.post("/api/videos/1/subtitles/generate-saved")
    assert response.status_code == 409 and "下载" in response.text
    assert (await client.post("/api/videos/999/subtitles/generate-saved")).status_code == 404
    assert (await client.post("/api/subtitles/999/translate-saved")).status_code == 404



async def test_local_video_job_uses_one_child_and_saves_timing(env, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from app.services import probe as probe_service

    client, factory, root = env
    media = root / "media.mp4"
    media.write_bytes(b"source remains intact")
    async with factory() as session:
        storage = Storage(name="local-speed-test", type=StorageType.local, config={})
        session.add(storage)
        await session.flush()
        session.add(VideoSource(
            video_id=1, storage_id=storage.id, source_type=StorageType.local,
            local_path=str(media), uri=str(media), available=True,
        ))
        await session.commit()

    async def probe(path):
        assert path == str(media)
        return SimpleNamespace(duration_ms=601000, audio_codec="aac")

    calls = []
    async def child(args, cb, **kwargs):
        calls.append(args)
        assert args[3] == "transcribe-video"
        manifest = json.loads(Path(args[-2]).read_text())
        assert manifest["duration_ms"] == 601000 and manifest["language"] == "auto"
        Path(args[-1]).write_text(json.dumps({
            "cues": [{"start_ms": 600100, "end_ms": 600900, "text": "Last section"}],
            "language": "en",
        }))
        await cb(600900, 601000, "partial")
        items = (await client.get("/api/videos/1/subtitles")).json()
        assert len(items) == 1 and items[0]["partial"] and items[0]["cue_count"] == 1

    monkeypatch.setattr(subtitles, "model_ready", lambda model: True)
    monkeypatch.setattr(probe_service, "probe", probe)
    monkeypatch.setattr(subtitles, "subprocess_job", child)
    result = await subtitles.generate(
        {"video_id": 1, "provider": "local", "model": "small", "language": "auto"}, progress
    )
    track = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert len(calls) == 1
    assert track["model"] == "small" and track["language"] == "en"
    assert track["cues"] == [{"start_ms": 600100, "end_ms": 600900, "text": "Last section"}]
    assert media.read_bytes() == b"source remains intact"
    assert not list(settings.work_dir.iterdir())


async def translation_source(client, count):
    content = "\n\n".join(
        f"{i + 1}\n00:{i // 60:02d}:{i % 60:02d},000 --> 00:{i // 60:02d}:{i % 60:02d},900\nsource-{i}"
        for i in range(count)
    )
    response = await client.post("/api/videos/1/subtitles/import", json={"content": content, "label": "English"})
    assert response.status_code == 201
    return response.json()


def api_translation_payload(track):
    return {"track_id": track["id"], "provider": "api", "model": "test-model",
            "target_language": "zh-CN", "source_version": 1}


async def test_api_translation_five_concurrent_batches_of_twenty(env, monkeypatch):
    client, _, _ = env
    track = await translation_source(client, 119)
    active = peak = 0
    sizes, completed = [], []
    five_started = asyncio.Event()

    async def request(method, url, **kwargs):
        nonlocal active, peak
        sent = json.loads(kwargs["json"]["messages"][1]["content"])
        first = int(sent[0]["text"].split("-")[1])
        sizes.append(len(sent))
        active += 1
        peak = max(peak, active)
        if active == 5:
            five_started.set()
        try:
            await asyncio.wait_for(five_started.wait(), 0.3)
            # Complete batches out of order to exercise the final merge.
            if first == 0:
                await asyncio.sleep(0.03)
            completed.append(first)
            return {"choices": [{"message": {"content": json.dumps({"translations": [
                {"id": item["id"], "text": "translated-" + item["text"]} for item in reversed(sent)
            ]})}}]}
        finally:
            active -= 1

    monkeypatch.setattr(subtitles, "api_request", request)
    result = await subtitles.translate(api_translation_payload(track), progress)
    output = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert sizes == [20, 20, 20, 20, 20, 19]
    assert peak == 5 and active == 0
    assert completed[0] != 0
    assert output["cues"] == [{**cue, "text": "translated-" + cue["text"]} for cue in track["cues"]]


@pytest.mark.parametrize("cancel_job", [False, True])
async def test_api_translation_cleans_up_requests_without_partial_track(env, monkeypatch, cancel_job):
    client, _, _ = env
    track = await translation_source(client, 101)
    started = stopped = 0
    five_started = asyncio.Event()

    async def request(method, url, **kwargs):
        nonlocal started, stopped
        sent = json.loads(kwargs["json"]["messages"][1]["content"])
        started += 1
        if started == 5:
            five_started.set()
        try:
            await five_started.wait()
            if not cancel_job and sent[0]["text"] == "source-0":
                raise ValueError("provider failed")
            await asyncio.Future()
        finally:
            stopped += 1

    async def report(*args):
        if cancel_job:
            raise tasks.JobCanceled()

    monkeypatch.setattr(subtitles, "api_request", request)
    with pytest.raises(tasks.JobCanceled if cancel_job else ValueError):
        await asyncio.wait_for(subtitles.translate(api_translation_payload(track), report), 3)
    assert started == stopped
    assert started == 5 if cancel_job else started >= 3
    original = (await client.get(f"/api/subtitles/{track['id']}")).json()
    assert original["cues"] == track["cues"]
    tracks = (await client.get("/api/videos/1/subtitles")).json()
    assert len(tracks) == 1


@pytest.mark.parametrize("variant", ["1.8B", "7B"])
async def test_hy_mt_plain_translation_preserves_cues(env, monkeypatch, variant):
    client, _, _ = env
    track = await imported(client)
    model = f"hf.co/tencent/HY-MT1.5-{variant}-GGUF:Q4_K_M"
    calls = []

    async def request(method, url, **kwargs):
        body = kwargs["json"]
        assert url.endswith("/api/generate")
        assert body["model"] == model and body["raw"] is True
        assert "format" not in body
        prompt = body["prompt"]
        assert "中文" in prompt and "zh-CN" not in prompt
        assert "translations" not in prompt
        if variant == "1.8B":
            assert prompt.startswith("<｜hy_begin▁of▁sentence｜><｜hy_User｜>")
            assert prompt.endswith("<｜hy_Assistant｜>")
        else:
            assert prompt.startswith("<|startoftext|>")
            assert prompt.endswith("<|extra_0|>")
        calls.append(prompt)
        return {"response": "  你好世界。  " if len(calls) == 1 else "测试字幕。", "done_reason": "stop"}

    monkeypatch.setattr(subtitles, "api_request", request)
    payload = {**api_translation_payload(track), "provider": "ollama", "model": model}
    result = await subtitles.translate(payload, progress)
    output = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert len(calls) == 2
    assert output["cues"] == [{**cue, "text": text} for cue, text in zip(track["cues"], ["你好世界。", "测试字幕。"], strict=True)]


@pytest.mark.parametrize("response", [{"response": " "}, {"response": "未完成", "done_reason": "length"}])
async def test_hy_mt_rejects_empty_or_truncated_translation(env, monkeypatch, response):
    client, _, _ = env
    track = await imported(client)
    async def request(*args, **kwargs):
        return response
    monkeypatch.setattr(subtitles, "api_request", request)
    payload = {**api_translation_payload(track), "provider": "ollama", "model": "hf.co/tencent/HY-MT1.5-1.8B-GGUF:Q4_K_M"}
    with pytest.raises(ValueError):
        await subtitles.translate(payload, progress)
    assert len((await client.get("/api/videos/1/subtitles")).json()) == 1


async def test_catalog_includes_official_hy_mt_without_ollama(monkeypatch):
    from app.services import subtitle_config
    real_client = httpx.AsyncClient
    def offline(request):
        raise httpx.ConnectError("offline", request=request)
    monkeypatch.setattr(subtitle_config.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(offline), **kwargs))
    data = await subtitle_config.catalog(subtitle_config.SubtitleConfig())
    models = {item["id"]: item for item in data["ollama"]}
    for variant in ("1.8B", "7B"):
        item = models[f"hf.co/tencent/HY-MT1.5-{variant}-GGUF:Q4_K_M"]
        assert item["size_mb"] > 0 and not item["installed"]
        assert item["source_url"].startswith("https://huggingface.co/tencent/")
    assert data["ollama_error"]


async def test_hy_mt_download_can_be_enqueued(env, monkeypatch):
    from app.api import subtitles as routes
    client, _, _ = env
    model = "hf.co/tencent/HY-MT1.5-1.8B-GGUF:Q4_K_M"
    async def enqueue(kind, payload, session):
        assert kind == "subtitle_model_download"
        assert payload == {"engine": "ollama", "model": model}
        return {"job_id": 123}
    monkeypatch.setattr(routes, "enqueue_unique", enqueue)
    response = await client.post("/api/subtitle-models/download", json={"engine": "ollama", "model": model})
    assert response.status_code == 200 and response.json()["job_id"] == 123


@pytest.mark.parametrize("target, name", [("fr", "French"), ("zh-TW", "繁体中文")])
async def test_hy_mt_uses_full_language_names(target, name):
    from app.services import subtitle_translation_hy as hy
    async def request(*args, **kwargs):
        assert name in kwargs["json"]["prompt"]
        return {"response": "translated"}
    result = await hy.translate_cue("http://localhost:11434", next(iter(hy.MODELS)),
                                    {"text": "source", "start_ms": 0, "end_ms": 900},
                                    "en", target, request)
    assert result[0]["text"] == "translated"


async def test_hy_mt_rejects_unsupported_language_without_request():
    from app.services import subtitle_translation_hy as hy
    async def request(*args, **kwargs):
        pytest.fail("Unsupported target should not reach Ollama")
    with pytest.raises(ValueError, match="目标语言"):
        await hy.translate_cue("http://localhost:11434", next(iter(hy.MODELS)),
                               {"text": "source", "start_ms": 0, "end_ms": 900},
                               "en", "fi", request)


async def test_api_translation_retries_only_malformed_batch(env, monkeypatch):
    client, _, _ = env
    track = await translation_source(client, 41)
    calls = {}
    async def request(method, url, **kwargs):
        rows = json.loads(kwargs["json"]["messages"][1]["content"])
        first = rows[0]["text"]
        calls[first] = calls.get(first, 0) + 1
        if first == "source-20" and calls[first] == 1:
            return {"choices": [{"message": {"content": "invalid response"}}]}
        return {"choices": [{"message": {"content": json.dumps({"translations": [
            {"id": r["id"], "text": "translated-" + r["text"]} for r in rows
        ]})}}]}
    monkeypatch.setattr(subtitles, "api_request", request)
    result = await subtitles.translate(api_translation_payload(track), progress)
    output = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert calls == {"source-0": 1, "source-20": 2, "source-40": 1}
    assert output["cues"] == [{**cue, "text": "translated-" + cue["text"]} for cue in track["cues"]]


async def test_api_translation_splits_repeatedly_incomplete_batch(env, monkeypatch):
    client, _, _ = env
    track = await translation_source(client, 15)
    sizes = []
    async def request(method, url, **kwargs):
        rows = json.loads(kwargs["json"]["messages"][1]["content"])
        sizes.append(len(rows))
        selected = rows[:1] if len(rows) > 8 else rows
        return {"choices": [{"message": {"content": json.dumps({"translations": [
            {"id": r["id"], "text": "translated-" + r["text"]} for r in selected
        ]})}}]}
    monkeypatch.setattr(subtitles, "api_request", request)
    result = await subtitles.translate(api_translation_payload(track), progress)
    output = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert sizes == [15, 15, 7, 8]
    assert output["cues"] == [{**cue, "text": "translated-" + cue["text"]} for cue in track["cues"]]


@pytest.mark.parametrize("engine,model,folder", [
    ("whisper", "small", "whisper"),
    ("translation", "nllb-600m-int8", "translation"),
])
async def test_uninstall_model_only_removes_selected_files(env, engine, model, folder):
    client, _, root = env
    track = await imported(client)
    selected = root / "data" / "models" / folder / model
    selected.mkdir(parents=True)
    (selected / "model.bin").write_bytes(b"model")
    other = selected.parent / "other-model"
    other.mkdir()
    (other / "asset").write_bytes(b"keep")
    response = await client.post("/api/subtitle-models/uninstall", json={"engine": engine, "model": model})
    assert response.status_code == 200
    assert not selected.exists() and (other / "asset").exists()
    assert (await client.get(f"/api/subtitles/{track['id']}")).status_code == 200


async def test_uninstall_blocks_active_ai_jobs(env):
    client, factory, root = env
    selected = root / "data" / "models" / "whisper" / "small"
    selected.mkdir(parents=True)
    async with factory() as session:
        session.add(Job(kind="subtitle_generate", state=JobState.running, payload={"model": "small"}))
        await session.commit()
    response = await client.post("/api/subtitle-models/uninstall", json={"engine": "whisper", "model": "small"})
    assert response.status_code == 409 and selected.exists()


async def test_uninstall_rejects_unknown_model_and_external_symlink(env):
    client, _, root = env
    invalid = await client.post("/api/subtitle-models/uninstall", json={"engine": "whisper", "model": "unknown"})
    assert invalid.status_code == 422
    external = root / "outside"
    external.mkdir()
    (external / "keep").write_text("keep")
    selected = root / "data" / "models" / "whisper" / "small"
    selected.parent.mkdir(parents=True)
    selected.symlink_to(external, target_is_directory=True)
    response = await client.post("/api/subtitle-models/uninstall", json={"engine": "whisper", "model": "small"})
    assert response.status_code == 422 and (external / "keep").exists()


async def test_uninstall_ollama_uses_delete_without_json_response(env, monkeypatch):
    from app.services import subtitle_config
    client, _, _ = env
    real_client = httpx.AsyncClient
    model = "hf.co/tencent/HY-MT1.5-1.8B-GGUF:Q4_K_M"
    def respond(request):
        assert request.method == "DELETE" and request.url.path == "/api/delete"
        assert json.loads(request.content) == {"model": model}
        return httpx.Response(200)
    monkeypatch.setattr(subtitle_config.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    response = await client.post("/api/subtitle-models/uninstall", json={"engine": "ollama", "model": model})
    assert response.status_code == 200


async def test_uninstall_dubbing_model_keeps_other_assets(env):
    client, _, root = env
    selected = root / "data" / "models" / "dubbing" / "qwen3-tts-0.6b-custom"
    selected.mkdir(parents=True)
    (selected / "model.safetensors").write_bytes(b"model")
    response = await client.post("/api/dubbing/local-models/uninstall", json={"model": selected.name})
    assert response.status_code == 200 and not selected.exists()


async def test_translation_partial_track_is_playable_before_task_finishes(env, monkeypatch):
    client, factory, _ = env
    track = await translation_source(client, 31)
    release = asyncio.Event()
    published = asyncio.Event()
    async with factory() as session:
        job = Job(kind="subtitle_translate", video_id=1, state=JobState.running, payload={})
        session.add(job)
        await session.commit()
        job_id = job.id
    async def request(method, url, **kwargs):
        rows = json.loads(kwargs["json"]["messages"][1]["content"])
        if rows[0]["text"] != "source-0":
            await release.wait()
        return {"choices": [{"message": {"content": json.dumps({"translations": [
            {"id": r["id"], "text": "translated-" + r["text"]} for r in rows
        ]})}}]}
    async def report(done, total, message):
        if done and done > 0:
            published.set()
    monkeypatch.setattr(subtitles, "api_request", request)
    task = asyncio.create_task(subtitles.translate({**api_translation_payload(track), "_job_id": job_id}, report))
    try:
        await asyncio.wait_for(published.wait(), 3)
        items = (await client.get("/api/videos/1/subtitles")).json()
        partial = next(item for item in items if item["id"] != track["id"])
        assert partial["partial"] and partial["building"] and partial["cue_count"] == 20
        exported = await client.get(f"/api/subtitles/{partial['id']}/export?bilingual=true")
        assert "translated-source-0" in exported.text and "source-30" not in exported.text
        blocked = await client.delete(f"/api/subtitles/{partial['id']}")
        assert blocked.status_code == 409
        detail = (await client.get(f"/api/subtitles/{partial['id']}")).json()
        edit = await client.put(f"/api/subtitles/{partial['id']}", json={"label": detail["label"], "version": detail["version"], "cues": detail["cues"]})
        assert edit.status_code == 409
        translation = await client.post(f"/api/subtitles/{partial['id']}/translate", json={"provider": "api", "model": "test", "target_language": "en"})
        assert translation.status_code == 409
        async with factory() as session:
            assert (await session.get(Job, job_id)).result["track_id"] == partial["id"]
        release.set()
        result = await task
        final = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
        assert final["id"] == partial["id"] and not final["partial"]
        assert final["cue_count"] == 31 and final["version"] > partial["version"]
        assert len((await client.get("/api/videos/1/subtitles")).json()) == 2
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_generation_publishes_api_chunk_before_next_request(env, monkeypatch):
    from app.services import probe as probe_module
    from types import SimpleNamespace
    client, factory, root = env
    media = root / "video.mp4"
    media.write_bytes(b"fake-media")
    async with factory() as session:
        storage = Storage(name="test", type=StorageType.local, config={})
        session.add(storage)
        await session.flush()
        session.add(VideoSource(video_id=1, storage_id=storage.id, source_type=StorageType.local,
                                local_path=str(media), uri=str(media), available=True))
        await session.commit()
    async def fake_probe(path):
        return SimpleNamespace(duration_ms=1200000, audio_codec="aac")
    async def extraction(args, report, **kwargs):
        import wave
        with wave.open(args[-1], "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 16000)
    calls = 0
    async def request(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            partials = (await client.get("/api/videos/1/subtitles")).json()
            assert len(partials) == 1 and partials[0]["partial"]
            assert partials[0]["cue_count"] == 1
        return {"language": "en", "segments": [{"start": 0, "end": 0.8, "text": "hello"}]}
    monkeypatch.setattr(probe_module, "probe", fake_probe)
    monkeypatch.setattr(subtitles, "subprocess_job", extraction)
    monkeypatch.setattr(subtitles, "api_request", request)
    result = await subtitles.generate({"video_id": 1, "provider": "api", "model": "test", "language": "auto"}, progress)
    items = (await client.get("/api/videos/1/subtitles")).json()
    assert len(items) == 1 and items[0]["id"] == result["track_id"]
    assert not items[0]["partial"] and items[0]["cue_count"] == 2


async def test_cancel_preserves_published_subtitles(env, monkeypatch):
    client, factory, _ = env
    original = await translation_source(client, 31)
    ready = asyncio.Event()
    async with factory() as session:
        job = Job(kind="subtitle_translate", video_id=1, state=JobState.running, payload={})
        session.add(job); await session.commit(); job_id = job.id
    async def request(method, url, **kwargs):
        rows = json.loads(kwargs["json"]["messages"][1]["content"])
        if rows[0]["text"] != "source-0":
            await asyncio.Future()
        return {"choices": [{"message": {"content": json.dumps({"translations": [
            {"id": r["id"], "text": "translated-" + r["text"]} for r in rows
        ]})}}]}
    async def report(done, total, message):
        if done:
            ready.set()
    monkeypatch.setattr(subtitles, "api_request", request)
    task = asyncio.create_task(subtitles.translate({**api_translation_payload(original), "_job_id": job_id}, report))
    try:
        await asyncio.wait_for(ready.wait(), 3)
        response = await client.post(f"/api/jobs/{job_id}/cancel")
        assert response.status_code == 200
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        items = (await client.get("/api/videos/1/subtitles")).json()
        partial = next(item for item in items if item["id"] != original["id"])
        assert partial["partial"] and not partial["building"] and partial["cue_count"] == 20
        exported = await client.get(f"/api/subtitles/{partial['id']}/export")
        assert exported.status_code == 200 and "translated-source-0" in exported.text
        assert (await client.delete(f"/api/subtitles/{partial['id']}")).status_code == 204
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_generate_translation_switch(env, monkeypatch):
    from app.api import subtitles as api
    client, _, _ = env
    captured = []
    async def enqueue(kind, **payload):
        captured.append(payload); return 999
    monkeypatch.setattr(api, 'enqueue_job', enqueue)
    monkeypatch.setattr(api.config_svc, 'model_ready', lambda _: True)
    options = (await client.get('/api/videos/1/subtitle-options')).json()
    assert options['auto_translate'] is False
    config = (await client.get('/api/subtitle-settings')).json()
    config['translation_provider'] = 'api'
    config['translation_api']['model'] = 'translator'
    await client.put('/api/subtitle-settings', json=config)
    options.update(auto_translate=True, target_language='ja')
    await client.put('/api/videos/1/subtitle-options', json=options)
    response = await client.post('/api/videos/1/subtitles/generate-saved')
    assert response.status_code == 202
    assert captured[-1]['translation']['target_language'] == 'ja'
    options['auto_translate'] = False
    await client.put('/api/videos/1/subtitle-options', json=options)
    await client.post('/api/videos/1/subtitles/generate-saved')
    assert 'translation' not in captured[-1]


async def test_pipeline_translates_before_generation_finishes(env, monkeypatch):
    from app.services.subtitle_partial import ProgressiveTrack
    client, factory, _ = env
    ready = asyncio.Event()
    original = [{'start_ms': i * 1000, 'end_ms': i * 1000 + 800, 'text': f'line {i}'} for i in range(17)]
    async with factory() as session:
        job = Job(kind='subtitle_generate', video_id=1, state=JobState.running, payload={})
        session.add(job); await session.commit(); job_id = job.id
    async def generation(payload, report, on_checkpoint=None):
        publisher = ProgressiveTrack(factory, 1, '原文', 'en', 'api', 'asr', job_id=job_id, result_key='source_track_id')
        await publisher.publish(original[:15])
        await on_checkpoint(original[:15], 'en', publisher.track_id)
        await asyncio.wait_for(ready.wait(), 3)
        items = (await client.get('/api/videos/1/subtitles')).json()
        assert len(items) == 2 and all(item['building'] for item in items)
        assert all(item['partial'] for item in items)
        for item in items:
            assert (await client.delete(f"/api/subtitles/{item['id']}")).status_code == 409
        await publisher.publish(original, final=True)
        await on_checkpoint(original, 'en', publisher.track_id)
        return {'track_id': publisher.track_id, 'cue_count': 17}
    async def request(method, url, **kwargs):
        rows = json.loads(kwargs['json']['messages'][1]['content'])
        return {'choices': [{'message': {'content': json.dumps({'translations': [{'id': row['id'], 'text': '译文 ' + row['text']} for row in rows]})}}]}
    async def report(*args):
        items = (await client.get('/api/videos/1/subtitles')).json()
        if any(item['source_track_id'] and item['cue_count'] == 15 for item in items): ready.set()
    monkeypatch.setattr(subtitles, 'generate_source', generation)
    monkeypatch.setattr(subtitles, 'api_request', request)
    result = await subtitles.generate({'video_id': 1, '_job_id': job_id, 'provider': 'api', 'model': 'asr', 'translation': {'provider': 'api', 'model': 'translator', 'target_language': 'zh-CN'}}, report)
    detail = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert not detail['partial'] and len(detail['cues']) == 17
    from app.models import SubtitleTrack
    async with factory() as session:
        assert (await session.get(SubtitleTrack, result['track_id'])).source_cues == original
    assert result['source_track_id'] != result['track_id']


async def test_pipeline_failure_preserves_complete_source_and_partial_translation(env, monkeypatch):
    from app.services.subtitle_partial import ProgressiveTrack
    client, factory, _ = env
    original = [{'start_ms': i * 1000, 'end_ms': i * 1000 + 800, 'text': f'line {i}'} for i in range(16)]
    translated_ready = asyncio.Event()
    async def generation(payload, report, on_checkpoint=None):
        publisher = ProgressiveTrack(factory, 1, '原文', 'en', 'api', 'asr')
        await publisher.publish(original[:15])
        await on_checkpoint(original[:15], 'en', publisher.track_id)
        await asyncio.wait_for(translated_ready.wait(), 3)
        await publisher.publish(original, final=True)
        await on_checkpoint(original, 'en', publisher.track_id)
        return {'track_id': publisher.track_id}
    async def request(method, url, **kwargs):
        rows = json.loads(kwargs['json']['messages'][1]['content'])
        content = 'invalid' if rows[0]['text'] == 'line 15' else json.dumps({'translations': [{'id': row['id'], 'text': '译文 ' + row['text']} for row in rows]})
        return {'choices': [{'message': {'content': content}}]}
    async def report(*args):
        items = (await client.get('/api/videos/1/subtitles')).json()
        if any(item['source_track_id'] and item['cue_count'] == 15 for item in items): translated_ready.set()
    monkeypatch.setattr(subtitles, 'generate_source', generation)
    monkeypatch.setattr(subtitles, 'api_request', request)
    with pytest.raises(ValueError, match='原字幕已生成，自动翻译失败'):
        await subtitles.generate({'video_id': 1, 'provider': 'api', 'model': 'asr', 'translation': {'provider': 'api', 'model': 'translator', 'target_language': 'zh-CN'}}, report)
    items = (await client.get('/api/videos/1/subtitles')).json()
    source = next(item for item in items if not item['source_track_id'])
    translation = next(item for item in items if item['source_track_id'])
    assert source['cue_count'] == 16 and not source['partial']
    assert translation['cue_count'] == 15 and translation['partial']


async def test_pipeline_cancel_stops_recognition_and_translation_workers(env, monkeypatch):
    from app.services.subtitle_partial import ProgressiveTrack
    from app.workers.tasks import JobCanceled
    _, factory, _ = env
    generation_stopped = asyncio.Event()
    async def generation(payload, report, on_checkpoint=None):
        publisher = ProgressiveTrack(factory, 1, '原文', 'en', 'api', 'asr')
        cue = {'start_ms': 0, 'end_ms': 800, 'text': 'hello'}
        await publisher.publish([cue])
        await on_checkpoint([cue], 'en', publisher.track_id)
        try:
            await asyncio.Event().wait()
        finally:
            generation_stopped.set()
    async def translation(*args, **kwargs):
        raise JobCanceled()
    monkeypatch.setattr(subtitles, 'generate_source', generation)
    monkeypatch.setattr(subtitles, 'translate', translation)
    with pytest.raises(JobCanceled):
        await asyncio.wait_for(subtitles.generate({'video_id': 1, 'translation': {'provider': 'api', 'model': 't', 'target_language': 'zh-CN'}}, progress), 3)
    assert generation_stopped.is_set()


async def test_real_api_generation_pipeline_streams_minute_chunks(env, monkeypatch):
    from app.services import probe as probe_module
    from types import SimpleNamespace
    client, factory, root = env
    media = root / 'video.mp4'; media.write_bytes(b'media')
    parameters = {'provider': 'api', 'model': 'translator', 'target_language': 'zh-CN'}
    async with factory() as session:
        storage = Storage(name='test', type=StorageType.local, config={})
        session.add(storage); await session.flush()
        session.add(VideoSource(video_id=1, storage_id=storage.id, source_type=StorageType.local, local_path=str(media), uri=str(media), available=True))
        job = Job(kind='subtitle_generate', video_id=1, state=JobState.running, payload={'translation': parameters})
        session.add(job); await session.commit(); job_id = job.id
    async def probe(path): return SimpleNamespace(duration_ms=120000, audio_codec='aac')
    async def extraction(args, report, **kwargs):
        import wave
        assert args[args.index('-t') + 1] == '60.0'
        with wave.open(args[-1], 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(b'\0\0' * 16000 * 60)
    translated_ready = asyncio.Event()
    asr_calls = 0
    async def request(method, url, **kwargs):
        nonlocal asr_calls
        if 'files' in kwargs:
            asr_calls += 1
            if asr_calls == 2:
                await asyncio.wait_for(translated_ready.wait(), 3)
                items = (await client.get('/api/videos/1/subtitles')).json()
                assert len(items) == 2 and all(item['building'] for item in items)
                statuses = (await client.get('/api/subtitle-status?video_ids=1')).json()['items'][0]
                assert statuses['generation']['state'] == statuses['translation']['state'] == 'running'
            return {'language': 'en', 'segments': [{'start': 0, 'end': .8, 'text': 'hello'}]}
        rows = json.loads(kwargs['json']['messages'][1]['content'])
        return {'choices': [{'message': {'content': json.dumps({'translations': [{'id': row['id'], 'text': '你好'} for row in rows]})}}]}
    async def report(*args):
        items = (await client.get('/api/videos/1/subtitles')).json()
        if any(item['source_track_id'] for item in items): translated_ready.set()
    monkeypatch.setattr(probe_module, 'probe', probe)
    monkeypatch.setattr(subtitles, 'subprocess_job', extraction)
    monkeypatch.setattr(subtitles, 'api_request', request)
    result = await subtitles.generate({'video_id': 1, '_job_id': job_id, 'provider': 'api', 'model': 'asr', 'translation': parameters}, report)
    async with factory() as session:
        job = await session.get(Job, job_id); job.state = JobState.done; job.result = result
        await session.commit()
    statuses = (await client.get('/api/subtitle-status?video_ids=1')).json()['items'][0]
    assert statuses['generation']['state'] == statuses['translation']['state'] == 'done'
    detail = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert [cue['start_ms'] for cue in detail['cues']] == [0, 60000]
    assert not detail['partial'] and not detail['building']


async def test_pipeline_api_limits_five_concurrent_twenty_cue_batches(env, monkeypatch):
    from app.services.subtitle_partial import ProgressiveTrack
    client, factory, _ = env
    original = [{'start_ms': i * 1000, 'end_ms': i * 1000 + 800, 'text': f'line {i}'} for i in range(120)]
    started = asyncio.Event()
    active = maximum = 0
    async def generation(payload, report, on_checkpoint=None):
        publisher = ProgressiveTrack(factory, 1, '原文', 'en', 'api', 'asr')
        await publisher.publish(original)
        await on_checkpoint(original, 'en', publisher.track_id)
        await asyncio.wait_for(started.wait(), 3)
        await publisher.publish(original, final=True)
        return {'track_id': publisher.track_id}
    async def request(method, url, **kwargs):
        nonlocal active, maximum
        rows = json.loads(kwargs['json']['messages'][1]['content'])
        assert len(rows) == 20
        active += 1; maximum = max(maximum, active)
        if active == 5: started.set()
        try:
            await asyncio.wait_for(started.wait(), 3)
            if rows[0]['text'] == 'line 0':
                await asyncio.sleep(.03)
            return {'choices': [{'message': {'content': json.dumps({'translations': [{'id': row['id'], 'text': '译文 ' + row['text']} for row in reversed(rows)]})}}]}
        finally: active -= 1
    monkeypatch.setattr(subtitles, 'generate_source', generation)
    monkeypatch.setattr(subtitles, 'api_request', request)
    result = await subtitles.generate({'video_id': 1, 'translation': {'provider': 'api', 'model': 't', 'target_language': 'zh-CN'}}, progress)
    assert result['cue_count'] == 120 and maximum == 5 and active == 0


    detail = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert detail['cues'] == [{**cue, 'text': '译文 ' + cue['text']} for cue in original]
    from app.models import SubtitleTrack
    async with factory() as session:
        assert (await session.get(SubtitleTrack, result['track_id'])).source_cues == original

async def test_pipeline_local_translation_uses_detected_source_language(env, monkeypatch):
    from app.services.subtitle_partial import ProgressiveTrack
    client, factory, _ = env
    original = [{'start_ms': 0, 'end_ms': 800, 'text': 'hello'}]
    async def generation(payload, report, on_checkpoint=None):
        publisher = ProgressiveTrack(factory, 1, '原文', 'en', 'local', 'small')
        await publisher.publish(original, final=True)
        await on_checkpoint(original, 'en', publisher.track_id)
        return {'track_id': publisher.track_id}
    async def translate_local(cues, source_language, target, model, report, publisher=None):
        assert source_language == 'en' and target == 'ja'
        return [{**cue, 'text': 'こんにちは'} for cue in cues]
    monkeypatch.setattr(subtitles, 'generate_source', generation)
    monkeypatch.setattr(subtitles, 'translate_local', translate_local)
    result = await subtitles.generate({'video_id': 1, 'translation': {'provider': 'local', 'model': 'nllb-600m-int8', 'target_language': 'ja'}}, progress)
    detail = (await client.get(f"/api/subtitles/{result['track_id']}")).json()
    assert detail['cues'][0]['text'] == 'こんにちは' and not detail['partial']
