"""Windows 兼容层。

本项目原本只在 Linux 上跑，Windows 上有两处硬伤，这里集中兜住，
让同一份代码在两个平台都能跑起来：

1. **configparser 读 UTF-8 配置**：alembic 读取 `alembic.ini` 时写死了
   ``encoding="locale"``（alembic/util/compat.py）。Windows 上
   ``locale.getencoding()`` 恒为 ANSI 代码页 cp936，UTF-8 模式也改不了它，
   于是含中文注释的 alembic.ini 直接抛 UnicodeDecodeError。

2. **fcntl 文件锁**：后端用 ``fcntl.flock`` 做跨进程写锁（扫描 vs. 移动整理）。
   fcntl 是 POSIX 独有的，Windows 上没有。这里用 msvcrt 的字节区间锁实现
   等价语义：LOCK_EX / LOCK_SH / LOCK_NB / LOCK_UN，拿不到锁时抛
   BlockingIOError（调用方据此回 409）。

非 Windows 平台不做任何事，走系统自带的 fcntl。
"""

import sys

if sys.platform == "win32":
    import configparser

    _orig_read = configparser.ConfigParser.read

    def _read_utf8(self, filenames, encoding=None):
        if encoding == "locale":
            encoding = "utf-8"
        return _orig_read(self, filenames, encoding=encoding)

    configparser.ConfigParser.read = _read_utf8

    import msvcrt
    import time as _time
    import types

    LOCK_SH = 1
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def _flock(handle, operation):
        """等价 fcntl.flock：阻塞/非阻塞排他锁 + 解锁。"""
        fd = handle.fileno()
        if operation & LOCK_UN:
            handle.seek(0)
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return
        exclusive = bool(operation & LOCK_EX)
        nonblocking = bool(operation & LOCK_NB)
        mode = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(fd, mode, 1)
                return
            except OSError as exc:
                if not nonblocking:
                    _time.sleep(0.1)
                    continue
                raise BlockingIOError(f"resource temporarily unavailable: {exc}") from exc

    _fcntl = types.ModuleType("fcntl")
    _fcntl.LOCK_SH = LOCK_SH
    _fcntl.LOCK_EX = LOCK_EX
    _fcntl.LOCK_NB = LOCK_NB
    _fcntl.LOCK_UN = LOCK_UN
    _fcntl.flock = _flock
    sys.modules["fcntl"] = _fcntl
