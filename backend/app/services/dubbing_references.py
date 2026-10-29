"""Locally stored, immutable reference clips for custom dubbing voices."""
from __future__ import annotations

import asyncio
import base64
import fcntl
import re
import shutil
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import numpy as np
from pydantic import BaseModel, Field

from app.config import settings
from app.services.subtitles import subprocess_job

MAX_UPLOAD = 20 * 1024**2
LOCK = asyncio.Lock()


class Reference(BaseModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=80)
    gender: Literal["male", "female"]
    text: str = Field(default="", max_length=2000)
    version: int = Field(default=1, ge=1)
    duration_ms: int = Field(ge=3000, le=29500)
    created_at: str


def root():
    return settings.data_dir / "dubbing" / "references"


def directory(key: str):
    if not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("参考音色不存在")
    path = root() / key
    if path.is_symlink():
        raise ValueError("参考音色不存在")
    return path


def get(key: str):
    path = directory(key)
    try:
        manifest = path / "reference.json"
        audio = path / "audio.wav"
        if manifest.is_symlink() or audio.is_symlink() or not audio.is_file():
            raise ValueError("参考音色文件不存在")
        result = Reference.model_validate_json(manifest.read_text())
        if result.id != key:
            raise ValueError("参考音色文件无效")
        return result
    except (OSError, ValueError):
        raise ValueError("参考音色不存在或文件已损坏，请重新导入") from None


def list_references():
    if not root().is_dir():
        return []
    rows = []
    for path in root().iterdir():
        if re.fullmatch(r"[a-f0-9]{32}", path.name):
            try:
                rows.append(get(path.name))
            except ValueError:
                continue
    return sorted(rows, key=lambda r: r.created_at, reverse=True)


async def noop(*_):
    pass


async def create(upload, name, gender, text=""):
    name, text = name.strip(), text.strip()
    if not name:
        raise ValueError("请填写音色名称")
    # Validate metadata before decoding or retaining any file.
    key = uuid4().hex
    Reference(id=key, name=name, gender=gender, text=text, duration_ms=3000,
              created_at=datetime.now(UTC).isoformat())
    async with LOCK:
        root().mkdir(mode=0o700, parents=True, exist_ok=True)
        if len(list_references()) >= 100:
            raise ValueError("最多保存 100 个参考音色，请先删除不用的音色")
        with tempfile.TemporaryDirectory(prefix=".import-", dir=root()) as work:
            source, audio = Path(work) / "source", Path(work) / "audio.wav"
            size = 0
            with source.open("wb") as file:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise ValueError("参考音频不能超过 20 MB")
                    file.write(chunk)
            if not size:
                raise ValueError("音频文件为空")
            try:
                # Decode at most 30 seconds. Restrict demuxers so uploaded playlists
                # cannot read local files or fetch network URLs.
                await subprocess_job([
                    settings.ffmpeg_bin, "-v", "error", "-nostdin", "-y",
                    "-protocol_whitelist", "file,pipe",
                    "-format_whitelist", "wav,mp3,ogg,flac,mov,matroska,webm,aac",
                    "-i", str(source), "-map", "0:a:0", "-vn", "-t", "30",
                    "-ac", "1", "-ar", "32000", "-c:a", "pcm_s16le", str(audio),
                ], noop, timeout=45)
                with wave.open(str(audio), "rb") as wav:
                    duration = round(wav.getnframes() / wav.getframerate() * 1000)
                    pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
                if not 3000 <= duration <= 29500:
                    raise ValueError("请使用 3–29 秒的单人语音，建议录制 8–10 秒")
                if not len(pcm) or np.max(np.abs(pcm.astype(np.int32))) < 100:
                    raise ValueError("音频几乎没有声音，请检查麦克风或重新选择文件")
            except ValueError:
                raise
            except Exception:
                raise ValueError("无法读取音频，请使用 MP3、WAV、M4A、WebM、OGG 或 FLAC 文件") from None
            row = Reference(id=key, name=name, gender=gender, text=text, duration_ms=duration,
                            created_at=datetime.now(UTC).isoformat())
            # Publish a complete directory atomically; failed uploads leave no entry.
            source.unlink()
            (Path(work) / "reference.json").write_text(row.model_dump_json(), encoding="utf-8")
            Path(work).rename(directory(key))
        return row


class ReferenceVersionConflict(ValueError):
    pass


def write_text(key, text, version):
    """File lock also serializes the API and background worker processes."""
    path = directory(key)
    get(key)
    with (path / ".text.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        row = get(key)
        if row.version != version:
            raise ReferenceVersionConflict("音频原文已更新，请重新载入后保存；你的修改尚未提交")
        text = text.strip()
        if not text or len(text) > 2000:
            raise ValueError("音频原文须为 1–2000 字")
        row = row.model_copy(update={"text": text, "version": row.version + 1})
        temporary = path / ("." + uuid4().hex + ".json")
        try:
            temporary.write_text(row.model_dump_json(), encoding="utf-8")
            temporary.replace(path / "reference.json")
        finally:
            temporary.unlink(missing_ok=True)
        return row


def remove(key):
    get(key)
    shutil.rmtree(directory(key))


def payload(key):
    reference = get(key)
    if not reference.text.strip():
        raise ValueError("参考音频原文尚未识别完成，请完成识别或校对后再使用")
    audio = (directory(key) / "audio.wav").read_bytes()
    if len(audio) > 2 * 1024**2:
        raise ValueError("参考音频过大，请重新导入")
    return {"audio": "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii"),
            "text": reference.text}
