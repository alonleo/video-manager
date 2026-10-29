"""Private, atomic local speech cache; also imported by the isolated TTS runner."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

SCHEMA = 1
MAX_BYTES = 2 * 1024**3
MAX_AGE = 30 * 86400


def digest(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def key(identity):
    return hashlib.sha256(
        json.dumps(
            {"schema": SCHEMA, **identity},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def valid(path):
    path = Path(path)
    marker = path.with_suffix(".json")
    try:
        if path.is_symlink() or marker.is_symlink():
            return False
        info = json.loads(marker.read_text())
        size = path.stat().st_size
        return (
            100 <= size <= 32 * 1024**2 and size == info["size"] and digest(path) == info["sha256"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def restore(path, output):
    path, output = Path(path), Path(output)
    if not valid(path):
        return False
    shutil.copyfile(path, output)
    os.utime(path, None)
    return True


def publish(source, destination):
    source, destination = Path(source), Path(destination)
    size = source.stat().st_size
    if not 100 <= size <= 32 * 1024**2:
        raise ValueError("本地语音文件大小无效")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.parent / ("." + uuid4().hex + ".tmp")
    marker = temporary.with_suffix(".json.tmp")
    try:
        shutil.copyfile(source, temporary)
        marker.write_text(json.dumps({"size": size, "sha256": digest(temporary)}))
        temporary.replace(destination)
        marker.replace(destination.with_suffix(".json"))
    finally:
        temporary.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)


def prune(root, keep=()):
    """Evict old clips; recent/in-flight batches are protected from eviction."""
    root, keep = Path(root), {str(p) for p in keep}
    now, rows = time.time(), []
    for path in root.glob("*.mp3"):
        try:
            stat = path.stat()
            rows.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            continue
    total = sum(size for _, size, _ in rows)
    for modified, size, path in sorted(rows):
        if str(path) in keep or now - modified < 3600:
            continue
        if now - modified < MAX_AGE and total <= MAX_BYTES:
            continue
        path.unlink(missing_ok=True)
        path.with_suffix(".json").unlink(missing_ok=True)
        total -= size
    for path in root.glob(".*.tmp"):
        try:
            if now - path.stat().st_mtime > 3600:
                path.unlink(missing_ok=True)
        except OSError:
            pass
