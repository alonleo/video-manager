"""Background original-language transcription of bounded reference clips."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.services import dubbing_references as refs
from app.services import subtitle_config as cfg
from app.services import subtitles


async def transcribe(payload, progress):
    key, version = payload["reference_id"], payload["version"]
    row = refs.get(key)
    if row.version != version:
        return {"reference_id": key, "applied": False}
    async with SessionLocal() as session:
        config = await cfg.read_config(session)
    provider, model = payload["provider"], payload["model"]
    label = ("男声" if row.gender == "male" else "女声") + " · " + row.name
    if provider == "local" and not cfg.model_ready(model):
        raise ValueError("请先在「AI 模型与服务」下载所选识别模型，再重试提取原文；也可手动填写")
    if provider == "api" and payload.get("base_url") != config.asr_api.base_url:
        raise ValueError("识别 API 地址已更改，请重新提取原文以使用当前设置")
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reference-asr-", dir=settings.work_dir) as work:
        audio, output = Path(work) / "audio.wav", Path(work) / "result.json"

        async def decoding(*_):
            await progress(5, 100, label + "：准备识别音频")

        await subtitles.subprocess_job(
            [
                settings.ffmpeg_bin,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(refs.directory(key) / "audio.wav"),
                "-t",
                "30",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio),
            ],
            decoding,
            timeout=45,
        )
        await progress(10, 100, label + "：载入识别模型 " + model)
        if provider == "local":

            async def recognizing(done, total, message):
                fraction = min(1, max(0, done / total)) if total else 0
                await progress(10 + 85 * fraction, 100, label + "：" + message)

            await subtitles.subprocess_job(
                [
                    sys.executable,
                    "-m",
                    "app.services.subtitle_local",
                    "transcribe",
                    str(cfg.model_dir(model)),
                    str(audio),
                    str(output),
                    "auto",
                ],
                recognizing,
                timeout=1800,
                parse_progress=True,
                env={
                    **os.environ,
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "OMP_NUM_THREADS": "4",
                    "MKL_NUM_THREADS": "4",
                },
            )
            result = json.loads(output.read_text())
            text = " ".join(c["text"].strip() for c in result["cues"])
        else:
            with audio.open("rb") as file:
                result = await subtitles.wait_cancelable(
                    cfg.api_request(
                        "POST",
                        config.asr_api.base_url + "/audio/transcriptions",
                        headers=cfg.auth(config.asr_api),
                        data={"model": model, "response_format": "json"},
                        files={"file": ("reference.wav", file, "audio/wav")},
                    ),
                    progress,
                    15,
                    100,
                    label + "：正在请求语音识别 API",
                )
            if not isinstance(result, dict):
                raise ValueError("语音识别 API 未返回有效原文，请检查所选模型")
            text = result.get("text")
            if not isinstance(text, str) and isinstance(result.get("segments"), list):
                text = " ".join(
                    s.get("text", "").strip()
                    for s in result["segments"]
                    if isinstance(s, dict) and isinstance(s.get("text"), str)
                )
        if not isinstance(text, str) or not text.strip():
            raise ValueError("未识别到可用语音，请换用清晰的单人录音、重试或手动填写原文")
        if len(text.strip()) > 2000:
            raise ValueError("识别原文超过 2000 字，请检查服务响应或手动填写原文")
        await progress(98, 100, label + "：保存识别原文")
        async with refs.LOCK:
            current = refs.get(key)
            if current.version != version:
                return {"reference_id": key, "applied": False}
            try:
                refs.write_text(key, text, version)
            except refs.ReferenceVersionConflict:
                return {"reference_id": key, "applied": False}
    return {
        "reference_id": key,
        "applied": True,
        "name": row.name,
        "gender": row.gender,
        "model": model,
    }


async def dispatch(payload, progress):
    await progress(0, 100, "等待提取参考音频原文")

    async def locked():
        async with subtitles.AI_LOCK:
            return await transcribe(payload, progress)

    # Continue cancellation checks while waiting for another ASR/translation task.
    return await subtitles.wait_cancelable(locked(), progress, None, None, "")
