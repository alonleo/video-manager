"""Standalone worker; heavy dependencies stay out of the web process."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from . import dubbing_speech_cache as speech_cache
except ImportError:  # Executed directly by the isolated Python environment.
    import dubbing_speech_cache as speech_cache


def emit(done, total, message):
    print(json.dumps({"done": done, "total": total, "message": message}), flush=True)


def valid(path, size, sha):
    if not path.is_file() or path.stat().st_size != size:
        return False
    if sha:
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest() == sha
    return True


def download(manifest):
    import httpx
    info = json.loads(Path(manifest).read_text())
    root = Path(info["root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / ".ready").unlink(missing_ok=True)
    total, done = sum(v[0] for v in info["files"].values()), 0
    with httpx.Client(timeout=httpx.Timeout(60, connect=20), follow_redirects=True) as client:
        for name, (size, sha) in info["files"].items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            # Identical tokenizer weights are shared by both pinned Qwen variants.
            if sha and not path.exists():
                for sibling in root.parent.glob("qwen3-tts-*"):
                    source = sibling / name
                    if sibling != root and valid(source, size, sha):
                        try:
                            os.link(source, path)
                        except OSError:
                            pass
                        break
            if valid(path, size, sha):
                done += size
                emit(done, total, "已校验 " + name)
                continue
            part = path.with_name(path.name + ".partial")
            url = "https://huggingface.co/" + info["repository"] + "/resolve/" + info["revision"] + "/" + name
            for attempt in range(3):
                offset = part.stat().st_size if part.exists() else 0
                if offset > size:
                    part.unlink()
                    offset = 0
                if offset == size and valid(part, size, sha):
                    part.replace(path)
                    break
                if offset == size:
                    part.unlink()
                    offset = 0
                headers = {"Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = "bytes=" + str(offset) + "-"
                emit(done + offset, total, "下载语音模型：" + name)
                try:
                    with client.stream("GET", url, headers=headers) as response:
                        response.raise_for_status()
                        if response.status_code == 206:
                            if not response.headers.get("content-range", "").startswith("bytes " + str(offset) + "-"):
                                raise ValueError("下载续传范围无效")
                        else:
                            offset = 0
                        last = time.monotonic()
                        with part.open("ab" if offset else "wb") as file:
                            for chunk in response.iter_bytes(1024 * 1024):
                                offset += len(chunk)
                                if offset > size:
                                    raise ValueError("下载文件大小不匹配")
                                file.write(chunk)
                                if time.monotonic() - last > 0.5:
                                    emit(done + offset, total, "下载语音模型：" + name)
                                    last = time.monotonic()
                    if not valid(part, size, sha):
                        part.unlink(missing_ok=True)
                        raise ValueError("下载文件校验失败")
                    part.replace(path)
                    break
                except (httpx.HTTPError, ValueError):
                    if attempt == 2:
                        raise ValueError("模型下载失败，请检查代理或网络后重新下载") from None
            done += size
            emit(done, total, "已下载并校验 " + name)


def load_model(root):
    import torch
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    return Qwen3TTSModel.from_pretrained(
        str(root), device_map="cpu", dtype=torch.float32,
        attn_implementation="eager", local_files_only=True,
    )


def synthesize(manifest):
    import soundfile as sf
    data = json.loads(Path(manifest).read_text())
    root = Path(data["root"])
    with (root.parent / ".inference.lock").open("a") as lock:
        emit(0, 100, "等待本地语音模型")
        fcntl.flock(lock, fcntl.LOCK_EX)
        emit(1, 100, "加载本地语音模型（CPU）")
        model = None
        prompts = {}
        total = len(data["items"])
        for i, row in enumerate(data["items"]):
            emit(5 + 94 * i / total, 100, "本地模型配音 " + str(i + 1) + "/" + str(total))
            output = Path(row["output"])
            cache_path = row.get("cache_path")
            if cache_path and speech_cache.restore(cache_path, output):
                emit(5 + 94 * (i + 1) / total, 100, "复用已完成的本地配音")
                continue
            if model is None:
                model = load_model(root)
            if data["kind"] == "reference":
                key = row["ref_audio"]
                if key not in prompts:
                    prompts[key] = model.create_voice_clone_prompt(
                        ref_audio=key, ref_text=row["ref_text"], x_vector_only_mode=False)
                wavs, rate = model.generate_voice_clone(
                    text=row["text"], language=row["language"],
                    voice_clone_prompt=prompts[key], max_new_tokens=4096,
                )
            else:
                wavs, rate = model.generate_custom_voice(
                    text=row["text"], language=row["language"], speaker=row["voice"],
                    max_new_tokens=4096,
                )
            wav = output.with_suffix(".local.wav")
            if not len(wavs) or not len(wavs[0]):
                raise ValueError("本地模型没有生成语音")
            sf.write(str(wav), wavs[0], rate)
            subprocess.run([
                data["ffmpeg"], "-nostdin", "-v", "error", "-y", "-i", str(wav),
                "-c:a", "libmp3lame", "-q:a", "4", "-f", "mp3", str(output),
            ], check=True)
            wav.unlink()
            if cache_path:
                speech_cache.publish(output, cache_path)
            emit(5 + 94 * (i + 1) / total, 100,
                 "已保存本地配音 " + str(i + 1) + "/" + str(total))
        emit(100, 100, "本地语音合成完成")


if __name__ == "__main__":
    if sys.argv[1] == "download":
        download(sys.argv[2])
    elif sys.argv[1] == "check":
        root = Path(sys.argv[2])
        with (root.parent / ".inference.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            model = load_model(root)
            print(json.dumps({"languages": model.get_supported_languages()}), flush=True)
    elif sys.argv[1] == "synthesize":
        synthesize(sys.argv[2])
