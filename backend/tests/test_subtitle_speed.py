"""Speed optimizations must preserve cue content, timing, invalidation and cancellation."""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

from app.services import subtitle_local as speech
from app.services import subtitle_translation_local as local
from app.services import subtitles
from app.services.subtitle_translation_memory import TranslationMemory


@pytest.fixture
def fake_translator(monkeypatch):
    calls = []

    class Tokenizer:
        def encode(self, text, out_type):
            return text.split()

        def decode(self, tokens):
            return " ".join(tokens)

    class Translator:
        def translate_batch(self, batch, target_prefix, **kwargs):
            calls.extend(
                (row[0], prefix[0], " ".join(row[1:-1]))
                for row, prefix in zip(batch, target_prefix, strict=True)
            )
            return [
                SimpleNamespace(hypotheses=[prefix + row[1:-1]])
                for row, prefix in zip(batch, target_prefix, strict=True)
            ]

    monkeypatch.setattr(local, "load_model", lambda root: (Translator(), Tokenizer()))
    return calls


def test_repeated_cues_cache_and_invalidation(tmp_path, monkeypatch, fake_translator):
    cache = tmp_path / "cache.sqlite3"
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.bin").write_bytes(b"first")
    texts = ["first sentence", "second sentence", "first sentence"]

    def run(values, target="zh-CN"):
        return local.translate_texts(
            model, values, "en", target, lambda *args: None, cache_path=cache
        )

    assert run(texts) == texts
    assert len(fake_translator) == 2
    assert run(texts) == texts
    assert len(fake_translator) == 2
    assert run(["changed text", *texts]) == ["changed text", *texts]
    assert len(fake_translator) == 3
    assert run(texts, "ja") == texts
    assert len(fake_translator) == 5
    (model / "model.bin").write_bytes(b"updated model")
    assert run(texts) == texts
    assert len(fake_translator) == 7

    def forbidden(*args):
        pytest.fail("A cache hit must not load the model")

    monkeypatch.setattr(local, "load_model", forbidden)
    assert run(texts) == texts
    assert local.translate_texts(model, texts, "en", "English") == texts


def test_corrupt_cache_is_optional(tmp_path, fake_translator):
    cache = tmp_path / "broken.sqlite3"
    cache.write_bytes(b"not a database")
    assert local.translate_texts(
        tmp_path, ["still translate"], "en", "zh", lambda *args: None, cache_path=cache
    ) == ["still translate"]


def test_memory_limit_and_language_isolation(tmp_path):
    cache = tmp_path / "memory.sqlite3"
    memory = TranslationMemory(cache, {"target": "zh"}, limit=2)
    memory.put_many([("a", "A"), ("b", "B"), ("c", "C")])
    assert memory.db.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 2
    assert memory.get("c") == "C"
    memory.close()
    other = TranslationMemory(cache, {"target": "ja"})
    assert other.get("c") is None
    other.close()


def test_long_video_loads_once_preserves_offsets_and_bounds(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.bin").write_bytes(b"fake")
    manifest, output = tmp_path / "input.json", tmp_path / "result.json"
    manifest.write_text(
        json.dumps(
            {
                "path": "unchanged-video.mp4",
                "duration_ms": 1201500,
                "language": "auto",
                "ffmpeg_bin": "ffmpeg",
            }
        )
    )
    loads, extractions, progress = [], [], []
    engine = object()
    monkeypatch.setattr(speech, "load_model", lambda path: loads.append(path) or engine)
    monkeypatch.setattr(speech, "emit", lambda *args: progress.append(args))
    monkeypatch.setattr(speech.subprocess, "run", lambda command, **kw: extractions.append(command))

    def transcribe(received, audio, language, batch, callback, on_cues=None):
        assert received is engine and batch == 4
        cues = [{"start_ms": 100, "end_ms": 2000, "text": "speech"}]
        if on_cues:
            on_cues(cues, "en")
            if len(extractions) == 1:
                snapshot = json.loads(output.read_text())
                assert snapshot == {"cues": cues, "language": "en"}
        callback(2000, 2000, "done")
        return {"cues": cues, "language": "en"}

    monkeypatch.setattr(speech, "transcribe_audio", transcribe)
    speech.transcribe_video(model, manifest, output)
    result = json.loads(output.read_text())
    assert len(loads) == 1 and len(extractions) == 3
    assert [row[row.index("-ss") + 1] for row in extractions] == ["0.0", "600.0", "1200.0"]
    assert result["cues"] == [
        {"start_ms": 100, "end_ms": 2000, "text": "speech"},
        {"start_ms": 600100, "end_ms": 602000, "text": "speech"},
        {"start_ms": 1200100, "end_ms": 1201500, "text": "speech"},
    ]
    assert result["language"] == "en"
    assert all(a[0] <= b[0] for a, b in zip(progress, progress[1:], strict=False))


@pytest.mark.skipif(os.name != "posix", reason="POSIX child process groups")
async def test_cancel_stops_extraction_descendant(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    descendant = (
        "import time;from pathlib import Path;"
        f"p=Path({str(heartbeat)!r});"
        "\nwhile True:\n p.write_text(str(time.monotonic()));time.sleep(0.05)"
    )
    parent = (
        "import subprocess,sys,signal,time;"
        f"child=subprocess.Popen([sys.executable,'-c',{descendant!r}]);"
        # Wait for the child during termination so it cannot remain a zombie.
        "signal.signal(signal.SIGTERM,lambda *args:(child.wait(timeout=2),sys.exit(0)));"
        "time.sleep(30)"
    )

    async def cancel(*args):
        raise RuntimeError("cancel test")

    with pytest.raises(RuntimeError, match="cancel test"):
        await subtitles.subprocess_job([sys.executable, "-c", parent], cancel)
    assert heartbeat.exists()
    stopped = heartbeat.read_text()
    await asyncio.sleep(0.15)
    assert heartbeat.read_text() == stopped


def test_whisper_final_snapshot_stays_readable_during_write(tmp_path, monkeypatch):
    """Simulate the parent reading exactly when a direct overwrite truncates the file."""
    from pathlib import Path
    output = tmp_path / 'result.json'
    manifest = tmp_path / 'input.json'
    manifest.write_text(json.dumps({'path': 'video.mp4', 'duration_ms': 1000, 'language': 'auto', 'ffmpeg_bin': 'ffmpeg'}))
    cues = [{'start_ms': 0, 'end_ms': 900, 'text': 'hello'}]
    monkeypatch.setattr(speech, 'load_model', lambda _: object())
    monkeypatch.setattr(speech.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(speech, 'batch_size', lambda _: 1)
    def transcribe(engine, audio, language, batch, report, on_cues):
        on_cues(cues, 'en')
        return {'cues': cues, 'language': 'en'}
    monkeypatch.setattr(speech, 'transcribe_audio', transcribe)
    write_text = Path.write_text
    observed = []
    def concurrent_write(path, text, *args, **kwargs):
        if path == output:
            with path.open('w'):
                # A progress reader can run after truncation, before bytes are written.
                json.loads(output.read_text())
        if output.exists():
            observed.append(json.loads(output.read_text()))
        return write_text(path, text, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', concurrent_write)
    speech.transcribe_video(tmp_path / 'small', manifest, output)
    assert observed and observed[-1]['cues'] == cues
    assert json.loads(output.read_text()) == {'cues': cues, 'language': 'en'}
