# 网页内独立解码播放器

2026-10-05。当前默认播放器使用固定版本 libmedia 1.3.1 的 WebAssembly 解码器，通过 Canvas/WebGL 显示视频、Web Audio 输出声音。关闭 WebCodecs、硬件解码和 MSE，不使用 HTML video 播放视频，也不自动切回原生播放。

## 使用和数据流

打开已有视频，等待媒体信息和所需解码组件加载，点击播放。服务端通过已有的视频 ID 和来源选择逻辑提供原文件 HTTP Range 字节流；解封装、音视频解码和同步在客户端执行，不为新播放器准备完整 MP4 文件，不修改原文件。组件从本项目提供，播放过程中无需访问外部 CDN。

保留播放/暂停、音量与静音、倍速、拖动、键盘控制、全屏、宽屏、字幕开关/语言/字号，以及观看进度保存和续播。字幕使用项目中的字幕轨道；字幕文字通过 React 文本节点显示。播放器离开页面时释放当前播放任务。

## 实际兼容范围

扩展名与编码组合必须分别验证。当前可播放的已测组合：

| 文件类型 | 验证编码 |
| --- | --- |
| MP4、M4V | H.264/AAC；MP4 另测 H.265 和 10-bit H.264 |
| MKV | H.264/AC3 |
| AVI | MPEG-4/MP3、H.264/MP3 |
| MOV | H.264/AAC |
| WebM | VP8/Vorbis、VP9/Opus、AV1/Opus |
| FLV | H.264/AAC |
| TS、MTS | H.264/AAC、H.264 无音轨 |
| M2TS、VOB | MPEG-2/AC3 |
| MPG、MPEG | MPEG-1/MP2、MPEG-2/MP2 |
| 3GP | H.263/AAC |

以下仍是明确的兼容缺口，不应宣称所有 17 种格式、所有编码均可独立播放：

- WMV/ASF 封装。
- RealMedia/RMVB，包括 RV20、RV40/Cook。
- Theora/Vorbis OGV。
- ProRes/PCM MOV。
- FLV1/Sorenson Spark FLV。它与 H.264 FLV 不同。
- 损坏、加密或尚未写完的媒体。

这些情况显示错误和重新加载按钮，不会暗中调用浏览器解码或启动后台转码。原有“转换格式”功能仍为独立的视频操作。

这是软件解码方案。测试样本主要是低分辨率短视频；不能据此保证大型文件、4K、高帧率或所有 HDR 内容流畅播放。当前未更改全局跨域隔离头，不具备 SharedArrayBuffer 的环境使用引擎的非共享内存路径。

## 实现

- `frontend/src/components/player/WasmMedia.ts`：控制接口、异步操作串行化、合并拖动请求、生命周期及错误处理。
- `VideoPlayer.tsx`、`PlayerControls.tsx`：自定义界面与独立引擎连接。
- `WasmCaptions.tsx`：字幕文字解析和时间同步。
- `GET /api/videos/{id}/playback-info?engine=wasm`：返回原文件路径，不进入准备缓存。
- `GET/HEAD /api/videos/{id}/stream?engine=wasm`：沿用现有来源和文件可用性检查，支持 Range 206/416。
- 旧 API 默认分支保留兼容性；新版网页显式选择 wasm，不使用旧缓存分支。
- `frontend/public/player-engine/libmedia-1.3.1/`：本地引擎、35 组解码模块的 baseline/atomic/SIMD 文件，以及重采样和变速组件。Wasm 与许可文件共约 66.8 MB；按需请求所需组件。
- `manifest.json`：上游固定引用、Git blob 校验、SHA-256 和本地补丁记录。
- `scripts/patch-player-engine.py`：固定版本的 MPEG 音频帧头偏移、无效帧头防护和采样时长修补；修订源码及上游许可随组件保留。
- 3GP 中缺少的 s263 映射由已探测的 H.263 元数据补充。TS 时间轴按流起始时间归一化。

## 构建

```bash
cd /home/leo/projects/video-manage/frontend
pnpm install --frozen-lockfile
cd ..
python3 scripts/setup-player-engine.py
cd frontend
pnpm build
```

安装脚本下载固定版本并校验内容，随后应用可重复的本地修补。构建前会验证组件是否完整；缺失组件时明确失败，避免发布黑屏播放器。

## 验证与限制

后端 `tests/test_wasm_playback.py` 使用临时数据库，验证原文件字节一致、Range、HEAD、离线文件、非法模式，以及从未调用 MP4 缓存准备。

浏览器测试在 `.run/wasm-validation/` 的独立数据库上进行。禁用原生 VideoDecoder、AudioDecoder 和 HTMLMediaElement.play 后，验证 Canvas 真正绘制、音频实际解码、拖动和播放控制，并检查未生成播放缓存。已知不支持的组合与损坏样本单独记录，不能算作播放通过。

本轮 26 个样例中，19 个有效样例通过播放、实际音视频解码和拖动检查，覆盖 14 种扩展名；6 个已知不支持组合和 1 个损坏文件正确报错。12 项播放控制检查通过。测试未调用浏览器原生视频播放，未产生转码缓存。

- 多格式结果：`.run/wasm-validation/matrix.json`。
- 控制、字幕、移动端与续播结果：`.run/wasm-validation/controls.json`。
- 测试脚本：`scripts/wasm-player-audit.mjs`、`scripts/wasm-controls-audit.mjs`。
- 测试服务：`scripts/wasm-audit-server.py`，仅监听本机，使用隔离数据库。
- Playwright 可通过 `PLAYWRIGHT_MODULE` 指向安装位置，Chrome 路径可由 `CHROMIUM_PATH` 指定。

测试未向正式视频库插入媒体，也未写入正式观看记录。修改前源码备份位于 `work/player-wasm-backup/before.tar.gz`；回退应逐文件比较，保留此后其他功能的改动。

上游项目：https://github.com/zhaohappy/libmedia
