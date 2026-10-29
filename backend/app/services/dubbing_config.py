"""Voice synthesis settings. Provider keys stay on the server."""
from __future__ import annotations

import asyncio
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, field_validator

from app.models import Setting
from app.services import dubbing_local as local
from app.services import dubbing_references as refs
from app.services.subtitle_config import Provider, SubtitleConfig, auth

LANGUAGES = {
    "zh-CN": "中文（普通话）", "en-US": "英语（美国）", "en-GB": "英语（英国）",
    "ja-JP": "日语", "ko-KR": "韩语", "fr-FR": "法语", "de-DE": "德语",
    "es-ES": "西班牙语", "ru-RU": "俄语", "pt-BR": "葡萄牙语（巴西）",
    "it-IT": "意大利语", "ar-SA": "阿拉伯语", "hi-IN": "印地语",
    "th-TH": "泰语", "vi-VN": "越南语", "id-ID": "印度尼西亚语",
}


class DubbingConfig(BaseModel):
    provider: Literal["edge", "api", "reference", "local"] = "edge"
    local_model: Literal["qwen3-tts-0.6b-custom", "qwen3-tts-0.6b-base"] = "qwen3-tts-0.6b-custom"
    local_male_voice: str = Field(default="uncle_fu", max_length=80)
    local_female_voice: str = Field(default="serena", max_length=80)
    target_language: str = "zh-CN"
    male_voice: str = Field(default="", max_length=150)
    female_voice: str = Field(default="", max_length=150)
    fallback_gender: Literal["male", "female"] = "female"
    api: Provider = Field(default_factory=lambda: Provider(model="tts-1"))
    reference_api: Provider = Field(default_factory=lambda: Provider(
        base_url="https://api.siliconflow.cn/v1", model="FunAudioLLM/CosyVoice2-0.5B"))
    male_reference: str = Field(default="", max_length=32)
    female_reference: str = Field(default="", max_length=32)
    proxy: str = ""
    keep_original: bool = True

    @field_validator("target_language")
    @classmethod
    def language(cls, value):
        if value not in LANGUAGES:
            raise ValueError("请选择支持的目标语言")
        return value

    @field_validator("proxy")
    @classmethod
    def proxy_url(cls, value):
        return SubtitleConfig.download_proxy(value)


async def read_config(session):
    row = await session.get(Setting, "dubbing.providers")
    return DubbingConfig.model_validate(row.value if row else {})


def public_config(config):
    data = config.model_dump()
    for field in ("api", "reference_api"):
        data[field]["has_api_key"] = bool(data[field].pop("api_key"))
    return data


async def save_config(session, config):
    old = await read_config(session)
    for field in ("api", "reference_api"):
        new_provider, old_provider = getattr(config, field), getattr(old, field)
        if new_provider.api_key is None:
            if new_provider.base_url != old_provider.base_url and old_provider.api_key:
                raise ValueError("更换语音 API 地址时请填写新密钥，或清除原密钥")
            new_provider.api_key = old_provider.api_key
    for gender in ("male", "female"):
        key = getattr(config, gender + "_reference")
        if key and refs.get(key).gender != gender:
            raise ValueError("参考音色的男女声分类与设置不符，请重新选择")
    row = await session.get(Setting, "dubbing.providers")
    if row is None:
        row = Setting(key="dubbing.providers", value={}, is_secret=True)
        session.add(row)
    row.value, row.is_secret = config.model_dump(), True
    await session.commit()
    return public_config(config)


_voice_cache: dict[str, tuple[float, list]] = {}


async def voices(config, language):
    DubbingConfig.language(language)
    if config.provider == "api":
        return []
    if local.is_reference(config):
        return [{"id": r.id, "name": r.name, "gender": r.gender, "language": language}
                for r in refs.list_references() if r.text.strip()]
    if config.provider == "local":
        return [{"id": key, "name": name, "gender": gender, "language": language}
                for key, name, gender in local.SPEAKERS]
    cached = _voice_cache.get(config.proxy)
    if not cached or time.monotonic() - cached[0] > 3600:
        try:
            import edge_tts
            rows = await asyncio.wait_for(edge_tts.list_voices(proxy=config.proxy or None), 25)
        except Exception:
            raise ValueError("无法获取在线音色，请检查网络或语音代理设置后重试") from None
        _voice_cache[config.proxy] = (time.monotonic(), rows)
    else:
        rows = cached[1]
    return [
        {"id": v["ShortName"], "name": v.get("FriendlyName", v["ShortName"]),
         "gender": v["Gender"].lower(), "language": v["Locale"]}
        for v in rows if v["Locale"].lower() == language.lower()
        and v["Gender"] in {"Male", "Female"}
    ]


def validate_reference_language(config, language):
    if config.provider == "local":
        local.validate_language(language)
    if (config.provider == "reference"
            and config.reference_api.model == "FunAudioLLM/CosyVoice2-0.5B"
            and language.split("-")[0] not in {"zh", "en", "ja", "ko"}):
        raise ValueError("当前参考音色模型支持中文、英语、日语和韩语，请更换目标语言或语音服务")


async def resolve_voices(config, language, male="", female="", genders=None):
    if config.provider == "local":
        local.validate_language(language)
        local.ensure_ready(config.local_model)
    if local.is_reference(config):
        validate_reference_language(config, language)
        provider = config.reference_api
        if config.provider == "reference" and not provider.api_key and urlsplit(provider.base_url).hostname not in {
            "localhost", "127.0.0.1", "::1"
        }:
            raise ValueError("请先在配音设置中配置参考音色服务的 API Key")
        resolved = {}
        for gender in (genders if genders is not None else ("male", "female")):
            key = (male if gender == "male" else female) or getattr(config, gender + "_reference")
            if not key:
                raise ValueError("请先录制或导入音频，并设置默认" + ("男声" if gender == "male" else "女声"))
            reference = refs.get(key)
            if reference.gender != gender:
                raise ValueError("所选参考音色的男女声分类不匹配")
            if not reference.text.strip():
                raise ValueError("参考音频原文尚未识别完成，请完成识别或校对后再使用")
            resolved[gender] = key
        return resolved
    if config.provider == "local":
        resolved = {}
        for gender in (genders if genders is not None else ("male", "female")):
            key = (male if gender == "male" else female) or getattr(config, "local_" + gender + "_voice")
            if not any(key == speaker and gender == g for speaker, _, g in local.SPEAKERS):
                raise ValueError("请选择本地模型支持的男女声音色")
            resolved[gender] = key
        return resolved
    if config.provider == "api":
        return {"male": male or config.male_voice or "onyx",
                "female": female or config.female_voice or "nova"}
    rows = await voices(config, language)
    resolved = {}
    for gender, selected in (("male", male), ("female", female)):
        candidates = [v for v in rows if v["gender"] == gender]
        configured = getattr(config, gender + "_voice") if language == config.target_language else ""
        selected = selected or configured
        if selected and not any(v["id"] == selected for v in candidates):
            raise ValueError(f"所选音色不属于目标语言的{'男' if gender == 'male' else '女'}声，请重新选择")
        if not candidates:
            raise ValueError("此语言暂未提供完整的男女声音色，请选择其他语言或语音 API")
        resolved[gender] = selected or candidates[0]["id"]
    return resolved


async def synthesize(config, text, voice, output):
    """Bound the response, deadline and errors; never expose upstream credentials."""
    if not text.strip() or len(text) > 4000:
        raise ValueError("单句配音文字须为 1–4000 字")
    if config.provider == "local":
        await local.synthesize(config, text, voice, output)
        return
    try:
        async with asyncio.timeout(180):
            if config.provider == "edge":
                import edge_tts
                communicate = edge_tts.Communicate(text, voice, proxy=config.proxy or None)
                size = 0
                with output.open("wb") as file:
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            size += len(chunk["data"])
                            if size > 32 * 1024**2:
                                raise ValueError("语音响应过大，请缩短该句文字")
                            file.write(chunk["data"])
            else:
                provider = config.reference_api if config.provider == "reference" else config.api
                body = {"model": provider.model, "input": text, "response_format": "mp3"}
                if config.provider == "reference":
                    body["references"] = [refs.payload(voice)]
                else:
                    body["voice"] = voice
                local_host = urlsplit(provider.base_url).hostname in {"localhost", "127.0.0.1", "::1"}
                async with httpx.AsyncClient(timeout=150, follow_redirects=False,
                                             trust_env=not local_host) as client:
                    async with client.stream(
                        "POST", provider.base_url + "/audio/speech", headers=auth(provider),
                        json=body,
                    ) as response:
                        if response.status_code >= 400:
                            raise ValueError(f"语音 API 返回 HTTP {response.status_code}，请检查地址、模型、密钥和额度")
                        size = 0
                        with output.open("wb") as file:
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > 32 * 1024**2:
                                    raise ValueError("语音响应过大，请缩短该句文字")
                                file.write(chunk)
            if not output.is_file() or output.stat().st_size < 100:
                raise ValueError("语音服务未返回有效音频")
    except ValueError:
        raise
    except Exception:
        raise ValueError("语音合成失败，请检查服务连接、音色和代理设置后重试") from None
