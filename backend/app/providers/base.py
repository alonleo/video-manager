"""存储源抽象（计划书 §3.3）。

API 与 worker 只依赖这个接口，三种实现（local / openlist / url）对上层透明。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class PlaybackMode(StrEnum):
    """播放三态（计划书 §5）。"""

    direct = "direct"      # 浏览器可直接解码，走 HTTP Range
    remux = "remux"        # 仅换容器/转音频，视频流 copy
    transcode = "transcode"  # 视频编码不兼容，需 VAAPI 转码


@dataclass
class RemoteEntry:
    """目录扫描得到的一个条目。"""

    name: str
    path: str            # provider 内的路径
    is_dir: bool
    size: int | None = None
    modified: str | None = None
    provider_file_id: str | None = None


@dataclass
class StreamTarget:
    """解析出的可播放目标。

    - kind="file"：本地文件，后端用 FileResponse 直接吐（支持 Range）
    - kind="redirect"：把浏览器 302 到 url（网盘直链）
    - kind="ffmpeg"：后端用 ffmpeg 以 url 为输入做 remux/转码
    """

    kind: str
    url: str | None = None
    local_path: str | None = None
    headers: dict[str, str] | None = None


@runtime_checkable
class StorageProvider(Protocol):
    """所有存储源的统一接口。"""

    async def test(self) -> tuple[bool, str]:
        """连通性/认证自检，返回 (是否可用, 说明)。"""
        ...

    async def list_dir(self, path: str) -> list[RemoteEntry]:
        """列出目录内容（非递归）。"""
        ...

    async def stat(self, path: str) -> RemoteEntry | None:
        """取单个条目信息。"""
        ...

    async def resolve_stream_url(self, path: str) -> StreamTarget:
        """解析出可播放/可读取的目标。

        本地实现返回 kind="file"；网盘实现返回 kind="redirect" 或 "ffmpeg"
        并给出带签名的直链。
        """
        ...

    async def open_range(
        self, path: str, start: int, end: int | None = None
    ) -> AsyncIterator[bytes]:
        """按 Range 读取字节，用于抽音频等场景。"""
        ...
