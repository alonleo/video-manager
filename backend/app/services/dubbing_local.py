"""Qwen3-TTS local models, downloads and cancelable CPU inference."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from app.config import PROJECT_ROOT, settings
from app.db import SessionLocal
from app.services import dubbing_references as refs
from app.services import subtitles
from app.services import dubbing_speech_cache as speech_cache
from app.services.subtitle_config import read_config

COMMON = {
    "generation_config.json": (245, None),
    "merges.txt": (1671839, None),
    "preprocessor_config.json": (127, None),
    "speech_tokenizer/config.json": (2336, None),
    "speech_tokenizer/configuration.json": (76, None),
    "speech_tokenizer/model.safetensors": (
        682293092, "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),
    "speech_tokenizer/preprocessor_config.json": (234, None),
    "tokenizer_config.json": (7344, None),
    "vocab.json": (2776833, None),
}
MODELS = {
    "qwen3-tts-0.6b-custom": {
        "name": "Qwen3-TTS 0.6B · 内置男女声", "kind": "builtin",
        "repository": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "revision": "85e237c12c027371202489a0ec509ded67b5e4b5",
        "files": {**COMMON, "config.json": (4908, None), "model.safetensors": (
            1811626576, "bc3c7e785eb961179c25450d1acff03f839e0002f2f3a5aeb67b5735c0fa2adb")},
    },
    "qwen3-tts-0.6b-base": {
        "name": "Qwen3-TTS 0.6B · 参考音色", "kind": "reference",
        "repository": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "revision": "5d83992436eae1d760afd27aff78a71d676296fc",
        "files": {**COMMON, "config.json": (4494, None), "model.safetensors": (
            1829344272, "180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6")},
    },
}
LANGUAGES = {
    "zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean",
    "de": "German", "fr": "French", "ru": "Russian", "pt": "Portuguese",
    "es": "Spanish", "it": "Italian",
}
SPEAKERS = [
    ("uncle_fu", "沉稳男声 · Uncle Fu", "male"),
    ("dylan", "清朗男声 · Dylan", "male"),
    ("eric", "活泼男声 · Eric", "male"),
    ("ryan", "英语男声 · Ryan", "male"),
    ("aiden", "明亮男声 · Aiden", "male"),
    ("vivian", "明亮女声 · Vivian", "female"),
    ("serena", "温柔女声 · Serena", "female"),
    ("ono_anna", "日语女声 · Ono Anna", "female"),
    ("sohee", "韩语女声 · Sohee", "female"),
]
DOWNLOAD_LOCK = asyncio.Lock()
RUNNER = Path(__file__).with_name("dubbing_local_runner.py")


def runtime_python():
    return PROJECT_ROOT / ".tts-venv" / "bin" / "python"


def runtime_ready():
    return runtime_python().is_file() and (PROJECT_ROOT / ".tts-venv" / ".qwen-ready").is_file()


def model_dir(model):
    if model not in MODELS:
        raise ValueError("未知的本地语音模型")
    return settings.data_dir / "models" / "dubbing" / model


def model_ready(model):
    info, root = MODELS[model], model_dir(model)
    try:
        return (root / ".ready").read_text() == info["revision"] and all(
            (root / name).is_file() and (root / name).stat().st_size == size
            for name, (size, _) in info["files"].items())
    except OSError:
        return False


def catalog():
    return {"runtime_ready": runtime_ready(), "device": "cpu",
            "languages": list(LANGUAGES),
            "voices": [{"id": key, "name": name, "gender": gender, "language": ""}
                       for key, name, gender in SPEAKERS], "models": [
        {"id": key, "name": info["name"], "kind": info["kind"],
         "installed": model_ready(key),
         "download_bytes": sum(v[0] for v in info["files"].values()),
         "size_bytes": sum(v[0] for v in info["files"].values()) if model_ready(key) else None}
        for key, info in MODELS.items()]}


def validate_language(language):
    if language.split("-")[0] not in LANGUAGES:
        raise ValueError("本地语音模型支持中、英、日、韩、德、法、俄、葡、西、意语言")


def is_reference(config):
    return config.provider == "reference" or (
        config.provider == "local" and MODELS[config.local_model]["kind"] == "reference")


def ensure_ready(model):
    if not runtime_ready():
        raise ValueError("本地语音运行环境未安装，请先运行 scripts/setup-local-tts.sh")
    if not model_ready(model):
        raise ValueError("请先在配音设置中下载所选本地语音模型")


async def download(payload, progress):
    model = payload["model"]
    if model not in MODELS:
        raise ValueError("未知的本地语音模型")
    if not runtime_ready():
        raise ValueError("本地语音运行环境未安装，请先运行 scripts/setup-local-tts.sh")
    async def locked():
        async with DOWNLOAD_LOCK:
            if model_ready(model):
                return {"model": model, "size_bytes": sum(v[0] for v in MODELS[model]["files"].values())}
            root = model_dir(model)
            root.mkdir(parents=True, exist_ok=True)
            required = sum(v[0] for v in MODELS[model]["files"].values())
            if shutil.disk_usage(root).free < required + 512 * 1024**2:
                raise ValueError("磁盘空间不足，请至少预留模型大小加 512 MB")
            async with SessionLocal() as session:
                config = await read_config(session)
            env = os.environ.copy()
            if config.model_download_proxy:
                for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                    env[key] = config.model_download_proxy
            settings.work_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="tts-download-", dir=settings.work_dir) as work:
                manifest = Path(work) / "download.json"
                manifest.write_text(json.dumps({**MODELS[model], "root": str(root)}))
                async def report(done, total, message):
                    await progress(98 * done / total if total else 0, 100, message)
                await subtitles.subprocess_job([sys.executable, str(RUNNER), "download", str(manifest)],
                                               report, parse_progress=True, env=env, timeout=21600)
                await progress(98, 100, "下载完成，正在检查本地语音模型")
                await subtitles.subprocess_job(
                    [str(runtime_python()), str(RUNNER), "check", str(root)],
                    lambda_done(progress, 98, "检查本地语音模型"), timeout=300,
                    env=offline_env())
            (root / ".ready").write_text(MODELS[model]["revision"])
            await progress(100, 100, "本地语音模型已就绪")
            return {"model": model, "size_bytes": required}
    return await subtitles.wait_cancelable(locked(), progress, None, None, "")


def lambda_done(progress, value, message):
    async def report(*_):
        await progress(value, 100, message)
    return report


def offline_env():
    return {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1", "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"}


async def synthesize_many(config, items, progress):
    ensure_ready(config.local_model)
    if not items:
        return
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    cache_root = settings.data_dir / "dubbing" / "speech-cache"
    cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    rows, cached, identities, cache_paths = [], 0, {}, []
    for item in items:
        language = item.get("language", config.target_language)
        validate_language(language)
        row = {**item, "language": LANGUAGES[language.split("-")[0]]}
        voice = row["voice"]
        if voice not in identities:
            identity = {"model": config.local_model,
                        "revision": MODELS[config.local_model]["revision"], "voice": voice}
            if is_reference(config):
                reference = refs.get(voice)
                audio = refs.directory(reference.id) / "audio.wav"
                identity.update(reference_sha256=speech_cache.digest(audio),
                                reference_text=reference.text)
            identities[voice] = identity
        identity = {**identities[voice], "text": row["text"], "language": row["language"]}
        cache = cache_root / (speech_cache.key(identity) + ".mp3")
        cache_paths.append(cache)
        await progress(cached, len(items), "检查可复用的本地配音")
        if speech_cache.restore(cache, row["output"]):
            cached += 1
            continue
        row["cache_path"] = str(cache)
        if is_reference(config):
            reference = refs.get(row.pop("voice"))
            row.update(ref_audio=str(refs.directory(reference.id) / "audio.wav"),
                       ref_text=identities[voice]["reference_text"])
        rows.append(row)
    await progress(cached, len(items), f"已复用 {cached}/{len(items)} 句配音")
    if not rows:
        return
    async def report(done, total, message):
        if done is None:
            return await progress(None, None, "")
        fraction = min(1, max(0, done / total)) if total else 0
        await progress(cached + len(rows) * fraction, len(items),
                       f"已复用 {cached} 句 · {message}")
    try:
        with tempfile.TemporaryDirectory(prefix="tts-local-", dir=settings.work_dir) as work:
            manifest = Path(work) / "input.json"
            manifest.write_text(json.dumps({
                "root": str(model_dir(config.local_model)), "items": rows,
                "kind": MODELS[config.local_model]["kind"], "ffmpeg": settings.ffmpeg_bin,
            }))
            await subtitles.subprocess_job(
                [str(runtime_python()), str(RUNNER), "synthesize", str(manifest)], report,
                timeout=21600, parse_progress=True, env=offline_env())
            # The runner checkpoints each finished sentence. This also supports
            # compatible runners that only write their requested output paths.
            for row in rows:
                if not speech_cache.valid(row["cache_path"]):
                    speech_cache.publish(row["output"], row["cache_path"])
    finally:
        speech_cache.prune(cache_root, cache_paths)


async def synthesize(config, text, voice, output):
    async def progress(*_):
        pass
    await synthesize_many(config, [{"text": text, "voice": voice, "output": str(output)}], progress)
