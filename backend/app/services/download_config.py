"""Validated inputs and local configuration for URL downloads."""

from __future__ import annotations

import asyncio
import importlib.metadata
import ipaddress
import re
import shutil
import socket
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Setting
from app.services.directory_categories import relative_directory

KEY = "url_downloads_v1"


def normalize_url(value: str) -> str:
    matches = re.findall(r"https?://[^\s<>\"，。]+", value.strip(), re.I)
    if len(matches) != 1:
        raise ValueError("请粘贴一个 HTTP/HTTPS 视频链接，或包含一个链接的分享文本")
    value = matches[0].rstrip(".,;!?)）】」")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("视频链接格式不正确") from exc
    host = (parsed.hostname or "").lower()
    if not host or parsed.username or parsed.password:
        raise ValueError("链接必须包含有效域名，且不能包含用户名或密码")
    if port is not None and port not in (80, 443):
        raise ValueError("视频链接仅支持标准 HTTP/HTTPS 端口")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("请使用公开视频网站链接")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("不支持本机或内网视频链接")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


async def validate_public_host(url: str) -> None:
    host = urlsplit(url).hostname
    try:
        rows = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM), 10
        )
    except (OSError, TimeoutError) as exc:
        raise HTTPException(422, "无法解析视频站点域名，请检查链接与网络") from exc
    if not rows or any(not ipaddress.ip_address(row[4][0]).is_global for row in rows):
        raise HTTPException(422, "不支持解析到本机或内网的视频地址")


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=8192)
    quality: Literal["best", "2160", "1440", "1080", "720", "480"] = "1080"
    container: Literal["auto", "mp4", "mkv"] = "auto"
    storage_id: int | None = Field(default=None, gt=0)
    path: str = Field(default="", max_length=4096)

    _url = field_validator("url")(normalize_url)
    _path = field_validator("path")(relative_directory)


class DownloadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proxy: str = Field(default="", max_length=2048)
    cookies_browser: Literal["", "chrome", "chromium", "firefox", "edge", "brave"] = ""
    cookie_file: str = Field(default="", max_length=4096)
    browser_profile: str = Field(default="", max_length=4096)
    browser_keyring: Literal["", "gnomekeyring", "kwallet", "kwallet5", "kwallet6", "basictext"] = (
        ""
    )

    @field_validator("browser_profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        value = value.strip()
        if "::" in value or any(ord(c) < 32 for c in value):
            raise ValueError("浏览器配置目录格式无效")
        return value

    @field_validator("proxy")
    @classmethod
    def validate_proxy(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        p = urlsplit(value)
        if p.scheme not in {"http", "https", "socks5", "socks5h"} or not p.hostname:
            raise ValueError("代理地址应以 http://、https:// 或 socks5:// 开头")
        if p.username or p.password or p.query or p.fragment:
            raise ValueError("代理地址不支持内嵌密码、查询参数或片段")
        try:
            _ = p.port
        except ValueError as exc:
            raise ValueError("代理端口无效") from exc
        return value

    @field_validator("cookie_file")
    @classmethod
    def validate_cookie_file(cls, value: str) -> str:
        value = value.strip()
        if value and (not Path(value).is_absolute() or any(ord(c) < 32 for c in value)):
            raise ValueError("Cookie 文件请填写运行后端电脑上的绝对路径")
        return value


async def read_config(session) -> DownloadConfig:
    row = await session.get(Setting, KEY)
    return DownloadConfig.model_validate(row.value if row else {})


def engine_status() -> dict:
    from app.config import settings

    try:
        version = importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {
        "installed": version is not None,
        "version": version,
        "ffmpeg": bool(shutil.which(settings.ffmpeg_bin)),
        "javascript": bool(shutil.which("node") or shutil.which("deno")),
        "default_directory": str(settings.downloads_dir / "online"),
    }
