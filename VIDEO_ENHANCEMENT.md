# 画质增强：分辨率、码率与帧率

入口：播放器上方的「画质增强」按钮，使用与重命名、转换和移动相同的弹窗操作。

## 可用功能

- 分辨率：保持、2 倍、1080p、1440p、4K；保留显示比例，支持竖屏、旋转标记与非方形像素。
- 帧率：保持、24 / 25 / 30 / 48 / 50 / 60 / 120 fps。
- 运动补帧：FFmpeg minterpolate 运动补偿，生成中间画面，保留时长及播放速度。
- 快速重复帧：只调整输出帧率，清楚标明不会增加运动细节。
- 码率：自动质量或 1–150 Mbps 目标视频码率；实际平均码率由内容和编码器决定。
- 普通增强：Lanczos 放大；AI 超分：Real-ESRGAN 通用影像与 AnimeVideo 动漫模型。
- 编码：H.264 MP4，自动优先 Intel VAAPI，或手动选择软件编码。
- 可选起点生成最多 5 秒预览，原片与增强片段对比播放，按预览速度粗略估算完整处理时间。
- 完整结果支持下载、加入影像库、后台取消与完成通知；原文件保留。
- 完整输出保留全部音轨，并可保留原片文字字幕；图形字幕不写入 MP4。预览对比画面及音轨，字幕设置用于完整输出。

## 边界

普通放大和提高码率不能恢复丢失的细节。AI 会推测纹理，可能产生闪烁或失真；补帧在快速运动、遮挡、切镜处也可能产生伪影，建议先看预览。

输入与输出最高支持 4K 像素量，输出最高 120 fps。HDR 输入明确拒绝，避免转换时默默造成错误色彩。可变帧率输入按所选帧率（保持时采用原平均帧率）规范成恒定帧率，时间轴保持原时长。

高分辨率 AI 与高帧率补帧可能很慢。默认只执行一个增强任务；FFmpeg 使用 2 个处理线程，AI 使用 128 像素分块、最多 4 帧暂存。取消、超时、磁盘空间不足都会终止子进程并清理临时结果。

## 集成

- API：`backend/app/api/enhancement.py`
- 参数、尺寸、编码命令：`backend/app/services/enhancement.py`
- 后台任务：`backend/app/workers/enhancement.py`，由 `app.workers.enhancement_settings.WorkerSettings` 在独立 `arq:enhancement` 队列串行处理，不占用字幕和扫描的任务槽。
- 弹窗：`frontend/src/components/EnhanceVideoActions.tsx`
- 预览及结果操作：`frontend/src/components/EnhancementResult.tsx`
- 状态接入任务中心与全局完成通知。

接口：
- `GET /api/enhancement/capabilities`
- `POST /api/videos/{id}/enhance`
- `GET /api/jobs/{id}/enhancement-file?original=false&download=false`
- `POST /api/jobs/{id}/enhancement-import`

任务类型：`enhance_video`、`enhance_preview`。复用现有取消接口与通知游标。
增强文件位于 `downloads/enhanced/job-{id}/`。明确加入影像库的结果在 `downloads/enhanced-library/` 创建硬链接，无需重复占用视频数据空间；扫描该存储不会收录预览或未完成文件。

## 本地模型

运行文件与模型：`tools/realesrgan/`。
来源：
- https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/releases/tag/v0.2.0
- https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0

二进制使用 v0.2.0 Ubuntu 发行包；模型来自 20220424 Ubuntu 模型包。来源和 SHA-256 清单位于 `tools/realesrgan/manifest.json`，许可证随程序保存。

已在本机 Intel Arc (Meteor Lake) Vulkan 上完成实际模型推理，并验证 VAAPI ICQ/VBR 编码。

## 验证

- `backend/tests/test_enhancement.py`：9 项实际媒体与生命周期检查，覆盖运动中间帧、分辨率/帧率/时长、音轨/字幕、预览、下载 Range、去重导入、取消清理、原文件变化、队列失败、真实 AI 推理等。
- 原通知接口 8 项回归检查。
- `scripts/enhancement-audit.mjs`：30 项浏览器检查，包含真实 1080p/60 fps、120 fps、4K、竖屏、两种 AI 模型及补帧组合、原片保护、下载/入库/通知、移动端和无障碍。
- 隔离验证目录：`.run/enhancement-validation/`，使用 API 18426 与独立 Redis 16426，未修改用户视频。
- 构建日志、输出探测信息、桌面/手机截图与测试结果均在验证目录。

启动脚本已包含增强 worker；手动启动命令：`cd backend && .venv/bin/arq app.workers.enhancement_settings.WorkerSettings`。

部署确认：独立增强队列实测完成，生产 API 已加载新接口及两个模型，主 worker 保持运行；验证服务已关闭。
