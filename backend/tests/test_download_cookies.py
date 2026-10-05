"""Regressions for browser cookie diagnostics and Douyin fallback isolation."""

import json
import time
from http.cookiejar import Cookie
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from yt_dlp.utils import ExtractorError

from app.services import download_cookies
from app.services.cookie_probe import inspect_cookies
from app.services.douyin_browser import browser_cookies, matching_detail
from app.services.download_config import DownloadConfig
from app.services.downloads import command, error_message


def cookie(domain=".douyin.com", name="sessionid", **kwargs):
    args = dict(
        version=0,
        name=name,
        value="private-value",
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=1,
        expires=int(time.time()) + 3600,
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
    )
    args.update(kwargs)
    return Cookie(**args)


def test_only_valid_unexpired_douyin_cookies_reach_browser():
    cookies = browser_cookies(
        [
            cookie(),
            cookie("www.douyin.com", "other"),
            cookie("notdouyin.com"),
            cookie("douyin.com.attacker.test"),
            cookie(name=""),
            cookie(expires=1),
            cookie(value="bad\nvalue"),
        ]
    )
    assert len(cookies) == 2
    assert all(c["secure"] is True for c in cookies)


def test_detail_must_match_requested_video():
    detail = {"aweme_id": "123", "video": {"play_addr": {"url_list": ["https://cdn.test/a"]}}}
    assert matching_detail({"aweme_detail": detail}, "123") == detail
    for value in [None, [], {"aweme_detail": detail}, {"aweme_detail": {"aweme_id": "456"}}]:
        assert matching_detail(value, "456") is None


def test_explicit_profile_and_keyring_are_separate_from_url(monkeypatch, tmp_path):
    monkeypatch.setattr(download_cookies.sys, "platform", "linux")
    config = DownloadConfig(
        cookies_browser="chrome",
        browser_keyring="gnomekeyring",
        browser_profile="/home/example/Profile 1",
    )
    args = command(
        {"url": "https://www.douyin.com/video/123", "quality": "1080", "container": "auto"},
        config,
        tmp_path,
    )
    assert args[2] == "app.services.downloader_cli"
    assert args[args.index("--cookies-from-browser") + 1] == (
        "chrome+gnomekeyring:/home/example/Profile 1"
    )
    assert args[-2:] == ["--", "https://www.douyin.com/video/123"]


def test_profile_cannot_inject_container_syntax():
    with pytest.raises(ValidationError):
        DownloadConfig(browser_profile="Default::other")


def test_existing_session_bus_is_preserved(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/custom/user/bus")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    env = download_cookies.download_environment()
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/custom/user/bus"
    assert env["XDG_CURRENT_DESKTOP"] == "GNOME"


def test_cookie_file_probe_never_rewrites_or_exposes_values(tmp_path):
    source = tmp_path / "cookies.txt"
    original = (
        "# Netscape HTTP Cookie File\n"
        ".douyin.com\tTRUE\t/\tTRUE\t2000000000\tsessionid\tPRIVATE_SECRET\n"
    )
    source.write_text(original)
    result = inspect_cookies(DownloadConfig(cookie_file=str(source)))
    assert result["sites"]["抖音"] == 1
    assert result["ok"] is True
    assert "PRIVATE_SECRET" not in json.dumps(result)
    assert source.read_text() == original


@pytest.mark.parametrize(
    ("logs", "message"),
    [
        (
            ["WARNING: cannot decrypt v11 cookies: no key found", "ERROR: HTTP Error 403"],
            "解密失败",
        ),
        (["ERROR: [Douyin] Fresh cookies are needed"], "并不一定是未登录"),
        (["WARNING: cannot decrypt", "ERROR: VM_DOUYIN_BROWSER_NO_VIDEO"], "浏览器解析未取得"),
        (["ERROR: VM_DOUYIN_BROWSER_UNAVAILABLE"], "解析浏览器未就绪"),
    ],
)
def test_diagnostics_identify_failure_without_raw_secrets(logs, message):
    result = error_message(logs + ["https://cdn.example/private?token=SECRET"])
    assert message in result
    assert "SECRET" not in result
    assert "https://" not in result


def test_douyin_fallback_is_only_for_upstream_detail_failure(monkeypatch):
    from app.services import douyin_browser
    from app.services.downloader_cli import DouyinBrowserIE

    original_class = DouyinBrowserIE.__wrapped__
    fallback = Mock(return_value={"id": "123"})
    monkeypatch.setattr(douyin_browser, "extract_from_browser", fallback)
    instance = DouyinBrowserIE()
    monkeypatch.setattr(instance, "to_screen", lambda *a: None)
    upstream = Mock(side_effect=ExtractorError("Fresh cookies are needed", expected=True))
    monkeypatch.setattr(original_class, "_real_extract", upstream)
    assert instance._real_extract("https://www.douyin.com/video/123") == {"id": "123"}
    fallback.assert_called_once_with(instance, "123")
    fallback.reset_mock()
    upstream.side_effect = ExtractorError("Private video", expected=True)
    with pytest.raises(ExtractorError, match="Private video"):
        instance._real_extract("https://www.douyin.com/video/123")
    fallback.assert_not_called()


def test_browser_fallback_reloads_original_cookie_source(monkeypatch):
    from types import SimpleNamespace

    from app.services.douyin_browser import configured_browser_cookies

    downloader = SimpleNamespace(
        params={"cookiesfrombrowser": ("chrome", None, "GNOMEKEYRING", None)},
        cookiejar=[cookie(value="changed-by-failed-api")],
    )
    reload = Mock(return_value=[cookie(value="original-browser-value")])
    monkeypatch.setattr("yt_dlp.cookies.load_cookies", reload)
    result = configured_browser_cookies(SimpleNamespace(_downloader=downloader))
    assert result[0]["value"] == "original-browser-value"
    reload.assert_called_once_with(None, downloader.params["cookiesfrombrowser"], downloader)
    assert downloader.cookiejar[0].value == "changed-by-failed-api"
