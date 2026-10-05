"""Run in an isolated child process. Output counts only, never cookie values."""

import json
import re
import sys

from yt_dlp import YoutubeDL

from app.services.download_config import DownloadConfig


class CookieLogger:
    def __init__(self):
        self.failed = 0
        self.errors = False

    def debug(self, message):
        self.info(message)

    def info(self, message):
        match = re.search(r"\((\d+) could not be decrypted\)", message)
        if match:
            self.failed = int(match[1])

    def warning(self, message):
        if any(s in message.lower() for s in ("decrypt", "keyring", "secretstorage")):
            self.errors = True

    def error(self, message):
        self.errors = True


def inspect_cookies(config: DownloadConfig) -> dict:
    logger = CookieLogger()
    opts = {"logger": logger, "quiet": True}
    if config.cookie_file:
        opts["cookiefile"] = config.cookie_file
    elif config.cookies_browser:
        opts["cookiesfrombrowser"] = (
            config.cookies_browser,
            config.browser_profile or None,
            config.browser_keyring.upper()
            if config.browser_keyring
            and sys.platform == "linux"
            and config.cookies_browser != "firefox"
            else None,
            None,
        )
    else:
        return {
            "ok": False,
            "message": "请先选择浏览器或 Cookie 文件。",
            "total": 0,
            "failed": 0,
            "sites": {},
        }
    # Do not use a context manager: it would save and mutate an input cookies.txt.
    ydl = YoutubeDL(opts)
    try:
        cookies = [c for c in ydl.cookiejar if not c.is_expired()]
        sites = {}
        for label, domain in [
            ("抖音", "douyin.com"),
            ("B站", "bilibili.com"),
            ("YouTube", "youtube.com"),
        ]:
            sites[label] = sum(
                c.domain.lstrip(".") == domain or c.domain.endswith("." + domain) for c in cookies
            )
        warning = logger.failed or logger.errors
        message = f"读取到 {len(cookies)} 条未过期 Cookie。"
        if warning:
            message += "部分 Cookie 无法解密，请核对配置目录与密钥环。"
        message += "读取成功不代表站点登录或验证一定有效。"
        return {
            "ok": bool(cookies) and not warning,
            "message": message,
            "total": len(cookies),
            "failed": logger.failed,
            "sites": sites,
        }
    finally:
        ydl.params.pop("cookiefile", None)
        ydl.close()


def main():
    try:
        result = inspect_cookies(DownloadConfig.model_validate_json(sys.stdin.read()))
    except Exception:
        result = {
            "ok": False,
            "message": "无法读取 Cookie。请检查文件格式、浏览器配置目录与桌面密钥环。",
            "total": 0,
            "failed": 0,
            "sites": {},
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
