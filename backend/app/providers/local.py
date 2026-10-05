"""本地文件系统存储源（外接盘 / NAS 挂载点）。"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.providers.base import RemoteEntry, StreamTarget
from app.services.photos import PHOTO_EXTENSIONS

log = logging.getLogger(__name__)

CHUNK_SIZE = 512 * 1024


class LocalProvider:
    """type=local 的存储源。config: {"root": "/mnt/videos"}"""

    def __init__(self, config: dict):
        self.root = Path(config.get("root", "")).expanduser()
        # 允许扫描时额外过滤扩展名
        exts = config.get("extensions")
        self.extensions = (
            {e.lower() if e.startswith(".") else f".{e.lower()}" for e in exts}
            if exts
            else settings.video_ext_set | PHOTO_EXTENSIONS
        )

    # ---------------------------------------------------------- 自检

    async def test(self) -> tuple[bool, str]:
        if not self.root:
            return False, "未配置 root 路径"
        if not self.root.exists():
            return False, f"路径不存在（外接盘可能未挂载）: {self.root}"
        if not self.root.is_dir():
            return False, f"不是目录: {self.root}"
        if not os.access(self.root, os.R_OK):
            return False, f"无读取权限: {self.root}"
        try:
            # 触发一次真实读取，避免仅 stat 通过但挂载已失效
            next(os.scandir(self.root), None)
        except OSError as e:
            return False, f"读取目录失败: {e}"
        return True, f"可访问: {self.root}"

    def guard_root_available(self) -> None:
        """扫描前的安全闸（计划书 §7.1 的保护逻辑）。

        外接盘未挂载时 root 会变成空目录。若此时继续扫描并把「文件消失」
        写回数据库，会把整库 source 误标为不可用。因此这里硬性拒绝。
        """
        if not self.root or not self.root.exists() or not self.root.is_dir():
            raise RuntimeError(
                f"存储根目录不可用，已跳过扫描以防误删索引: {self.root}"
            )

    # ---------------------------------------------------------- 遍历

    def _is_video(self, name: str) -> bool:
        if name.startswith("."):
            return False
        return Path(name).suffix.lower() in self.extensions

    def to_rel(self, abs_path: Path) -> str:
        """绝对路径 → 相对 root 的路径（统一用 / 分隔）。"""
        return abs_path.relative_to(self.root).as_posix()

    def to_abs(self, rel_path: str) -> Path:
        """相对路径 → 绝对路径，并阻断越界访问。"""
        candidate = (self.root / rel_path.lstrip("/")).resolve()
        root_resolved = self.root.resolve()
        if not candidate.is_relative_to(root_resolved):
            raise ValueError(f"路径越界: {rel_path}")
        return candidate

    def walk_videos(self, sub_path: str = "") -> list[RemoteEntry]:
        """同步递归遍历视频文件。由 worker 用 to_thread 调用。

        返回的 path 是相对 root 的 posix 路径。
        """
        base = self.to_abs(sub_path) if sub_path else self.root
        entries: list[RemoteEntry] = []

        for dirpath, dirnames, filenames in os.walk(base):
            # 跳过隐藏目录与常见非视频目录
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d.lower() not in {"#recycle", "@eadir", "lost+found"}
            ]
            for fn in filenames:
                if not self._is_video(fn):
                    continue
                abs_p = Path(dirpath) / fn
                if abs_p.is_symlink():
                    continue
                try:
                    st = abs_p.stat()
                except OSError as e:
                    log.warning("跳过无法 stat 的文件 %s: %s", abs_p, e)
                    continue
                entries.append(
                    RemoteEntry(
                        name=fn,
                        path=self.to_rel(abs_p),
                        is_dir=False,
                        size=st.st_size,
                        modified=datetime.fromtimestamp(
                            st.st_mtime, tz=UTC
                        ).isoformat(),
                    )
                )
        return entries

    async def list_dir(self, path: str) -> list[RemoteEntry]:
        def _scan() -> list[RemoteEntry]:
            base = self.to_abs(path) if path else self.root
            out: list[RemoteEntry] = []
            with os.scandir(base) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    out.append(
                        RemoteEntry(
                            name=e.name,
                            path=self.to_rel(Path(e.path)),
                            is_dir=e.is_dir(),
                            size=None if e.is_dir() else st.st_size,
                            modified=datetime.fromtimestamp(
                                st.st_mtime, tz=UTC
                            ).isoformat(),
                        )
                    )
            return out

        return await asyncio.to_thread(_scan)

    async def stat(self, path: str) -> RemoteEntry | None:
        abs_p = self.to_abs(path)
        try:
            st = abs_p.stat()
        except OSError:
            return None
        return RemoteEntry(
            name=abs_p.name,
            path=path,
            is_dir=abs_p.is_dir(),
            size=None if abs_p.is_dir() else st.st_size,
            modified=datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
        )

    # ---------------------------------------------------------- 播放解析

    async def resolve_stream_url(self, path: str) -> StreamTarget:
        """本地文件直接交给 Starlette FileResponse，原生支持 Range/206。"""
        abs_p = self.to_abs(path)
        if not abs_p.exists():
            raise FileNotFoundError(f"文件不存在: {abs_p}")
        return StreamTarget(kind="file", local_path=str(abs_p))

    async def open_range(
        self, path: str, start: int, end: int | None = None
    ) -> AsyncIterator[bytes]:
        abs_p = self.to_abs(path)
        remaining = None if end is None else end - start + 1

        def _open():
            f = abs_p.open("rb")
            f.seek(start)
            return f

        f = await asyncio.to_thread(_open)
        try:
            while True:
                want = CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining)
                if want <= 0:
                    break
                chunk = await asyncio.to_thread(f.read, want)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(f.close)
