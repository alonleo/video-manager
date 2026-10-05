# 帧藏 · 视频管理平台

个人自用的视频库：把散落在**本地外接盘 / NAS、阿里云盘、百度网盘、WebDAV、
B站/抖音/YouTube** 的视频统一索引到一个界面，支持浏览、搜索、在线播放、
AI 转录与内容总结。

## 界面与交互

2026-10-05 完成「帧藏」视觉与交互改造：影像库、存储源、任务中心和放映室采用统一设计，支持移动端、搜索筛选持久化、网格／列表切换、宽屏播放与无障碍键盘操作。

访问 http://127.0.0.1:8420 即可查看当前版本。详细改动、64 项验收结果、已知边界、截图位置与备份说明见 [设计品质验收记录](DESIGN_QUALITY_REVIEW.md)。

## 字幕与翻译

播放页已提供底部字幕、SRT/VTT 导入与校对、自动语音识别、字幕翻译、双语显示和字幕导出。新增「AI 设置」页面，可选择下载本地 Whisper 模型、连接 Ollama 下载翻译模型，或配置兼容的第三方语音与翻译 API。

当前已安装语音依赖并下载 Whisper Tiny。使用说明、网络代理、接口要求及验证边界见 [字幕与翻译说明](SUBTITLES.md)。字幕以播放轨道显示，原视频保持不变。

## 视频链接下载

侧边栏「链接下载」已接入 yt-dlp，支持抖音、B站、YouTube 及其他受支持站点。可选择清晰度、自动/MP4/MKV 容器和本地分类；支持进度、取消、显式重试、自动加入影像库和完成通知。代理与 Cookie 来源在「设置 → 链接下载」配置。站点登录与网络条件会影响可用性；使用方式、安装更新及实测边界见 [视频链接下载说明](DOWNLOADS.md)。

## 核心设计：引用式，视频本体不入库

本机磁盘远小于视频总量（1TB+ 视频 vs 447GB 可用空间），因此本平台
**日常索引不拷贝或搬运视频文件**（用户主动下载在线链接、重命名、移动视频或导出格式转换结果除外）：

- 数据库只存**元数据 + 视频所在位置的引用**（路径 / 网盘文件 ID / 原始链接）
- 本地只落盘小文件：封面缩略图（KB）、转录音频中间件（用完即删）、转码缓存（LRU 上限）
- 播放和分析时**按需现取现算**，直接从原始位置流式读取

## 当前进度

- ✅ **Phase 0** 基础设施
- ✅ **Phase 1** 本地库扫描 + 元数据探测 + 在线播放（HTTP Range）+ 进度续播
- 🟡 **Phase 2** 已支持全部 17 种本地视频扩展名的兼容播放；VAAPI / HLS 待接入
- 🟡 **Phase 3** 在线链接下载与自动入库已接入；网盘（OpenList）索引仍待完成
- ⏳ Phase 4 语音转录 + 全文检索
- ⏳ Phase 5 AI 总结 + 关键点提取

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python 3.13 + FastAPI | 单服务；重活丢给独立 arq worker 进程 |
| 数据库 | **SQLite**（WAL） | 零运维。API 与 worker 双进程靠 WAL + busy_timeout 协调 |
| 全文检索 | SQLite **FTS5 + trigram** | 中文子串检索 |
| 队列 | Redis + arq | 转录串行（`max_jobs=1`），转码/扫描并发 |
| 前端 | React 19 + TS + Vite + TanStack Query + Tailwind | 构建产物由 FastAPI 静态托管 |
| 媒体 | FFmpeg 7.x | 当前兼容播放使用 CPU H.264/AAC；VAAPI 硬编待接入播放链路 |
| 网盘网关 | OpenList（Docker） | 阿里云盘 / 百度网盘 / WebDAV 统一挂载 |

## 快速开始

### 1. 启动依赖服务

```bash
docker compose up -d          # Redis (6399) + OpenList (5244)
docker compose ps             # 确认都是 healthy
```

> 用项目专属的 Redis 实例（端口 6399），避让系统上那个带密码的 6379。

### 2. 后端

```bash
cd backend
uv sync                       # 核心依赖
uv sync --extra asr           # 可选：转录能力（faster-whisper / SenseVoice，体积大）
cp ../.env.example ../.env    # 按需修改
uv run alembic upgrade head   # 初始化数据库
```

### 3. 前端

```bash
cd frontend
pnpm install
pnpm build                    # 构建产物交给后端托管
```

### 4. 运行

生产模式（两个进程）：

```bash
cd backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8420     # API + 前端，监听局域网
uv run arq app.workers.settings.WorkerSettings              # 后台任务
```

开发模式（带热重载，前端在 5173）：

```bash
./scripts/dev.sh
```

首次启动请在服务器本机访问 <http://127.0.0.1:8420>，按页面提示初始化首台信任设备。
开发模式访问 <http://127.0.0.1:5173>。
API 文档在 `/docs`。

#### 局域网与信任设备

服务默认监听所有网卡，但所有影像与管理 API 在初始化后只接受信任设备：

1. 在服务器本机通过 `http://127.0.0.1:8420` 完成首台设备初始化。
2. 先为局域网访问配置最终的 HTTPS 地址，例如 `https://video.home`。
3. 在首台设备的「设置 → 信任设备」中生成二维码，并把“新设备访问地址”填写为该 HTTPS 地址。
4. 用新设备扫描二维码，核对两端的六位验证码，再由已有设备批准。
5. 设备丢失时，在同一页面撤销；其现有会话会立即失效。

除服务器本机的 `localhost` 外，浏览器只允许在 HTTPS 页面生成设备私钥。请在本服务前配置
局域网反向代理与可信证书，并将 HTTPS 请求转发到 `127.0.0.1:8420`；不要通过明文
`http://局域网IP:8420` 进行配对。代理应保留原始 `Host`，并正确传递客户端协议。

#### Android 客户端安装与配对

Android 客户端支持 Android 8.0（API 26）及以上版本。先在一台已信任的电脑浏览器中打开
「设置 → 信任设备」，选择「Android 应用」模式，并将访问地址填写为实际的局域网地址，
例如 `http://192.168.1.70:8420`，再生成二维码。

明文 HTTP 模式必须让手机通过局域网**直接访问帧藏服务**。后端依据真实连接来源限制私有网段，
不会信任可伪造的转发请求头；如果中间经过反向代理，必须改用 HTTPS 地址。即使直接访问，
HTTP 中的视频、照片、会话信息和操作内容仍可能被同一网络中的攻击者窃听或篡改，建议配置
可信 HTTPS。信任设备机制只负责设备身份与访问授权，**不等于网络加密**。

构建后的安装包位于仓库内 `artifacts/frame-archive-mobile-0.1.0.apk`。安装前可在项目根目录核对完整性：

```bash
cd artifacts
sha256sum --check frame-archive-mobile-0.1.0.apk.sha256
```

把 APK 传到手机后，在系统设置中允许 Chrome 或所用文件管理器“安装未知应用”，再由该应用打开
APK 并确认安装。2026 年直接侧载仍可使用；若设备启用了 Android 的高级“未验证开发者”限制，
必须由用户本人在系统设置中阅读提示并明确允许，帧藏不会也不应绕过系统保护。

安装后打开客户端，扫描电脑上生成的二维码；双方会显示同一个六位验证码。核对无误后，在已信任
设备上批准，手机将自动进入影像库。设备丢失或不再使用时，在「设置 → 信任设备」中撤销；客户端
会话随后失效。再次使用时，在手机端取消旧配对并重新扫码、核对验证码和批准。

发布脚本 `./scripts/build-android-apk.sh` 会在首次运行时创建本机唯一的稳定签名，并完成 release
测试、Lint、构建、签名校验和 SHA-256 文件生成。`android/signing/` 已被 Git 忽略，其中的 keystore
和密码文件不会提交。请把**整个 `android/signing/` 目录**安全备份；丢失或只恢复其中一个文件，
后续 APK 将不能覆盖安装已有版本，只能卸载后重新配对。

### 5. 添加视频

1. 打开「存储源」页 → 添加存储源（本地目录填绝对路径，如 `/mnt/videos`）
2. 点「检测」确认路径可访问
3. 点「扫描」或「浏览」选子目录后扫描
4. 回「视频库」查看，点封面进播放页

## 项目结构

```
backend/
  app/
    api/           # videos / playback / storages / jobs / misc 路由
    models/        # SQLAlchemy 模型（12 张表）
    providers/     # StorageProvider 抽象 + local 实现
    services/      # library(扫描入库) probe(ffprobe) playback(三态决策) dedup(去重)
    workers/       # arq 任务与队列
  alembic/         # 数据库迁移
frontend/
  src/
    api/           # 类型定义与 fetch 客户端
    pages/         # Library / Player / Storages / Jobs
    components/    # VideoCard / player/VideoPlayer
docker-compose.yml # Redis + OpenList
data/              # SQLite 库 + 封面（随项目走）
cache/ work/ downloads/   # 运行时产物，可清理/可指向大磁盘
```

## 视频格式与播放

已实现 **17 种扩展名的本地兼容播放**：

`MP4、MKV、AVI、MOV、WMV、FLV、WebM、M4V、MPG、MPEG、TS、M2TS、MTS、RMVB、3GP、VOB、OGV`。

扩展名只用于扫描；播放会结合实际容器、视频编码、音频编码及像素格式决定处理方式：

| 模式 | 触发条件 | 实现 |
|---|---|---|
| **direct** | 兼容的 MP4/M4V + H.264/AAC，或 WebM + VP8/VP9/AV1 与兼容音轨 | 原始文件 HTTP Range 直连 |
| **remux** | 视频为兼容 H.264，仅容器或音轨不兼容 | 复制视频流生成 MP4，按需转 AAC；视频画质不变 |
| **transcode** | HEVC、MPEG-1/2/4、WMV/VC-1、RealVideo、H.263、Theora、ProRes、H.264 10-bit 等 | FFmpeg CPU 转 H.264 8-bit 4:2:0 / AAC / MP4 |

需要处理的文件首次打开会显示排队／准备进度，生成完整 MP4 后可以播放、拖动和续播；再次打开复用缓存。大文件首次等待可能较长。原文件不改名、不覆盖、不搬移。

浏览器无法解码直连或换封装结果时，播放器自动尝试一次兼容转码；也可点击「兼容播放」。切换会保留播放位置，刷新页面仍保持兼容模式。转换失败后显示原因，可手动重新准备。

沿用 `cache/ts-playback-v1/` 存放通用播放缓存，以复用已完成的 TS 文件；计入 `CACHE_MAX_GB` 上限。当前 H.264 编码器限制 2 线程、转换任务串行执行。VAAPI 硬件加速、边处理边播放的 HLS 和未挂载的云端来源播放仍待实现。

验收：**51 项后端测试、129 项真实 Chrome 检查通过**，覆盖全部 17 种扩展名、23 个有效媒体样本、拖动续播、Range 请求、错误恢复及桌面／手机布局。详情见 [17 种视频格式支持说明](VIDEO_FORMAT_SUPPORT.md)；此前 TS 专项记录见 [TS 支持说明](TS_SUPPORT.md)。

## 安全保护

外接盘或 NAS 掉线时，挂载点会变成空目录。若此时照常扫描并把"文件消失"
写回数据库，会把整库来源误标为不可用。因此扫描前有两道闸：

1. 根目录不可用（不存在 / 非目录）→ **直接拒绝扫描**
2. 扫描结果为空 → **不做"标记消失"处理**

## 配置

所有配置项见 `.env.example`。关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `REDIS_URL` | `redis://127.0.0.1:6399/0` | 项目专属 Redis |
| `CACHE_MAX_GB` | `100` | 转码缓存硬上限，保护 447GB 磁盘 |
| `TRANSCRIBE_MAX_JOBS` | `1` | 转录串行（内存仅约 15GB 可用） |
| `VAAPI_DEVICE` | `/dev/dri/renderD128` | Intel 核显 |
| `LLM_*` | DeepSeek | OpenAI 兼容协议，只有文本上云 |

## 环境要求

- Debian 13 / Node 24 / Python 3.13 / uv / ffmpeg 7.x / Docker
- Intel 核显（可选，用于硬编转码）；无核显则回退 CPU 软编


## 视频基础操作

打开视频播放页，在播放器下方直接使用「重命名」和「转换格式」，没有单独的工具入口。

- **重命名**：可只改视频库显示名称，或勾选「同时重命名本地文件」。填写不含扩展名的新名称，保留原扩展名，同目录重名时拒绝覆盖；文件改名后同步更新来源索引。文件有正在处理的任务时，须先完成或取消任务。
- **转换格式**：支持 MP4、MKV、MOV、TS，使用 FFmpeg 流复制换封装，保留原音视频编码；不做转码压缩，也不提供裁剪、旋转翻转、变速、静音或音频提取。
- **处理范围**：当前支持已索引且可读取的本地文件；复制主视频和全部音轨，不携带内嵌字幕、数据流或附件。编码与目标容器不兼容时明确报错，可尝试 MKV，不会悄悄转码。
- **结果**：转换后台执行，播放页及任务中心显示进度、失败原因、取消按钮和下载链接；输出为新文件，原视频保持不变。结果保存在 `downloads/exports/job-任务编号/`，不会自动加入视频库，也不会自动删除已完成文件。
- **资源限制**：默认单文件上限 20 GB（`EXPORT_MAX_FILE_MB=20480`），超时 6 小时（`EXPORT_TIMEOUT_SEC=21600`），预留空间复用 `PLAYBACK_MIN_FREE_MB`。失败或取消时停止处理并清理临时文件。

接口：`POST /api/videos/{id}/rename`、`POST /api/videos/{id}/convert`、`GET /api/jobs/{id}/file`。转换进度和取消复用已有任务接口。

验证：`cd backend && .venv/bin/pytest -q tests/test_operations.py` 使用临时数据库和生成的视频验证文件操作，不修改已有视频。浏览器全流程验证由 `scripts/basic-actions-audit-server.py` 与 `scripts/basic-actions-audit.mjs` 在隔离的 8431 端口及专属队列运行。

## 目录分类与移动视频

影像库支持按视频父目录自动分类，已有索引立即生效。「新建分类」会创建真实的本地目录；在播放页点击「移动视频」，选择来源文件及目标分类即可整理。同名目录按存储源和完整路径区分，空目录也可作为分类。移动作为后台任务执行，提交后可离开页面，在任务中心查看进度，完成后右下角通知并刷新索引；保留收藏、标签、字幕记录及播放进度。跨盘复制先校验后清理原文件，同名文件不会覆盖。详见 [目录分类功能说明](DIRECTORY_CATEGORIES.md)。

## 标签管理

影像库的「管理标签」支持搜索、查看关联视频数量和删除标签本身。全局删除会移除所有视频与该标签的关联，保留视频文件、其他标签和观看进度；播放页的 × 仍仅取消单个视频的标签关联。详情及 6 项后端／26 项浏览器验收记录见 [标签管理说明](TAG_MANAGEMENT.md)。

## 字幕转换与替换原片

播放页「转换格式」支持 MKV、MP4、MOV、AVI、TS、M2TS，可不添加字幕、选择可开关字幕轨或烧录字幕。支持选择项目字幕、翻译双语及兼容的原视频字幕；可勾选校验后替换原视频，并保留隐藏的原片备份。详见 [字幕转换与替换说明](CONVERSION_SUBTITLES.md)。

## 列表位置恢复

从播放页返回或使用浏览器后退时，会恢复之前视频卡片所在的位置，并保留搜索、标签、目录分类、收藏、排序、页码及视图。支持刷新后返回和手机端；40 项浏览器验收通过。详见 [列表位置恢复说明](LIST_POSITION_RESTORATION.md)。

任务完成通知说明与验证记录：[TASK_NOTIFICATIONS.md](TASK_NOTIFICATIONS.md)。

画质增强、码率与帧率设置：[VIDEO_ENHANCEMENT.md](VIDEO_ENHANCEMENT.md)。手动启动时另运行 `cd backend && .venv/bin/arq app.workers.enhancement_settings.WorkerSettings`；开发和重启脚本已包含此进程。

后台移动视频：[BACKGROUND_MOVES.md](BACKGROUND_MOVES.md)。手动启动时另运行 `cd backend && .venv/bin/arq app.workers.move_settings.WorkerSettings`；开发和重启脚本已包含此进程。

## 删除本地视频源文件

视频详情页的「删除源文件」支持选择一个本地来源，查看完整路径、大小及影响，并勾选确认后永久删除。此操作不经过系统回收站，不删除同目录字幕、其他视频或父目录。

- 多来源视频：仅删除选中的物理文件及其重复索引，保留其他来源、收藏、标签、进度与字幕；自动选择剩余主来源。
- 最后一个来源：同步移除视频收录和关联记录。原有「移除收录」仍仅移除索引、保留文件。
- 处理任务、扫描、来源变化、符号链接、存储根目录外路径或文件被其他视频共同引用时拒绝删除。
- 接口先预览，再提交确认：GET / POST `/api/videos/{video_id}/sources/{source_id}/deletion`。POST 使用预览返回的 `confirmation` 和 `confirmed: true`，不接受任意文件路径。
- 删除前短暂暂存于原目录下的隐藏目录，数据库失败时恢复原文件；正常完成后清除暂存文件。如磁盘清理失败或进程异常退出，隐藏目录中的 `recovery.json` 保留原路径及影响信息，界面会报告可检测到的清理失败。恢复时应核对索引和原路径，不覆盖现有文件。

验证：`backend/tests/test_source_deletion.py`；隔离浏览器验证使用 `scripts/source-deletion-audit-server.py` 与 `scripts/source-deletion-audit.mjs`，只删除临时测试媒体。
