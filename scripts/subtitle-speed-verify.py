"""Cold/warm process-level speed comparison on fixed synthetic fixtures."""
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".run/subtitle-speed-validation"
BACKUP = ROOT / ".run/subtitle-speed-backup-20261005-063459/backend/app/services"
BACKEND = ROOT / "backend"
PYTHON = BACKEND / ".venv/bin/python"
OUT.mkdir(parents=True, exist_ok=True)
records = []
prior = {}
if "--resume" in sys.argv:
    for line in (ROOT / ".run/subtitle-speed-verify.log").read_text().splitlines():
        if line.startswith("{"):
            item = json.loads(line)
            if "case" in item:
                prior[item["case"]] = item


def run(name, command, env=None):
    if name in prior:
        records.append(prior[name])
        print(json.dumps(prior[name]), flush=True)
        return
    start = time.perf_counter()
    subprocess.run(command, cwd=BACKEND, env=env, check=True, stdout=subprocess.DEVNULL)
    seconds = time.perf_counter() - start
    item = {"case": name, "seconds": round(seconds, 3)}
    records.append(item)
    print(json.dumps(item), flush=True)


def valid_cues(path, duration, legacy_parent_clamp=False):
    data = json.loads(path.read_text())
    cues = data["cues"]
    if legacy_parent_clamp:
        # The old parent clips raw Whisper timestamps to the extracted audio bounds.
        cues = [{**c, "start_ms": max(0, min(duration, c["start_ms"])),
                 "end_ms": min(duration, c["end_ms"])} for c in cues]
        cues = [c for c in cues if c["end_ms"] > c["start_ms"]]
    assert cues and all(0 <= c["start_ms"] < c["end_ms"] <= duration for c in cues)
    assert all(a["start_ms"] <= b["start_ms"] for a, b in zip(cues, cues[1:]))
    return " ".join(c["text"] for c in cues)


with wave.open(str(ROOT / ".run/subtitle-validation/speech-16k.wav")) as f:
    frames, params = f.readframes(f.getnframes()), f.getparams()
audio = OUT / "fixture.wav"
with wave.open(str(audio), "wb") as f:
    f.setparams(params)
    f.writeframes(frames * 8)
duration = round(len(frames) * 8 / 2 / 16)
model = ROOT / "data/models/whisper/small"
manifest = OUT / "input.json"
manifest.write_text(json.dumps({
    "path": str(audio), "duration_ms": duration,
    "language": "en", "ffmpeg_bin": "ffmpeg",
}))
before, after = OUT / "asr-before.json", OUT / "asr-after.json"
run("ASR old cold process", [
    str(PYTHON), str(BACKUP / "subtitle_local.py"), "transcribe",
    str(model), str(audio), str(before), "en",
])
run("ASR optimized including extraction", [
    str(PYTHON), "-m", "app.services.subtitle_local", "transcribe-video",
    str(model), str(manifest), str(after),
])
for output in (before, after):
    text = valid_cues(output, duration, legacy_parent_clamp=(output == before))
    assert text.lower().count("hello") >= 7 and text.lower().count("computer") >= 7

sentences = [
    "This video explains how to organize a personal media library.",
    "You can generate subtitles and translate them into another language.",
    "The original video and its audio remain unchanged.",
    "Select a subtitle language from the menu at the bottom of the player.",
    "The model runs on your own computer without sending text to a server.",
    "Long recordings are processed in smaller sections.",
    "The timeline should remain accurate after translation.",
    "You can pause a video, change the playback speed and adjust the volume.",
    "The download will continue in the background.",
    "When a task finishes, a notification appears in the corner.",
    "Please check the source language before translating an imported subtitle.",
    "You can edit the translated text and export a subtitle file.",
]
texts = [f"{text} Example number {index + 1}." for index, text in enumerate(sentences * 4)]
translation_input = OUT / "translation-input.json"
translation_input.write_text(json.dumps({"texts": texts, "source": "en", "target": "zh-CN"}))
model = ROOT / "data/models/translation/nllb-600m-int8"
cache = OUT / "verify-memory.sqlite3"
cache.unlink(missing_ok=True)
environment = {**os.environ, "SUBTITLE_TRANSLATION_CACHE": str(cache)}
outputs = []
for label, entry, env in [
    ("translation old 48 unique", [str(BACKUP / "subtitle_translation_local.py")], None),
    ("translation optimized cold 48 unique", ["-m", "app.services.subtitle_translation_local"], environment),
    ("translation optimized cached 48 unique", ["-m", "app.services.subtitle_translation_local"], environment),
]:
    output = OUT / f"translation-{len(outputs)}.json"
    run(label, [str(PYTHON), *entry, "translate", str(model), str(translation_input), str(output)], env)
    translated = json.loads(output.read_text())
    assert len(translated) == len(texts) and all(isinstance(t, str) and t.strip() for t in translated)
    outputs.append(translated)
assert outputs[1] == outputs[2]
summary = {
    "asr_fixture": "58.719 s fixed synthetic English speech repeated eight times",
    "translation_fixture": "48 distinct English cues translated into Simplified Chinese",
    "quality": "Same models and beam widths (Whisper 3, NLLB 4); batching can change text and cue boundaries.",
    "asr_duration_ms": duration,
    "translation_same_text_count": sum(a == b for a, b in zip(outputs[0], outputs[1])),
    "records": records,
    "checks": "timestamps bounded and ordered; repeated speech retained; translation count and cache match",
}
(OUT / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False), flush=True)
