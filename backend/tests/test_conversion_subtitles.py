"""Real media conversion, subtitle muxing/burning and reversible source replacement."""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from test_operations import env as base_env
from test_operations import sample  # noqa: F401

from app.config import settings
from app.models import SubtitleTrack, Video, VideoSource, WatchHistory
from app.services import conversion_replace
from app.services.library import scan_storage
from app.services.probe import _run_ffprobe
from app.workers import conversion


@pytest_asyncio.fixture
async def env(tmp_path, sample, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    async for value in base_env.__wrapped__(tmp_path, sample, monkeypatch):
        async with value.sessions() as session:
            tracks = [
                SubtitleTrack(
                    video_id=value.vid,
                    label="中文字幕",
                    language="zh-CN",
                    engine="import",
                    cues=[{"start_ms": 100, "end_ms": 1800, "text": "海边的回忆"}],
                ),
                SubtitleTrack(
                    video_id=value.vid,
                    label="英文翻译",
                    language="en",
                    engine="translation",
                    source_cues=[{"start_ms": 100, "end_ms": 1800, "text": "海边的回忆"}],
                    cues=[{"start_ms": 100, "end_ms": 1800, "text": "Memories by the sea"}],
                ),
            ]
            session.add_all(tracks)
            session.add(WatchHistory(video_id=value.vid, position_ms=321, played_ms=321))
            (await session.get(Video, value.vid)).is_favorite = True
            await session.commit()
            value.track_ids = [track.id for track in tracks]
        yield value


async def convert(env, **options):
    response = await env.client.post(f"/api/videos/{env.vid}/convert", json=options)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    await conversion.run_conversion({}, job_id)
    state = (await env.client.get(f"/api/jobs/{job_id}")).json()
    return state


@pytest.mark.parametrize("fmt", ["mkv", "mp4", "mov"])
async def test_two_soft_subtitle_tracks_and_text(env, fmt):
    before = env.path.read_bytes()
    job = await convert(env, format=fmt, subtitle_mode="embed", subtitle_track_ids=env.track_ids)
    assert job["state"] == "done", job
    path = conversion.export_path(job["id"], fmt)
    raw = await _run_ffprobe(str(path))
    subs = [s for s in raw["streams"] if s["codec_type"] == "subtitle"]
    assert len(subs) == 2
    assert [s["codec_name"] for s in subs] == (["subrip"] * 2 if fmt == "mkv" else ["mov_text"] * 2)
    text = subprocess.check_output(
        [settings.ffmpeg_bin, "-v", "error", "-i", str(path), "-map", "0:s:0", "-f", "srt", "-"]
    ).decode()
    assert "海边的回忆" in text
    assert env.path.read_bytes() == before


@pytest.mark.parametrize("fmt", ["mkv", "mp4", "mov", "avi", "ts", "m2ts"])
async def test_burned_subtitles_in_all_containers(env, fmt):
    job = await convert(
        env, format=fmt, subtitle_mode="burn", subtitle_track_ids=[env.track_ids[0]]
    )
    assert job["state"] == "done", job
    path = conversion.export_path(job["id"], fmt)
    raw = await _run_ffprobe(str(path))
    assert not [s for s in raw["streams"] if s["codec_type"] == "subtitle"]
    assert next(s for s in raw["streams"] if s["codec_type"] == "video")["codec_name"] == "h264"

    # Decode a real frame during the cue and compare lower-half pixel data, not metadata alone.
    def frame(p):
        return subprocess.check_output(
            [
                settings.ffmpeg_bin,
                "-v",
                "error",
                "-i",
                str(p),
                "-ss",
                "0.8",
                "-frames:v",
                "1",
                "-vf",
                "crop=iw:ih/2:0:ih/2",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ]
        )

    assert frame(path) != frame(env.path)
    assert (await env.client.get(f"/api/jobs/{job['id']}/file")).status_code == 200
    if fmt == "m2ts":
        data = path.read_bytes()
        assert data[4] == data[196] == 0x47


@pytest.mark.parametrize("fmt", ["avi", "m2ts"])
async def test_new_containers_without_subtitles(env, fmt):
    job = await convert(env, format=fmt)
    assert job["state"] == "done", job
    assert env.path.is_file()


async def test_bilingual_soft_subtitle(env):
    job = await convert(
        env,
        format="mkv",
        subtitle_mode="embed",
        subtitle_track_ids=[env.track_ids[1]],
        bilingual=True,
    )
    assert job["state"] == "done", job
    path = conversion.export_path(job["id"], "mkv")
    text = subprocess.check_output(
        [settings.ffmpeg_bin, "-v", "error", "-i", str(path), "-map", "0:s:0", "-f", "srt", "-"]
    ).decode()
    assert "海边的回忆" in text and "Memories by the sea" in text


@pytest.mark.parametrize("fmt", ["mkv", "mp4"])
async def test_replacement_retains_backup_identity_progress_and_rescan(env, fmt):
    original_path, original = env.path, env.path.read_bytes()
    job = await convert(
        env,
        format=fmt,
        subtitle_mode="embed",
        subtitle_track_ids=[env.track_ids[0]],
        replace_source=True,
    )
    assert job["state"] == "done", job
    result = job["result"]
    output = original_path.with_suffix("." + fmt)
    assert result["replaced"] and output.is_file()
    assert Path(result["backup_path"]).read_bytes() == original
    if fmt != "mp4":
        assert not original_path.exists()
    assert (await env.client.get(f"/api/jobs/{job['id']}/file")).content == output.read_bytes()
    async with env.sessions() as session:
        ref = await session.get(VideoSource, env.sid)
        assert ref.local_path == str(output)
        assert ref.uri == "local:" + output.name
        assert (await session.get(Video, env.vid)).is_favorite
        assert (await session.get(WatchHistory, env.vid)).position_ms == 321
        assert len((await session.scalars(select(SubtitleTrack))).all()) == 2
        stats = await scan_storage(session, ref.storage_id, do_probe=False)
        assert stats.created == stats.linked == 0
        assert len((await session.scalars(select(VideoSource))).all()) == 1


async def test_replacement_failure_restores_original(env, monkeypatch):
    original = env.path.read_bytes()
    response = await env.client.post(
        f"/api/videos/{env.vid}/convert", json={"format": "mkv", "replace_source": True}
    )
    job_id = response.json()["id"]
    real = conversion_replace.move_without_replace

    def fail_publish(source, destination):
        if source.name == "result.mkv":
            raise OSError("simulated publish failure")
        return real(source, destination)

    monkeypatch.setattr(conversion_replace, "move_without_replace", fail_publish)
    await conversion.run_conversion({}, job_id)
    assert (await env.client.get(f"/api/jobs/{job_id}")).json()["state"] == "error"
    assert env.path.read_bytes() == original
    assert not env.path.with_suffix(".mkv").exists()


async def test_replacement_database_failure_rolls_back_file_and_index(env, monkeypatch):
    original = env.path.read_bytes()
    original_install = conversion.install_replacement

    async def failing_install(session, *args, **kwargs):
        session.commit = AsyncMock(side_effect=RuntimeError("simulated commit failure"))
        return await original_install(session, *args, **kwargs)

    monkeypatch.setattr(conversion, "install_replacement", failing_install)
    job = await convert(env, format="mkv", replace_source=True)
    assert job["state"] == "error", job
    assert env.path.read_bytes() == original
    assert not env.path.with_suffix(".mkv").exists()
    async with env.sessions() as session:
        assert (await session.get(VideoSource, env.sid)).local_path == str(env.path)


async def test_replacement_collision_is_rejected_before_job(env):
    target = env.path.with_suffix(".mkv")
    target.write_bytes(b"keep-existing")
    response = await env.client.post(
        f"/api/videos/{env.vid}/convert", json={"format": "mkv", "replace_source": True}
    )
    assert response.status_code == 409
    assert target.read_bytes() == b"keep-existing"


@pytest.mark.parametrize(
    "options",
    [
        {"format": "ts", "subtitle_mode": "embed", "subtitle_track_ids": [1]},
        {"format": "avi", "subtitle_mode": "embed", "subtitle_track_ids": [1]},
        {"format": "mkv", "subtitle_mode": "burn", "subtitle_track_ids": [1, 2]},
        {"format": "mkv", "subtitle_mode": "embed", "subtitle_track_ids": []},
        {"format": "mkv", "subtitle_mode": "none", "subtitle_track_ids": [1]},
        {"format": "mkv", "subtitle_mode": "embed", "subtitle_track_ids": [999]},
    ],
)
async def test_invalid_subtitle_selection_rejected(env, options):
    assert (
        await env.client.post(f"/api/videos/{env.vid}/convert", json=options)
    ).status_code == 422


async def test_changed_source_or_subtitle_does_not_replace(env):
    response = await env.client.post(
        f"/api/videos/{env.vid}/convert", json={"format": "mkv", "replace_source": True}
    )
    job_id = response.json()["id"]
    env.path.write_bytes(env.path.read_bytes() + b"changed")
    changed = env.path.read_bytes()
    await conversion.run_conversion({}, job_id)
    assert (await env.client.get(f"/api/jobs/{job_id}")).json()["state"] == "error"
    assert env.path.read_bytes() == changed


async def test_cancel_before_install_retains_original(env, monkeypatch):
    original = env.path.read_bytes()
    install = conversion.install_replacement

    async def cancel_then_install(session, job_id, *args, **kwargs):
        assert (await env.client.post(f"/api/jobs/{job_id}/cancel")).status_code == 200
        return await install(session, job_id, *args, **kwargs)

    monkeypatch.setattr(conversion, "install_replacement", cancel_then_install)
    job = await convert(env, format="mkv", replace_source=True)
    assert job["state"] == "canceled", job
    assert env.path.read_bytes() == original
    assert not env.path.with_suffix(".mkv").exists()


async def test_native_subtitles_and_multiple_audio_tracks(env):
    srt = env.path.parent / "native.srt"
    srt.write_text("1\n00:00:00,100 --> 00:00:01,800\nNative subtitle\n")
    source = env.path.parent / "native.mkv"
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-v",
            "error",
            "-i",
            str(env.path),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880",
            "-i",
            str(srt),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-map",
            "1:a:0",
            "-map",
            "2:s:0",
            "-t",
            "2",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-c:s",
            "srt",
            str(source),
        ],
        check=True,
    )
    async with env.sessions() as session:
        ref = await session.get(VideoSource, env.sid)
        ref.local_path = str(source)
        ref.uri = "local:native.mkv"
        await session.commit()
    options = (
        await env.client.get(f"/api/videos/{env.vid}/conversion-options?source_id={env.sid}")
    ).json()
    index = options["subtitles"][0]["index"]
    for fmt in ["mkv", "mp4", "mov"]:
        job = await convert(
            env,
            format=fmt,
            subtitle_mode="embed",
            source_subtitle_indices=[index],
            subtitle_track_ids=[env.track_ids[0]],
        )
        assert job["state"] == "done", job
        raw = await _run_ffprobe(str(conversion.export_path(job["id"], fmt)))
        assert len([s for s in raw["streams"] if s["codec_type"] == "audio"]) == 2
        assert len([s for s in raw["streams"] if s["codec_type"] == "subtitle"]) == 2
    job = await convert(env, format="ts", subtitle_mode="burn", source_subtitle_indices=[index])
    assert job["state"] == "done", job


async def test_replacing_secondary_source_makes_verified_output_primary(env):
    other = env.path.with_name("other-copy.mp4")
    original = env.path.read_bytes()
    other.write_bytes(original)
    async with env.sessions() as session:
        ref = await session.get(VideoSource, env.sid)
        ref.is_primary = False
        session.add(
            VideoSource(
                video_id=env.vid,
                storage_id=ref.storage_id,
                source_type=ref.source_type,
                local_path=str(other),
                uri="local:other-copy.mp4",
                is_primary=True,
                available=True,
            )
        )
        await session.commit()
    job = await convert(env, format="mkv", source_id=env.sid, replace_source=True)
    assert job["state"] == "done", job
    async with env.sessions() as session:
        refs = (
            await session.scalars(select(VideoSource).where(VideoSource.video_id == env.vid))
        ).all()
        assert [r.id for r in refs if r.is_primary] == [env.sid]
        assert (await session.get(Video, env.vid)).container in {"mkv", "matroska"}
    assert other.read_bytes() == original


async def test_subtitle_edit_after_enqueue_requires_resubmission(env):
    original = env.path.read_bytes()
    response = await env.client.post(
        f"/api/videos/{env.vid}/convert",
        json={
            "format": "mkv",
            "subtitle_mode": "embed",
            "subtitle_track_ids": [env.track_ids[0]],
            "replace_source": True,
        },
    )
    job_id = response.json()["id"]
    async with env.sessions() as session:
        track = await session.get(SubtitleTrack, env.track_ids[0])
        track.version += 1
        await session.commit()
    await conversion.run_conversion({}, job_id)
    job = (await env.client.get(f"/api/jobs/{job_id}")).json()
    assert job["state"] == "error" and "字幕已修改" in job["error"]
    assert env.path.read_bytes() == original
