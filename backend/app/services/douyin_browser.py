"""Render Douyin's normal video page when its direct detail endpoint fails."""

import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp.utils import ExtractorError


def browser_cookies(jar) -> list[dict]:
    cookies = []
    for cookie in jar:
        domain = cookie.domain.lstrip(".")
        if domain != "douyin.com" and not domain.endswith(".douyin.com"):
            continue
        if not cookie.name or cookie.is_expired():
            continue
        if any(ord(c) < 32 for c in cookie.name + cookie.value):
            continue
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
                "secure": bool(cookie.secure),
                "expires": min(cookie.expires, time.time() + 400 * 86400)
                if cookie.expires and cookie.expires > 0
                else -1,
            }
        )
    return cookies


def matching_detail(data, video_id):
    detail = data.get("aweme_detail") if isinstance(data, dict) else None
    if (
        isinstance(detail, dict)
        and str(detail.get("aweme_id")) == video_id
        and isinstance(detail.get("video"), dict)
        and detail["video"].get("play_addr")
    ):
        return detail
    return None


def configured_browser_cookies(extractor):
    from yt_dlp.cookies import load_cookies

    # Failed API/redirect requests may replace site cookies. Start the browser
    # from the configured source, not that failed HTTP session.
    source = extractor._downloader.params
    if source.get("cookiefile") or source.get("cookiesfrombrowser"):
        jar = load_cookies(
            source.get("cookiefile"), source.get("cookiesfrombrowser"), extractor._downloader
        )
    else:
        jar = extractor._downloader.cookiejar
    return browser_cookies(jar)


def extract_from_browser(extractor, video_id):
    from playwright.sync_api import Error, sync_playwright
    from playwright.sync_api import TimeoutError as BrowserTimeout

    target = f"https://www.douyin.com/video/{video_id}"
    detail = None
    launch = {"headless": True, "timeout": 20000}
    executable = next(
        (
            path
            for name in ("google-chrome", "chromium", "chromium-browser")
            if (path := shutil.which(name))
        ),
        None,
    )
    if executable:
        launch["executable_path"] = executable
    proxy = extractor.get_param("proxy")
    if proxy:
        launch["proxy"] = {"server": proxy.replace("socks5h://", "socks5://", 1)}

    def on_response(response):
        nonlocal detail
        parsed = urlsplit(response.url)
        if (
            parsed.hostname != "www.douyin.com"
            or parsed.path != "/aweme/v1/web/aweme/detail/"
            or response.status != 200
        ):
            return
        try:
            detail = matching_detail(response.json(), video_id) or detail
        except (Error, ValueError):
            pass

    try:
        with sync_playwright() as playwright:
            if Path(playwright.chromium.executable_path).is_file():
                launch["executable_path"] = playwright.chromium.executable_path
            browser = playwright.chromium.launch(**launch)
            try:
                context = browser.new_context(viewport={"width": 1366, "height": 900})
                context.add_cookies(configured_browser_cookies(extractor))
                page = context.new_page()
                page.on("response", on_response)
                deadline = time.monotonic() + 45
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=25000)
                except BrowserTimeout:
                    pass
                while detail is None and time.monotonic() < deadline:
                    page.wait_for_timeout(250)
                if detail is None:
                    # Do not solve CAPTCHA or bypass account access restrictions.
                    raise ExtractorError(
                        "VM_DOUYIN_BROWSER_NO_VIDEO: 浏览器未取得该视频详情，请在 Chrome "
                        "中确认视频可播放并完成站点要求的登录或验证，然后重新检测 Cookie。",
                        expected=True,
                    )
                user_agent = page.evaluate("navigator.userAgent")
                for cookie in context.cookies(["https://www.douyin.com/"]):
                    extractor._set_cookie(
                        cookie["domain"],
                        cookie["name"],
                        cookie["value"],
                        path=cookie["path"],
                        secure=cookie["secure"],
                    )
                info = extractor._parse_aweme_video_app(detail)
                info["http_headers"] = {"User-Agent": user_agent, "Referer": target}
                return info
            finally:
                browser.close()
    except Error:
        # Browser errors may include URLs or user paths; expose a fixed diagnostic.
        raise ExtractorError(
            "VM_DOUYIN_BROWSER_UNAVAILABLE: 无法启动抖音解析浏览器。请安装 Chrome，"
            "或在后端运行 uv run playwright install chromium。",
            expected=True,
        ) from None
