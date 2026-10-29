"""Reference clips use real audio decoding and a mocked speech service."""
import base64
import io
import json
import subprocess
import wave

import httpx
import pytest
from test_dubbing import env as env
from test_dubbing import prepared, progress

from app.models import DubbingProject, Job, JobState
from app.services import dubbing
from app.services import dubbing_config as cfg
from app.services import dubbing_references as refs


def audio_bytes(duration=4, frequency=120, fmt="wav"):
    codec = {"wav": "pcm_s16le", "webm": "libopus", "mp3": "libmp3lame"}[fmt]
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        f"sine=frequency={frequency}:duration={duration}", "-ac", "1",
        "-c:a", codec, "-f", fmt, "pipe:1",
    ])


async def upload(client, gender="male", data=None, name="参考声音"):
    return await client.post("/api/dubbing/references",
        data={"name": name, "gender": gender, "text": "这是参考音频的原文。"},
        files={"file": ("../../unsafe.wav", data if data is not None else audio_bytes(), "audio/wav")})


async def reference_config(client, male="", female=""):
    body = (await client.get("/api/dubbing/settings")).json()
    body.update(provider="reference", male_reference=male, female_reference=female)
    body["reference_api"].update(base_url="https://voice.example/v1", api_key="reference-secret")
    response = await client.put("/api/dubbing/settings", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def test_import_decode_playback_gender_validation_and_delete(env):
    client, _, _, _, _ = env
    response = await upload(client, data=audio_bytes(fmt="webm"), name="网页录音")
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["gender"] == "male" and 3900 <= row["duration_ms"] <= 4100
    assert len(row["id"]) == 32
    assert not list(refs.root().glob(".import-*"))
    assert (await client.get("/api/dubbing/references")).json() == [row]
    response = await client.get("/api/dubbing/references/" + row["id"] + "/audio")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    with wave.open(io.BytesIO(response.content)) as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == 32000
    response = await client.get("/api/dubbing/references/" + row["id"] + "/audio",
                                headers={"Range": "bytes=0-99"})
    assert response.status_code == 206 and len(response.content) == 100
    config = await reference_config(client, male=row["id"])
    assert "reference-secret" not in json.dumps(config)
    assert config["reference_api"]["has_api_key"]
    bad = {**config, "female_reference": row["id"]}
    assert (await client.put("/api/dubbing/settings", json=bad)).status_code == 422
    assert (await client.delete("/api/dubbing/references/" + row["id"])).status_code == 409
    config["male_reference"] = ""
    assert (await client.put("/api/dubbing/settings", json=config)).status_code == 200
    assert (await client.delete("/api/dubbing/references/" + row["id"])).status_code == 204
    assert (await client.get("/api/dubbing/references")).json() == []
    assert (await client.get("/api/dubbing/references/" + row["id"] + "/audio")).status_code == 404
    with pytest.raises(ValueError):
        refs.directory("../outside")


@pytest.mark.parametrize("data", [
    b"not an audio file", b"", audio_bytes(1), audio_bytes(31), audio_bytes(4, 0),
    b"#EXTM3U\nfile:///etc/passwd\n",
], ids=["invalid", "empty", "too-short", "too-long", "silent", "playlist"])
async def test_reject_invalid_short_long_silent_or_playlist_audio_without_retaining_files(env, data):
    client, _, _, _, _ = env
    response = await upload(client, data=data)
    assert response.status_code == 422, response.text
    assert refs.list_references() == []
    assert not list(refs.root().glob(".import-*"))


async def test_reference_payload_preview_actual_render_and_only_required_gender(env, monkeypatch):
    client, factory, directory, ids, source = env
    original = source.read_bytes()
    male = (await upload(client)).json()
    female = (await upload(client, "female", audio_bytes(frequency=240), "女声样本")).json()
    config = await reference_config(client, male["id"], female["id"])
    seen = []
    mp3 = audio_bytes(1, fmt="mp3")
    original_client = httpx.AsyncClient

    def handler(request):
        assert str(request.url) == "https://voice.example/v1/audio/speech"
        assert request.headers["authorization"] == "Bearer reference-secret"
        body = json.loads(request.content)
        assert "voice" not in body and body["model"] == "FunAudioLLM/CosyVoice2-0.5B"
        assert body["response_format"] == "mp3"
        reference = body["references"][0]
        assert reference["text"] == "这是参考音频的原文。"
        assert reference["audio"].startswith("data:audio/wav;base64,")
        seen.append(base64.b64decode(reference["audio"].split(",", 1)[1]))
        return httpx.Response(200, content=mp3)

    def speech_client(**kwargs):
        return original_client(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cfg.httpx, "AsyncClient", speech_client)
    response = await client.post("/api/dubbing/reference-preview",
        json={"reference_id": female["id"], "text": "这是一句合成试听。"})
    assert response.status_code == 200 and response.content == mp3, response.text
    assert seen[-1] == (refs.directory(female["id"]) / "audio.wav").read_bytes()

    result = await prepared(env)
    async with factory() as session:
        project = await session.get(DubbingProject, result["project_id"])
        project.cues = [{**c, "gender": "male" if i == 0 else "female"}
                        for i, c in enumerate(project.cues)]
        await session.commit()
    seen.clear()
    result = await dubbing.render({"video_id": ids["video"], "project_id": result["project_id"],
                                   "version": 1, "keep_original": True}, progress)
    assert seen == [(refs.directory(r["id"]) / "audio.wav").read_bytes() for r in (male, female)]
    assert dubbing.output_path(result["output_key"]).is_file() and source.read_bytes() == original
    async with factory() as session:
        current = await cfg.read_config(session)
        current.female_reference = ""
        assert await cfg.resolve_voices(current, "en-US", genders={"male"}) == {"male": male["id"]}
        with pytest.raises(ValueError, match="女声"):
            await cfg.resolve_voices(current, "en-US")
        with pytest.raises(ValueError, match="中文"):
            await cfg.resolve_voices(current, "fr-FR", genders={"male"})
        current.reference_api.api_key = ""
        with pytest.raises(ValueError, match="API Key"):
            await cfg.resolve_voices(current, "en-US", genders={"male"})

    # Secret retention must not forward the old provider's key to a different host.
    config["reference_api"]["base_url"] = "https://other.example/v1"
    assert (await client.put("/api/dubbing/settings", json=config)).status_code == 422
    async with factory() as session:
        session.add(Job(kind="dub_render", state=JobState.running, video_id=ids["video"], payload={}))
        await session.commit()
    extra = (await upload(client, name="未绑定样本")).json()
    assert (await client.delete("/api/dubbing/references/" + extra["id"])).status_code == 409


async def test_oversize_upload_and_unconfigured_preview_fail_locally(env):
    client, _, _, _, _ = env
    response = await client.post("/api/dubbing/references", content=b"x",
                                 headers={"Content-Length": str(refs.MAX_UPLOAD + 20000)})
    assert response.status_code == 413
    row = (await upload(client)).json()
    response = await client.post("/api/dubbing/reference-preview",
        json={"reference_id": row["id"], "text": "你好"})
    assert response.status_code == 422
    assert "参考音色" in response.json()["detail"]
