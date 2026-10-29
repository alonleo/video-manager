# Windows 本地部署说明（帧藏 · video-manager）

本仓库原本只面向 Linux（Debian 13）。这份说明记录在 Windows 11 上跑通全部功能
所需的改动与运维方式。部署位置：`F:\WorkSapce\note-work\video-manager`。

## 架构

| 组件 | 跑在哪 | 说明 |
|---|---|---|
| API（uvicorn :8420，托管前端 dist） | Windows | `backend/.venv`（Python 3.14） |
| arq worker × 3（主 / 增强 / 移动） | Windows | 同上 |
| Redis :6399、OpenList :5244 | WSL2 Docker | `docker compose up -d`，随 systemd/docker 自启 |

WSL2 使用 **mirrored 网络**（`~/.wslconfig`），Windows 直接用 `127.0.0.1` 访问 WSL
里的容器端口；一个隐藏的 `wsl --exec sleep` 保活进程防止 WSL VM 空闲关机把容器带走。

## 一键启停

```bash
./scripts/start-windows.sh   # 保活 + 容器 + API + workers + 健康检查
./scripts/stop-windows.sh    # 只停 API/workers，容器保留
```

日志在 `.run/*.log`。

## 为 Windows 做的代码改动

1. **`backend/sitecustomize.py`（新增）** — Python 自动加载的兼容层：
   - 让 configparser 用 UTF-8 读 `alembic.ini`（Windows 上 `encoding="locale"`
     恒为 cp936，含中文注释的 ini 直接 UnicodeDecodeError）；
   - 用 msvcrt 实现等价 `fcntl.flock` 的 `fcntl` 模块（扫描/移动的跨进程锁，
     拿不到锁抛 BlockingIOError，调用方回 409）。仅 `sys.platform == "win32"` 生效。
2. **`backend/app/services/downloads.py`** — `terminate()` 补上 Windows 分支：
   POSIX 用 `os.killpg`，Windows 用 `process.terminate()/kill()`（与
   `subtitles.py` 已有的写法一致）。`start_new_session=True` 在 Windows 上无害，
   保持原样。

## 环境差异（.env）

- `DATABASE_URL=sqlite+aiosqlite:///F:/WorkSapce/note-work/video-manager/data/video_manage.db`
  （Windows 需要绝对路径/正斜杠）
- 启动进程时需带 `NO_PROXY=127.0.0.1,localhost`，否则 httpx 对 OpenList 的
  健康检查会被系统代理（Clash 7897）劫持返回 502（start-windows.sh 已处理）

## WSL 侧一次性配置

- `apt install docker.io docker-compose-v2`；`systemctl enable docker`
- 拉镜像代理：`/etc/systemd/system/docker.service.d/http-proxy.conf` 指向
  `http://127.0.0.1:7897`（mirrored 网络下可达 Windows 上的 Clash）
- `~/.wslconfig`：`networkingMode=mirrored`、`vmIdleTimeout=4294967295`
- 注意：**不要**在 WSL 里再装原生 redis-server，会与 compose 的 6399 端口冲突

## 已验证

`GET /api/health` → `status=ok`，database/redis/openlist/ffmpeg 全 true；
`vaapi=false` 属预期（Windows 无 `/dev/dri`，自动降级 CPU 软编）。
前端已 `pnpm build`，由 API 在 <http://127.0.0.1:8420> 静态托管。
