#!/usr/bin/env bash
# Windows 一键启动（Git Bash 运行）：
#
#   ./scripts/start-windows.sh
#
# 组成：
#   1. WSL 保活进程（防止 WSL VM 空闲关机，Redis/OpenList 容器随之消失）
#   2. docker compose 依赖容器（Redis 6399 / OpenList 5244，跑在 WSL 里）
#   3. API（uvicorn :8420）+ 3 个 arq worker（主 / 画质增强 / 移动）
#
# 前置（首次）：
#   - WSL 发行版内已安装 docker + docker-compose-v2，docker.service 已 enable
#   - /etc/systemd/system/docker.service.d/http-proxy.conf 已配置代理（拉镜像用）
#   - cd backend && uv sync；cd frontend && pnpm install && pnpm build
#   - 根目录有 .env（参考 .env.example，DATABASE_URL 用绝对路径）

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
RUN="$ROOT/.run"
WSL_EXE="/c/Windows/System32/wsl.exe"
DISTRO="Ubuntu-26.04"
mkdir -p "$RUN"

wsl_root() { "$WSL_EXE" -d "$DISTRO" -u root -- bash -lc "$@"; }

# ---------- 1. WSL 保活 ----------
if ! tasklist //FI "IMAGENAME eq wsl.exe" 2>/dev/null | grep -q wsl.exe; then
  echo "==> 启动 WSL 保活进程"
  /c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command \
    "Start-Process -WindowStyle Hidden -FilePath 'C:\\Windows\\System32\\wsl.exe' \
     -ArgumentList '-d','$DISTRO','--exec','sleep','2147483647'"
  sleep 3
else
  echo "==> WSL 保活进程已在运行"
fi

# 等 WSL 里 systemd 拉起 docker（开机自启）
for i in $(seq 1 30); do
  if wsl_root "docker info >/dev/null 2>&1"; then break; fi
  sleep 1
  [ "$i" = 30 ] && { echo "✗ WSL docker 未就绪"; exit 1; }
done

# ---------- 2. 依赖容器 ----------
echo "==> 启动依赖容器（redis / openlist）"
wsl_root "cd /mnt/f/WorkSapce/note-work/video-manager && docker compose up -d" | tail -2
sleep 5

# ---------- 3. API + workers ----------
export NO_PROXY="127.0.0.1,localhost" no_proxy="127.0.0.1,localhost" PYTHONUTF8=1

start_one() {
  local name="$1"; shift
  if [ -f "$RUN/$name.pid" ] && kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null; then
    echo "  $name 已在运行 (pid $(cat "$RUN/$name.pid"))"
    return
  fi
  (cd "$BACKEND" && nohup "$@" > "$RUN/$name.log" 2>&1 < /dev/null &
   echo $! > "$RUN/$name.pid")
  echo "  已启动 $name (pid $(cat "$RUN/$name.pid"))"
}

echo "==> 启动 API 与 workers"
start_one api "$BACKEND/.venv/Scripts/python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8420
start_one worker "$BACKEND/.venv/Scripts/python.exe" -m arq app.workers.settings.WorkerSettings
start_one enhancement_worker "$BACKEND/.venv/Scripts/python.exe" -m arq app.workers.enhancement_settings.WorkerSettings
start_one move_worker "$BACKEND/.venv/Scripts/python.exe" -m arq app.workers.move_settings.WorkerSettings

# ---------- 4. 健康检查 ----------
echo "==> 等待 API 就绪"
for i in $(seq 1 40); do
  if curl -s --noproxy '*' -m 2 http://127.0.0.1:8420/api/health -o /dev/null 2>/dev/null; then
    break
  fi
  sleep 0.5
  [ "$i" = 40 ] && { echo "✗ API 未就绪，查看 $RUN/api.log"; tail -20 "$RUN/api.log"; exit 1; }
done

curl -s --noproxy '*' -m 30 http://127.0.0.1:8420/api/health \
  | "$BACKEND/.venv/Scripts/python.exe" -c "import json,sys; d=json.load(sys.stdin); print(f\"  {d['status']}  db={d['database']} redis={d['redis']} openlist={d['openlist']} vaapi={d['vaapi']} ffmpeg={d['ffmpeg']}\")"

echo
echo "  界面   http://127.0.0.1:8420   （首次访问按提示初始化信任设备）"
echo "  日志   $RUN/*.log"
