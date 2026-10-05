"""yt-dlp CLI with a project-owned, narrowly scoped Douyin fallback."""

from yt_dlp import main
from yt_dlp.extractor.tiktok import DouyinIE
from yt_dlp.utils import ExtractorError


class DouyinBrowserIE(DouyinIE, plugin_name="VideoManage"):
    def _real_extract(self, url):
        try:
            return super()._real_extract(url)
        except ExtractorError as exc:
            if "Fresh cookies" not in str(exc):
                raise
        from app.services.douyin_browser import extract_from_browser

        self.to_screen("详情接口不可用，正在通过浏览器解析视频…")
        return extract_from_browser(self, self._match_id(url))


if __name__ == "__main__":
    main()
