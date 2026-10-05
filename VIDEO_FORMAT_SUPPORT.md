> 当前网页已切换为独立 WebAssembly 解码播放器，实际支持范围与限制见 [WASM_PLAYER.md](WASM_PLAYER.md)。下文记录的是旧版浏览器播放加服务端转换方案，不能视为新播放器的全格式支持承诺。

# 全部 17 种视频格式兼容播放

更新时间：2026-10-05。项目 `/home/leo/projects/video-manage`，访问 http://127.0.0.1:8420 。

## 使用

所有已导入的视频直接打开即可；新文件在「存储源」扫描后进入视频库。默认扩展名白名单包含下表 17 项，大小写不敏感。自定义存储源白名单仍需包含目标扩展名。

系统根据媒体实际编码选择直连、换封装或转码，不通过改后缀伪装兼容性。首次需要转换时显示进度，完整文件生成后可播放、拖动与续播。后续复用缓存，原文件保持不变。浏览器无法解码直连或换封装结果时自动切到兼容转码，也可手动点击「兼容播放」；当前位置和刷新后的模式都会保留。

## 覆盖范围

| 扩展名 | 本轮验证的实际内容 | 默认处理 |
| --- | --- | --- |
| MP4 | H.264/AAC；另测 HEVC、10-bit H.264 | 兼容编码直连，其他转码 |
| MKV | H.264/AC3 | 视频复制，音频转 AAC，生成 MP4 |
| AVI | MPEG-4 Part 2/MP3；另测 H.264/MP3 | MPEG-4 转码；H.264 视频复制 |
| MOV | ProRes/PCM；另测 H.264/AAC | ProRes 转码；H.264 换封装 |
| WMV | WMV2/WMA2（ASF 容器） | 转码 |
| FLV | Sorenson Spark/MP3 | 转码 |
| WebM | VP8/Vorbis、VP9/Opus | 兼容编码直连，可切换兼容转码 |
| M4V | MP4 容器、H.264/AAC | 直连；原始 MPEG-4 码流按实际编码转码 |
| MPG | MPEG-1/MP2 | 转码 |
| MPEG | MPEG-2/MP2 | 转码 |
| TS | H.264/AAC | 换封装 |
| M2TS | MPEG-2/AC3 | 转码 |
| MTS | H.264、无音轨 | 换封装 |
| RMVB | RealMedia RV20/AC3，以及真实 RV40/Cook | 转码 |
| 3GP | H.263/AAC | 转码 |
| VOB | MPEG-2/AC3 | 转码 |
| OGV | Theora/Vorbis | 转码 |

「支持 17 种」指这些本地文件类型均已接入兼容播放流程；每个扩展名可容纳多种编码。实际解码能力由当前 FFmpeg 构建决定，损坏、加密、尚在写入或无法解码的文件会明确报错。文件路径必须在服务器上可读；本地挂载的 NAS 目录适用，未挂载的 OpenList/URL 来源仍属于后续接入工作。

## 实现与保护

- 保守直连判断同时检查真实容器和扩展名，避免把 AVI/MKV 内的 H.264 误当作浏览器可直放内容。
- 非直连统一生成 H.264 8-bit 4:2:0 / AAC / MP4；安全 H.264 复制视频流，其他视频转码。复制路径可保留 AAC-LC；其他音轨和强制兼容模式转为 AAC-LC、双声道、48 kHz。
- 处理第一条非封面视频轨道及第一条可选音轨；无音轨视频可播放。嵌入字幕、多音轨选择及 HDR 专用色调映射不属于本次实现。
- 准备时重新探测源媒体，避免直接依赖旧库里的编码信息。输出验证编码、时长和大小，检查源文件未变化后原子发布。
- `GET playback-info`、`POST playback-retry` 和 `GET stream` 支持 `compatible=true`。兼容模式使用独立处理策略的缓存身份，原直连仍可用。
- 解码错误自动兼容转换至多一次；缓存 URL 出错自动刷新也有次数限制，避免循环加载。手动重试始终保留。
- 使用完整 MP4 及 HTTP Range 206/416，支持拖动、条件 Range、稳定 ETag 和断点续播。
- 沿用 `cache/ts-playback-v1/` 的历史目录名，复用已有 TS 缓存，目录现已服务全部本地格式。
- `CACHE_MAX_GB` 限制缓存总量；保留 `PLAYBACK_MIN_FREE_MB` 磁盘余量。仅清理本功能拥有、近期未使用且未受本进程传输保护的缓存。
- 同一文件请求去重；跨进程文件锁串行准备。超时、失败或关闭服务时回收子进程、清理半成品，缓存淘汰后可重新生成。

当前先完成整片处理再开始播放，大文件首次准备会等待。使用 CPU 编码且 H.264 编码器 2 线程；VAAPI、HLS 边处理边播尚未接入。没有修改用户 `.env`，不需要数据库迁移。

## 验证结果

- **51 项后端测试通过**：原有 TS 19 项全部回归；新增 17 扩展名真实样本、特殊编码、完整扫描、强制兼容及进度保存、附带封面识别、真实 RV40/Cook 等检查。
- **129 项浏览器检查通过**：23 个有效媒体样本均在真实 Chrome 播放，跳转到第 7 秒、保存进度、刷新续播；每个样本验证真实 Range 206。
- 23 个有效样本使用不同内容，隔离扫描产生 23 个独立视频 ID，避免去重导致格式测试误用同一个主来源。
- 浏览器验证手动兼容切换保留位置和刷新保留模式；专门注入一次无效直连媒体响应，确认自动转码后能继续播放。其余播放使用真实后端和媒体文件，没有伪造 API 或成功播放响应。
- 129 项浏览器检查由主矩阵 126 项及连续失败恢复专项 3 项组成；验证自动重试次数受限后，手动重新加载仍能恢复实际播放。
- 损坏 AVI 显示稳定错误并可手动重试。桌面、手机和错误状态的 3 次 WCAG A/AA 自动检查无违规，未捕获浏览器异常。
- 后端静态检查和前端 TypeScript / Vite 构建通过。
- 原片哈希不变；H.264 换封装前后的解码帧哈希一致。容量限制、超时、取消、过期缓存等原有保护测试全部保留。

测试使用 `.run/format-validation/` 内的独立 SQLite 数据库、生成样本及测试缓存，未写入用户视频库或观看记录。浏览器脚本会检查数据库路径，拒绝操作正式库。

结果：`.run/format-validation/pytest.log`、`lint.log`、`build.log`、`browser.log`、`browser-results.json`、`recovery-results.json`、`recovery.log`、`fixtures.json`、`wmv-desktop.png`、`wmv-mobile.png`、`format-error-mobile.png`。

真实 RV40/Cook 样本来自 FFmpeg 官方测试样本库：https://samples.ffmpeg.org/real/VC-RV40/Sample.rmvb 。仅作兼容性验收，未将其导入正式库。较小的 `20040506.rm` 样本本身被截断，已验证会被完整性检查拦截，未把它算作正常播放样本。

## 复测

```bash
cd /home/leo/projects/video-manage/backend
REAL_RMVB_SAMPLE=/home/leo/projects/video-manage/.run/format-validation/sources/complete-rv40.rmvb \
  .venv/bin/pytest -q tests
```

不设置 `REAL_RMVB_SAMPLE` 时，仅跳过真实 RV40 外部样本检查；生成式 17 格式测试和其余回归仍运行。

`scripts/prepare-format-validation.py` 可生成隔离浏览器测试库。浏览器验收脚本为 `scripts/format-playback-audit.mjs`，默认连接 8422；需要先用隔离库的 `DATABASE_URL`、`DATA_DIR`、`CACHE_DIR` 启动测试 API。`FORMAT_AUDIT_URL`、`PLAYWRIGHT_MODULE`、`CHROMIUM_PATH` 可覆盖测试连接和运行时位置。测试结束后停止 8422 服务。

## 备份

修改前备份：`.run/all-formats-before-20261005-0402.tar.gz`，含应用源码、前端构建、已有测试、脚本和说明。回退前先保存后续改动，再按需还原并重启服务。
