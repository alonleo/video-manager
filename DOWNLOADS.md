# 视频链接下载

## 使用

打开侧边栏 **链接下载**（/downloads），粘贴一个视频播放页链接或含单个链接的分享文本。选择清晰度上限、容器和目标分类，然后开始下载。

- 支持 yt-dlp 的站点解析器，包括抖音、B站、YouTube 和其他第三方站点，以及可被 Generic 解析器识别的视频直链。
- 每次处理单个视频，合集/多分P链接仅取首条；不提供整套合集批量下载或直播录制。
- 清晰度可选最佳可用、2160p、1440p、1080p（默认）、720p、480p。所选值是上限，无法提升源视频画质，未知分辨率的直链仍允许下载。
- 容器支持自动、MP4、MKV。使用 FFmpeg 合并或换封装，保留编码；遇到不兼容编码时可改选自动或 MKV。
- 默认保存在 downloads/online，并自动建立本地存储源“链接下载”；也可选择已有本地分类。
- 视频下载后探测画面、音频、时长和尺寸，登记本地来源、原始链接与站点标识，生成封面，并进入影像库。
- 下载记录及任务中心可查看进度、传输速度、预计时间、错误、取消、重试及播放入口。进度条保留最后一段用于合并和入库，不等同于单个分片的字节进度。
- 完成和失败复用右下角任务通知；完成后刷新影像库、存储源与分类。
- 离开页面不会停止后台任务。失败和取消保留临时文件，显式重试时可由 yt-dlp 续传；是否能够续传取决于源站 Range/分片支持和链接有效期。

## 下载设置

在 **设置 → 链接下载**（/settings/downloads）中配置：

1. 下载代理：例如 http://127.0.0.1:7897。该地址指后端所在电脑，留空则使用后端进程的网络环境。不支持在代理 URL 内嵌账号密码。
2. 浏览器 Cookie：默认不读取。可选择后端电脑上的 Chrome、Chromium、Firefox、Edge 或 Brave；需要先在对应浏览器访问/登录目标站点。浏览器配置、系统密钥环或容器环境可能影响 Cookie 提取。
3. 浏览器配置目录与 Linux 密钥环：多配置浏览器可指定 Default、Profile 1 或绝对路径。自动识别基于桌面环境；浏览器实际使用的密钥环可能不同。本机 Chrome 已验证使用 GNOME Keyring，后台会补充同一用户的 D-Bus 会话环境；Linux 依赖 secretstorage 已加入锁文件。
4. 检测 Cookie：使用当前表单检查，不必先保存。仅返回未过期 Cookie 数量、各站点数量和解密失败提示，不返回 Cookie 内容；读取成功不等同于站点登录有效。
5. cookies.txt：也可填写后端电脑上 Netscape 格式 Cookie 文件的绝对路径。与浏览器 Cookie 二选一。

设置保存在本机数据库，只影响之后新建/重试的任务，不改变正在运行的任务。Cookie 内容不会返回到网页或写入任务元数据。Cookie 文件使用独立临时副本，任务退出时删除副本，不回写原始 Cookie 文件。

## 安装与更新

项目核心依赖已加入 yt-dlp[default]，包含 YouTube 所需的 yt-dlp-ejs。另需 FFmpeg，以及 Node 或 Deno 脚本运行环境。当前设备使用 Node 24；应用会显式启用检测到的 Node。

安装：

    cd /home/leo/projects/video-manage/backend
    uv sync --extra dev --extra subtitles
    uv run playwright install chromium

抖音直接详情接口失败时，会使用 Playwright 打开正常视频页面，读取同一视频的详情响应，再交回 yt-dlp 下载。优先使用与 Playwright 匹配的 Chromium，未安装时尝试系统 Chrome/Chromium。浏览器回退重新读取原始 Cookie 来源，避免失败的接口请求覆盖内存 Cookie；仅向抖音页面提供抖音 Cookie。解析限时 45 秒，退出/取消后关闭临时浏览器，不复用或修改用户浏览器配置，不自动处理验证码或绕过观看权限。

更新站点解析器：

    cd /home/leo/projects/video-manage/backend
    uv lock --upgrade-package yt-dlp --upgrade-package yt-dlp-ejs
    uv sync --extra dev --extra subtitles
    cd ..
    bash scripts/restart.sh

2026-10-04 验证安装版本：yt-dlp 2026.8.19、yt-dlp-ejs 0.8.0。版本锁记录在 backend/uv.lock。站点接口会变化；更新解析器后仍可能需要登录、有效 Cookie、代理或相应观看权限。DRM 视频不支持下载。

## 文件与任务行为

下载使用参数数组启动独立子进程，不执行链接中的 shell 文本；不读取用户全局 yt-dlp 配置或第三方插件目录。视频输入仅接受公共 HTTP/HTTPS 地址的标准端口，提交和执行时检查域名解析结果。此检查不等同于对所有上游 CDN/重定向请求的网络隔离；本项目仍按个人本地服务使用。

临时文件在 work/url-downloads/<随机任务标识>/，目录权限为 0700；只有探测到有效视频后才发布到保存目录。同名文件不会被覆盖；已入库的同来源、同大小、同快速指纹内容会复用既有视频。

目录扫描/整理与最终入库共享文件锁。视频入库和任务完成在同一事务中提交；取消若先于提交生效会回滚新文件和索引。取消会终止整个下载进程组，包括合并子进程。旧进程退出前拒绝复用它的临时目录。

默认最多同时下载两个任务；沿用 EXPORT_MAX_FILE_MB（默认 20 GB）、EXPORT_TIMEOUT_SEC（默认 6 小时）及 PLAYBACK_MIN_FREE_MB（默认 512 MB）保护下载过程。超限、空间不足或超时会明确报错。临时文件为续传保留，不会自动无限重试；不再需要的失败/取消任务可在确认任务停止后手动清理对应临时目录。

完成任务不提供浏览器二次下载按钮，视频已保存到后端电脑，可直接在影像库播放或通过原有基本操作进行管理。

## 接口

- GET /api/downloads：最近 50 条下载记录，含经筛选的输入元数据。
- POST /api/downloads：url、quality、container、storage_id（可选）、path。
- POST /api/downloads/{job_id}/retry：仅重试失败或取消的下载，使用当前下载设置。
- GET /api/downloads/status：引擎版本、FFmpeg、脚本环境和默认目录。
- GET/PUT /api/downloads/settings：代理及 Cookie 来源、browser_profile、browser_keyring 设置。
- POST /api/downloads/cookies/check：接收当前设置，返回脱敏检测结果；30 秒超时。
- POST /api/jobs/{job_id}/cancel：复用通用取消接口。
- 下载任务类型为 download_url；后台使用既有 run_job、Redis/arq 队列及 Job 表，无需数据库迁移。

## 验证

- backend/tests/test_downloads.py：20 项隔离测试，通过真实 yt-dlp 子进程下载本地生成的视频，覆盖 MP4、MKV、元数据入库、通知、取消、重试、同名保护、输入校验、队列不可用及取消时事务回滚。
- 原有任务通知、视频操作、字幕、目录分类回归：81 项通过。
- 前端 TypeScript/Vite 生产构建通过。
- scripts/downloads-audit.mjs：浏览器请求使用模拟任务，不向正式库提交下载；验证参数、取消/重试、播放链接、设置保存、桌面与移动布局、运行时错误及可访问性。

站点验证记录（注明解析与实际下载范围）：
- B站：成功解析用户之前提供的视频播放页。
- 抖音（修复后真实下载）：用户失败链接 https://v.douyin.com/brYo9Bh3sAw/ 已通过原有重试接口下载完成，任务 #30，1080p、约 190 秒，已入库。问题包含 Linux 密钥环识别、上游详情接口 403，以及失败请求覆盖内存 Cookie；已补充浏览器回退和针对性诊断。
- YouTube：直连超时；显式使用本机 HTTP 代理 http://127.0.0.1:7897 后成功解析 Big Buck Bunny 视频。正式下载设置仍由用户选择，不自动改写代理配置。YouTube 未做真实完整下载。

参考：
- https://github.com/yt-dlp/yt-dlp
- https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md
- https://github.com/MapleShaw/yt-dlp-downloader-skill

实现参考清晰度选择、续传和显式 Cookie 配置方式；没有安装或执行第三方 skill 的 shell 脚本。

修复回归：新增浏览器 Cookie 作用域、过期过滤、密钥环/配置目录、原始 Cookie 重读、错误脱敏、cookies.txt 不回写和抖音回退边界测试。下载设置浏览器检查覆盖 Cookie 检测、密钥环保存及移动布局。

本次修复最终验证：40 项后端测试通过，13 项浏览器检查通过，TypeScript/Vite 构建与 Ruff 检查通过。任务 #30 实际文件经 ffprobe 确认为 1920×1080、HEVC/AAC、190.659 秒、24,697,505 字节；影像库视频 #473 可正常读取。
