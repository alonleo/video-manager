"""去重指纹（计划书 §3.4）。

不做全量 hash —— 1TB+ 读一遍太慢。改用「大小 + 首尾各 1MB」的快速指纹，
文件改名/移动/跨盘拷贝都能命中。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import xxhash

log = logging.getLogger(__name__)

SAMPLE_SIZE = 1024 * 1024  # 1MB
# 超过此大小的文件不做全量 hash
FULL_HASH_MAX_BYTES = 200 * 1024 * 1024


def quick_hash_file(path: Path) -> str | None:
    """本地文件快速指纹：xxh3(size || 首1MB || 尾1MB)。

    小文件直接全量读（不足 2MB 时首尾会重叠，这里做去重处理）。
    """
    try:
        size = path.stat().st_size
    except OSError as e:
        log.warning("无法读取文件大小 %s: %s", path, e)
        return None

    h = xxhash.xxh3_64()
    h.update(size.to_bytes(8, "little"))

    try:
        with path.open("rb") as f:
            head = f.read(SAMPLE_SIZE)
            h.update(head)

            if size > SAMPLE_SIZE:
                # 尾部采样：小文件时与头部重叠，跳过以免重复计入
                if size > SAMPLE_SIZE * 2:
                    f.seek(-SAMPLE_SIZE, 2)
                else:
                    f.seek(SAMPLE_SIZE)
                tail = f.read(SAMPLE_SIZE)
                h.update(tail)
    except OSError as e:
        log.warning("无法读取文件内容 %s: %s", path, e)
        return None

    return h.hexdigest()


def quick_hash_parts(size: int, head: bytes, tail: bytes) -> str:
    """供网盘/远程源使用：调用方负责取到首尾字节。"""
    h = xxhash.xxh3_64()
    h.update(size.to_bytes(8, "little"))
    h.update(head)
    h.update(tail)
    return h.hexdigest()


def quick_hash_meta(size: int, path_hint: str) -> str:
    """无法读取内容的兜底指纹（网盘 file_id 缺失时用）。

    仅基于大小与归一化路径，可靠性低于内容指纹，但足以避免同一路径重复入库。
    """
    h = xxhash.xxh3_64()
    h.update(size.to_bytes(8, "little"))
    h.update(normalize_title(path_hint).encode("utf-8"))
    return h.hexdigest()


def full_hash_file(path: Path) -> str | None:
    """全量 blake3/xxh3，仅对小文件或用户手动触发时使用。"""
    try:
        if path.stat().st_size > FULL_HASH_MAX_BYTES:
            return None
    except OSError:
        return None

    h = xxhash.xxh3_128()
    try:
        with path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
    except OSError as e:
        log.warning("全量 hash 失败 %s: %s", path, e)
        return None
    return h.hexdigest()


# ------------------------------------------------------------------ 标题归一化

# 常见需要剥离的噪声：分辨率、编码、来源站、集数
_NOISE_PATTERNS = [
    r"\b(480|720|1080|1440|2160|4k|8k)[pi]\b",
    r"\b(h\.?26[45]|hevc|avc|av1|vp9|x26[45])\b",
    r"\b(bluray|bdrip|web-?dl|webrip|hdtv|dvdrip|remux)\b",
    r"\b(aac|ac3|dts|flac|mp3|opus)\b",
    r"\b\d+(\.\d+)?(gb|mb|kb)\b",
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)
# 集数/分P 标记，如 S01E02 / EP03 / 第4集 / [05]
_EPISODE_RE = re.compile(
    r"(s\d{1,2}e\d{1,3}|ep?\d{1,3}|第\s*\d{1,3}\s*[集话期]|\[\d{1,3}\]|\(\d{1,3}\))",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


def normalize_title(name: str) -> str:
    """把文件名/标题归一化成可比较的形式，用于跨源模糊去重。"""
    s = Path(name).stem if "." in name else name
    s = _EPISODE_RE.sub(" ", s)
    s = _NOISE_RE.sub(" ", s)
    s = _PUNCT_RE.sub("", s)
    return s.lower().strip()


def is_probably_same(
    *,
    dur_a: int | None,
    dur_b: int | None,
    size_a: int | None,
    size_b: int | None,
    title_a: str,
    title_b: str,
    duration_tolerance_ms: int = 2000,
    size_tolerance: float = 0.05,
) -> bool:
    """跨源模糊判重（计划书 §3.4 的「疑似重复」，只提示不自动合并）。

    时长与大小都在容差内，或归一化标题完全相同，则认为是同一视频。
    """
    if dur_a and dur_b and abs(dur_a - dur_b) <= duration_tolerance_ms:
        if size_a and size_b:
            base = max(size_a, size_b)
            if base and abs(size_a - size_b) / base <= size_tolerance:
                return True
        else:
            # 缺大小时仅靠时长且标题一致才判重
            return normalize_title(title_a) == normalize_title(title_b)

    na, nb = normalize_title(title_a), normalize_title(title_b)
    return bool(na) and na == nb
