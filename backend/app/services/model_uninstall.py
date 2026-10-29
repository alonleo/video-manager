"""Remove model assets without touching media, tracks, settings or reference voices."""

import asyncio
import shutil

import httpx
from sqlalchemy import or_, select

from app.config import settings
from app.models import Job, JobState
from app.services import subtitle_config, subtitle_translation_local, dubbing_local


class ModelBusy(ValueError):
    pass


async def uninstall(engine, model, session):
    # Be conservative: dubbing can also consume recognition and translation models.
    active = await session.scalar(select(Job.id).where(
        Job.state.in_([JobState.queued, JobState.running]),
        or_(Job.kind.in_(["subtitle_generate", "subtitle_translate", "subtitle_model_download"]),
            Job.kind.like("dub_%")),
    ).limit(1))
    if active is not None:
        raise ModelBusy(f"AI 任务 #{active} 正在排队或运行，请完成或取消任务后再卸载模型")
    if engine == "ollama":
        config = await subtitle_config.read_config(session)
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.request("DELETE", config.ollama_url + "/api/delete", json={"model": model})
                if response.status_code != 404:
                    response.raise_for_status()
        except httpx.HTTPError:
            raise ValueError("Ollama 模型卸载失败，请确认本机服务已启动后重试") from None
    else:
        if engine == "whisper":
            root = subtitle_config.model_dir(model)
        elif engine == "translation":
            root = subtitle_translation_local.model_dir(model)
        elif engine == "dubbing":
            root = dubbing_local.model_dir(model)
        else:
            raise ValueError("未知模型类型")
        models_root = (settings.data_dir / "models").resolve()
        if root.is_symlink() or not root.resolve().is_relative_to(models_root):
            raise ValueError("模型目录不安全，拒绝卸载")
        try:
            if root.exists():
                await asyncio.to_thread(shutil.rmtree, root)
        except OSError:
            raise ValueError("模型文件卸载失败，请检查目录权限后重试") from None
    return {"model": model, "message": "模型已卸载"}
