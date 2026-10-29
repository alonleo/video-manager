"""Cancelable CPU worker. One model load per video, bounded audio and batches."""

import json
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path


def emit(done, total, message):
    print(json.dumps({"done": done, "total": total, "message": message}), flush=True)


def cpu_threads():
    available = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    return max(1, min(4, available or 1))


def load_model(path):
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    model = WhisperModel(
        str(path),
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads(),
        local_files_only=True,
    )
    return BatchedInferencePipeline(model=model)


def batch_size(path):
    # Larger models need more activation memory. Small remains the default model.
    size = (path / "model.bin").stat().st_size
    return 1 if size > 2_000_000_000 else 2 if size > 700_000_000 else 4


def transcribe_audio(engine, audio, language, batch, progress=emit, on_cues=None):
    import numpy as np

    with wave.open(str(audio)) as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise ValueError("Expected mono 16 kHz PCM audio")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    samples = samples.astype(np.float32) / 32768.0
    duration_ms = round(len(samples) / 16)
    segments, info = engine.transcribe(
        samples,
        language=None if language == "auto" else language,
        vad_filter=True,
        beam_size=3,
        condition_on_previous_text=False,
        batch_size=batch,
        without_timestamps=False,
    )
    cues = []
    for segment in segments:
        start = max(0, min(duration_ms, round(segment.start * 1000)))
        end = max(0, min(duration_ms, round(segment.end * 1000)))
        if segment.text.strip() and end > start:
            cues.append({"start_ms": start, "end_ms": end, "text": segment.text.strip()})
            if on_cues:
                on_cues(cues, info.language)
        progress(end, duration_ms, "正在批量识别语音")
    return {"cues": cues, "language": info.language}


def write_snapshot(output, result):
    # Readers see either the previous complete JSON or the new complete JSON.
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(output)


def transcribe_video(path, manifest, output):
    data = json.loads(manifest.read_text())
    duration_ms, language = data["duration_ms"], data["language"]
    emit(0, duration_ms, "载入 Whisper 模型")
    engine = load_model(path)
    batch = batch_size(path)
    audio = manifest.parent / "audio.wav"
    cues, detected = [], None
    last_snapshot = 0
    def snapshot(chunk_cues, chunk_language, offset):
        nonlocal last_snapshot
        now = time.monotonic()
        if output.exists() and now - last_snapshot < 2:
            return
        current = cues + [{**cue, "start_ms": cue["start_ms"] + offset,
                           "end_ms": min(duration_ms, cue["end_ms"] + offset)} for cue in chunk_cues]
        current = [cue for cue in current if cue["end_ms"] > cue["start_ms"]]
        write_snapshot(output, {"cues": current, "language": detected or chunk_language})
        last_snapshot = now
    # Reuse the loaded model across bounded 10-minute audio chunks.
    for offset in range(0, duration_ms, 600000):
        emit(offset, duration_ms, "提取音轨")
        if shutil.disk_usage(manifest.parent).free < 128 * 1024**2:
            raise ValueError("磁盘剩余空间不足，无法提取音轨")
        subprocess.run(
            [
                data["ffmpeg_bin"],
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-ss",
                str(offset / 1000),
                "-i",
                data["path"],
                "-t",
                "600",
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

        def chunk_progress(done, total, message, offset=offset):
            fraction = min(done / total, 1) if total else 0
            emit(offset + fraction * min(600000, duration_ms - offset), duration_ms, message)

        result = transcribe_audio(engine, audio, language, batch, chunk_progress,
                                  lambda current, lang: snapshot(current, lang, offset))
        if result["cues"]:
            detected = detected or result["language"]
        for cue in result["cues"]:
            cue["start_ms"] += offset
            cue["end_ms"] = min(duration_ms, cue["end_ms"] + offset)
            if cue["end_ms"] > cue["start_ms"]:
                cues.append(cue)
        if len(cues) > 30000:
            raise ValueError("字幕片段超过 30000 条，请分段处理视频")
    write_snapshot(output, {"cues": cues, "language": detected})


def main():
    action, model_path = sys.argv[1:3]
    path = Path(model_path)
    if action == "download":
        from faster_whisper import WhisperModel, download_model

        model = sys.argv[3]
        path.mkdir(parents=True, exist_ok=True)
        emit(0, 1, "正在下载 Whisper 模型，可在任务中心取消；失败后可继续下载")
        download_model(model, output_dir=str(path))
        WhisperModel(
            str(path),
            device="cpu",
            compute_type="int8",
            cpu_threads=cpu_threads(),
            local_files_only=True,
        )
        (path / ".ready").write_text("ready")
        emit(1, 1, "模型下载完成")
    elif action == "transcribe-video":
        transcribe_video(path, Path(sys.argv[3]), Path(sys.argv[4]))
    elif action == "transcribe":
        audio, output, language = sys.argv[3:6]
        result = transcribe_audio(load_model(path), audio, language, batch_size(path))
        Path(output).write_text(json.dumps(result), encoding="utf-8")
    else:
        raise ValueError("未知操作")


if __name__ == "__main__":
    main()
