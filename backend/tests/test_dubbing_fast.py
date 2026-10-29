"""Codec-preserving audio replacement and checkpointed local speech."""

import json
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_dubbing import env as env
from test_dubbing import fake_synthesis, prepared, progress
from test_dubbing_references import audio_bytes, upload

from app.models import Job, JobState, VideoSource
from app.services import dubbing
from app.services import dubbing_config as cfg
from app.services import dubbing_local as local
from app.services import dubbing_local_runner as runner
from app.services import dubbing_references as refs
from app.services import dubbing_speech_cache as cache
from app.workers.tasks import JobCanceled


def video_packets(path):
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_packets",
                "-show_data_hash",
                "sha256",
                "-show_entries",
                "packet=data_hash",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    return [p["data_hash"] for p in info["packets"]]


def decoded_frames(path):
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-an",
            "-f",
            "framemd5",
            "-",
        ]
    ).decode()
    return [
        line.rsplit(",", 1)[1].strip()
        for line in raw.splitlines()
        if line and not line.startswith("#")
    ]


@pytest.mark.parametrize("container", ["mp4", "mkv", "ts"])
async def test_real_replacement_preserves_picture_codec_packets_and_playback_metadata(
    env, monkeypatch, container
):
    client, factory, _, ids, source = env
    if container != "mp4":
        changed = source.with_suffix("." + container)
        if container == "mkv":
            args = [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-c:v",
                "libvpx-vp9",
                "-deadline",
                "realtime",
                "-cpu-used",
                "8",
                "-threads",
                "2",
                "-c:a",
                "copy",
                str(changed),
            ]
        else:
            args = ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-c", "copy", str(changed)]
        subprocess.run(args, check=True)
        async with factory() as session:
            row = await session.get(VideoSource, ids["source"])
            row.local_path = row.uri = str(changed)
            await session.commit()
        source = changed
    before = source.read_bytes()
    project = await prepared(env)

    async def voices(*_):
        return {"male": "male-test", "female": "female-test"}

    monkeypatch.setattr(cfg, "resolve_voices", voices)
    monkeypatch.setattr(cfg, "synthesize", fake_synthesis)
    seen = []
    original_mux = dubbing.run_mux

    async def mux(args, report, duration):
        seen.append(args)
        assert args[args.index("-c:v") + 1] == "copy"
        assert "-vf" not in args and "libx264" not in args
        return await original_mux(args, report, duration)

    monkeypatch.setattr(dubbing, "run_mux", mux)
    result = await dubbing.render(
        {
            "project_id": project["project_id"],
            "version": 1,
            "keep_original": True,
        },
        progress,
    )
    extension = "mkv" if container == "mkv" else "mp4"
    output = dubbing.output_path(result["output_key"], result["source_extension"])
    assert result["source_extension"] == extension
    assert result["video_copied"] is True
    assert result["video_codec"] == ("vp9" if container == "mkv" else "h264")
    assert source.read_bytes() == before
    assert decoded_frames(source) == decoded_frames(output)
    if container != "ts":  # TS remux changes Annex B framing, not decoded picture.
        assert video_packets(source) == video_packets(output)
    async with factory() as session:
        job = Job(kind="dub_render", video_id=ids["video"], state=JobState.done, result=result)
        session.add(job)
        await session.commit()
        job_id = job.id
    response = await client.get(
        "/api/jobs/" + str(job_id) + "/dubbing-file", headers={"Range": "bytes=0-99"}
    )
    assert response.status_code == 206 and len(response.content) == 100
    assert response.headers["content-type"] == (
        "video/x-matroska" if extension == "mkv" else "video/mp4"
    )
    response = await client.get("/api/jobs/" + str(job_id) + "/dubbing-file?download=true")
    assert (
        "filename=dubbed-" + str(job_id) + "." + extension
        in response.headers["content-disposition"]
    )
    track = (await client.get("/api/videos/" + str(ids["video"]) + "/dubbing/tracks")).json()[0]
    assert track["source_extension"] == extension and track["video_codec"] == result["video_codec"]
    assert track["video_copied"]


async def test_local_checkpoint_survives_cancel_skips_finished_sentences_and_rejects_corruption(
    env, monkeypatch
):
    _, _, directory, _, _ = env
    root = directory / "model"
    root.mkdir()
    monkeypatch.setattr(local, "ensure_ready", lambda _: None)
    monkeypatch.setattr(local, "model_dir", lambda _: root)
    calls, loaded = [], []
    cancel_second = True

    class Model:
        def generate_custom_voice(self, **kwargs):
            calls.append(kwargs["text"])
            if cancel_second and len(calls) == 2:
                raise JobCanceled()
            samples = np.sin(2 * np.pi * 220 * np.arange(24000) / 24000) * 0.3
            return [samples], 24000

    def load(_):
        loaded.append(True)
        return Model()

    monkeypatch.setattr(runner, "load_model", load)

    def write_wav(path, samples, rate):
        with wave.open(path, "wb") as audio:
            audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            audio.writeframes((samples * 32767).astype("<i2").tobytes())

    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=write_wav))

    async def child(args, report, **kwargs):
        runner.synthesize(args[-1])

    monkeypatch.setattr(local.subtitles, "subprocess_job", child)
    config = cfg.DubbingConfig(provider="local")
    items = [
        {"text": text, "voice": voice, "language": "en-US", "output": str(directory / name)}
        for text, voice, name in [
            ("Hello.", "ryan", "male.mp3"),
            ("Welcome.", "serena", "female.mp3"),
        ]
    ]
    with pytest.raises(JobCanceled):
        await local.synthesize_many(config, items, progress)
    root_cache = directory / "data/dubbing/speech-cache"
    clips = list(root_cache.glob("*.mp3"))
    assert len(clips) == 1 and cache.valid(clips[0])
    cancel_second = False
    await local.synthesize_many(config, items, progress)
    assert calls == ["Hello.", "Welcome.", "Welcome."]
    assert len(loaded) == 2
    await local.synthesize_many(config, items, progress)
    assert len(calls) == 3 and len(loaded) == 2
    items[1]["text"] = "Changed sentence."
    await local.synthesize_many(config, items, progress)
    assert calls[-1] == "Changed sentence." and len(calls) == 4
    clips[0].write_bytes(b"broken cache")
    await local.synthesize_many(config, items, progress)
    assert calls[-1] == "Hello." and len(calls) == 5
    assert cache.valid(clips[0])


async def test_reference_text_and_language_changes_invalidate_local_cache(env, monkeypatch):
    client, _, directory, _, _ = env
    reference = (await upload(client)).json()
    config = cfg.DubbingConfig(provider="local", local_model="qwen3-tts-0.6b-base")
    monkeypatch.setattr(local, "ensure_ready", lambda _: None)
    seen = []

    async def child(args, report, **kwargs):
        rows = json.loads(Path(args[-1]).read_text())["items"]
        for row in rows:
            seen.append(row)
            Path(row["output"]).write_bytes(audio_bytes(1, fmt="mp3"))

    monkeypatch.setattr(local.subtitles, "subprocess_job", child)
    items = [
        {
            "text": "Hello.",
            "voice": reference["id"],
            "language": "en-US",
            "output": str(directory / "reference.mp3"),
        }
    ]
    await local.synthesize_many(config, items, progress)
    await local.synthesize_many(config, items, progress)
    assert len(seen) == 1
    refs.write_text(reference["id"], "校对后的参考原文", reference["version"])
    await local.synthesize_many(config, items, progress)
    assert len(seen) == 2 and seen[-1]["ref_text"] == "校对后的参考原文"
    items[0]["language"] = "zh-CN"
    await local.synthesize_many(config, items, progress)
    assert len(seen) == 3
