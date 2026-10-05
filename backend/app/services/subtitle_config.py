"""Validated subtitle providers; secret values never leave the server."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.models import Setting
from app.services import subtitle_translation_local as local_translation

WHISPER_MODELS = {
    "tiny": ("Whisper Tiny", 80, "快速体验，识别精度较低"),
    "base": ("Whisper Base", 150, "轻量，多语言"),
    "small": ("Whisper Small", 500, "日常字幕，速度与准确率均衡"),
    "medium": ("Whisper Medium", 1550, "更高准确率，CPU 处理较慢"),
    "large-v3": ("Whisper Large v3", 3100, "高准确率，需要更多内存"),
    "turbo": ("Whisper Turbo", 1650, "大模型精度与速度折中"),
}
OLLAMA_MODELS = ["qwen2.5:3b", "qwen2.5:7b", "gemma3:4b"]


class Provider(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    model: str = Field(default="whisper-1", min_length=1, max_length=150)
    api_key: str | None = Field(default=None, max_length=4096)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        value = value.strip().rstrip("/")
        u = urlsplit(value)
        if (
            u.scheme not in {"http", "https"}
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
        ):
            raise ValueError("请输入不含账号、查询参数的 HTTP(S) 服务地址")
        if u.scheme == "http" and u.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("远程服务必须使用 HTTPS；本机服务可使用 HTTP")
        return value


class SubtitleConfig(BaseModel):
    asr_provider: Literal["local", "api"] = "local"
    asr_model: str = "small"
    asr_api: Provider = Field(default_factory=Provider)
    translation_provider: Literal["local", "ollama", "api"] = "local"
    local_translation_model: Literal["nllb-600m-int8"] = "nllb-600m-int8"
    translation_api: Provider = Field(
        default_factory=lambda: Provider(base_url=settings.llm_base_url, model=settings.llm_model)
    )
    model_download_proxy: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = Field(
        default="qwen2.5:3b", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_./:\-]{0,149}$"
    )

    @field_validator("model_download_proxy")
    @classmethod
    def download_proxy(cls, value):
        if not value.strip():
            return ""
        value = Provider.valid_url(value)
        if urlsplit(value).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("下载代理请输入本机 HTTP 代理地址")
        return value

    @field_validator("asr_model")
    @classmethod
    def known_model(cls, value):
        if value not in WHISPER_MODELS:
            raise ValueError("不支持的 Whisper 模型")
        return value

    @field_validator("ollama_url")
    @classmethod
    def local_ollama(cls, value):
        value = Provider.valid_url(value)
        if urlsplit(value).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("本地翻译仅连接本机 Ollama；远程服务请使用 API 模式")
        return value


def model_dir(model: str) -> Path:
    if model not in WHISPER_MODELS:
        raise ValueError("未知模型")
    return settings.data_dir / "models" / "whisper" / model


def model_ready(model: str) -> bool:
    root = model_dir(model)
    return all(
        (root / p).is_file() for p in ["model.bin", "config.json", "tokenizer.json", ".ready"]
    )


def model_size_bytes(root: Path) -> int | None:
    """Sum flat model assets; exclude Hugging Face caches and readiness markers."""
    try:
        return sum(
            p.stat().st_size for p in root.iterdir() if p.is_file() and not p.name.startswith(".")
        )
    except OSError:
        return None


async def read_config(session) -> SubtitleConfig:
    row = await session.get(Setting, "subtitle.providers")
    config = SubtitleConfig.model_validate(row.value if row else {})
    if not row and settings.llm_api_key:
        config.translation_api.api_key = settings.llm_api_key
    return config


def public_config(config: SubtitleConfig):
    data = config.model_dump()
    for key in ("asr_api", "translation_api"):
        data[key]["has_api_key"] = bool(data[key].pop("api_key"))
    return data


async def save_config(session, config):
    old = await read_config(session)
    for name in ("asr_api", "translation_api"):
        new_provider, old_provider = getattr(config, name), getattr(old, name)
        if new_provider.api_key is None:
            if new_provider.base_url != old_provider.base_url and old_provider.api_key:
                raise ValueError("更换 API 地址时请填写新密钥，或勾选清除原密钥")
            new_provider.api_key = old_provider.api_key
    row = await session.get(Setting, "subtitle.providers")
    if not row:
        row = Setting(key="subtitle.providers", value={}, is_secret=True)
        session.add(row)
    row.value = config.model_dump()
    row.is_secret = True
    await session.commit()
    return public_config(config)


def auth(provider):
    return {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}


async def api_request(method, url, **kwargs):
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(180, connect=15),
            follow_redirects=False,
            trust_env=urlsplit(url).hostname not in {"localhost", "127.0.0.1", "::1"},
        ) as client:
            response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise ValueError(
            f"AI 服务返回 HTTP {exc.response.status_code}，请检查地址、密钥、模型与额度"
        ) from None
    except (httpx.RequestError, ValueError):
        raise ValueError("AI 服务连接失败或响应无效，请检查网络和服务地址") from None


async def catalog(config):
    installed, ollama_error = [], None
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            r = await client.get(config.ollama_url + "/api/tags")
            r.raise_for_status()
            installed = [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        ollama_error = "Ollama 未连接，请先在本机安装并启动 Ollama"
    return {
        "asr_runtime": importlib.util.find_spec("faster_whisper") is not None,
        "translation_runtime": local_translation.runtime_ready(),
        "translation": [
            {
                "id": local_translation.MODEL_ID,
                "name": "NLLB-200 600M · INT8",
                "size_mb": 630,
                "size_bytes": (
                    model_size_bytes(local_translation.model_dir(local_translation.MODEL_ID))
                    if local_translation.model_ready(local_translation.MODEL_ID)
                    else None
                ),
                "description": "轻量多语言翻译，CPU 离线运行。CC-BY-NC 4.0（非商业用途）。",
                "installed": local_translation.model_ready(local_translation.MODEL_ID),
            }
        ],
        "whisper": [
            {
                "id": k,
                "name": v[0],
                "size_mb": v[1],
                "size_bytes": model_size_bytes(model_dir(k)) if model_ready(k) else None,
                "description": v[2],
                "installed": model_ready(k),
            }
            for k, v in WHISPER_MODELS.items()
        ],
        "ollama": [
            {"id": k, "installed": k in installed} for k in dict.fromkeys(OLLAMA_MODELS + installed)
        ],
        "ollama_error": ollama_error,
    }
