"""Isolated database and mocked AI providers; real ffmpeg audio extraction."""

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
async def test_translation_snapshot_and_timing(env, monkeypatch, provider):
    client, factory, _ = env
    track = await imported(client)

    async def request(method, url, **kwargs):
        sent = json.loads(kwargs["json"]["messages"][1]["content"])
        assert [i["id"] for i in sent] == [0, 1]
        content = json.dumps(
            {"translations": [{"id": 1, "text": "测试字幕。"}, {"id": 0, "text": "你好世界。"}]}
        )
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
        Path(args[-1]).write_text(json.dumps(["你好世界。", "测试字幕。"], ensure_ascii=False))
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
        await cb(601000, 601000, "done")

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
