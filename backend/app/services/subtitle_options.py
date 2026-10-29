"""Per-video subtitle task parameters shared by Settings and the CC menu."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models import Setting
from app.services.subtitle_config import WHISPER_MODELS, SubtitleConfig


class SubtitleTaskOptions(BaseModel):
    auto_translate: bool = False
    asr_provider: Literal["local", "api"] | None = None
    asr_model: str = Field(default="", max_length=150)
    language: str = Field(default="auto", pattern=r"^(auto|[a-z]{2,3})$")
    translation_provider: Literal["local", "ollama", "api"] | None = None
    translation_model: str = Field(default="", max_length=150)
    source_language: str = Field(default="auto", pattern=r"^[a-zA-Z][a-zA-Z0-9-]{0,19}$")
    target_language: str = Field(default="zh-CN", pattern=r"^[a-zA-Z][a-zA-Z0-9-]{0,19}$")

    @model_validator(mode="after")
    def valid_models(self):
        self.asr_model = self.asr_model.strip()
        self.translation_model = self.translation_model.strip()
        if self.asr_model and not self.asr_provider:
            raise ValueError("语音模型需要指定处理方式")
        if self.translation_model and not self.translation_provider:
            raise ValueError("翻译模型需要指定处理方式")
        if self.asr_provider == "local" and self.asr_model and self.asr_model not in WHISPER_MODELS:
            raise ValueError("不支持的 Whisper 模型")
        if self.translation_provider == "local" and self.translation_model not in {"", "nllb-600m-int8"}:
            raise ValueError("不支持的本地翻译模型")
        if not self.asr_model:
            self.asr_provider = None
        if not self.translation_model:
            self.translation_provider = None
        return self


async def read_options(session, video_id: int) -> SubtitleTaskOptions:
    row = await session.get(Setting, f"subtitle.options.{video_id}")
    return SubtitleTaskOptions.model_validate(row.value if row else {})


async def save_options(session, video_id: int, options: SubtitleTaskOptions):
    key = f"subtitle.options.{video_id}"
    row = await session.get(Setting, key)
    if not row:
        row = Setting(key=key, value={}, is_secret=False)
        session.add(row)
    row.value = options.model_dump()
    await session.commit()
    return row.value


def generate_parameters(config: SubtitleConfig, options: SubtitleTaskOptions):
    model = config.asr_model if config.asr_provider == "local" else config.asr_api.model
    if options.asr_provider == config.asr_provider and options.asr_model:
        model = options.asr_model
    return {"provider": config.asr_provider, "model": model, "language": options.language}


def translation_parameters(config: SubtitleConfig, options: SubtitleTaskOptions):
    model = (
        config.local_translation_model if config.translation_provider == "local"
        else config.ollama_model if config.translation_provider == "ollama"
        else config.translation_api.model
    )
    if options.translation_provider == config.translation_provider and options.translation_model:
        model = options.translation_model
    return {
        "provider": config.translation_provider,
        "model": model,
        "source_language": options.source_language,
        "target_language": options.target_language,
    }
