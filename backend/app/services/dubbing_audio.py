"""Bounded audio analysis in a subprocess; pitch estimates are editable, not identity."""
import json
import subprocess
import sys
import wave

import numpy as np


def estimate(samples, rate):
    pitches = []
    count = min(40, max(1, len(samples) // 800))
    for start in np.linspace(0, max(0, len(samples) - 800), count).astype(int):
        x = samples[start:start + 800].astype(np.float64) / 32768
        if len(x) < 800 or np.sqrt(np.mean(x * x)) < 0.006:
            continue
        x = (x - x.mean()) * np.hanning(len(x))
        autocorrelation = np.correlate(x, x, mode="full")[len(x) - 1:]
        lo, hi = int(rate / 350), int(rate / 65)
        peak = lo + int(np.argmax(autocorrelation[lo:hi]))
        if autocorrelation[0] > 0 and autocorrelation[peak] / autocorrelation[0] > 0.45:
            pitches.append(rate / peak)
    if len(pitches) < 3:
        return None
    return round(float(np.median(pitches)), 1)


def classify(audio, manifest, output):
    options = json.loads(open(manifest).read())
    result = []
    with wave.open(audio) as wav:
        rate, frames = wav.getframerate(), wav.getnframes()
        for i, cue in enumerate(options["cues"]):
            start = min(frames, round(cue["start_ms"] * rate / 1000))
            wav.setpos(start)
            samples = np.frombuffer(wav.readframes(min(rate * 12, max(0, round(
                (cue["end_ms"] - cue["start_ms"]) * rate / 1000)))), dtype="<i2")
            pitch = estimate(samples, rate)
            mode = options.get("mode", "auto")
            gender = mode if mode in {"male", "female"} else (
                options["fallback"] if pitch is None else "male" if pitch < 170 else "female")
            result.append({"gender": gender, "pitch_hz": pitch,
                           "uncertain": mode == "auto" and (pitch is None or 145 <= pitch <= 195)})
            print(json.dumps({"done": i + 1, "total": len(options["cues"]),
                              "message": f"声线分析 {i + 1}/{len(options['cues'])}"}), flush=True)
    with open(output, "w") as file:
        json.dump(result, file)


def mux(manifest):
    data = json.loads(open(manifest).read())
    # Inherit the wrapper process group so cancellation terminates ffmpeg as well.
    process = subprocess.Popen(data["args"], stdout=subprocess.PIPE, text=True)
    for line in process.stdout:
        if line.startswith("out_time_us="):
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue
            print(json.dumps({"done": seconds, "total": data["duration"],
                              "message": f"已合成 {seconds:.0f}/{data['duration']:.0f} 秒"}), flush=True)
    if process.wait():
        raise SystemExit(1)


if __name__ == "__main__":
    if sys.argv[1] == "classify":
        classify(*sys.argv[2:])
    elif sys.argv[1] == "mux":
        mux(sys.argv[2])
