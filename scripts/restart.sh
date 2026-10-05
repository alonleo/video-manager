#!/usr/bin/env bash
# 重启 API 与 worker（生产模式，前端由 FastAPI 托管）。
#
#   ./scripts/restart.sh
#
# 用 PID 文件管理进程，避免 pkill -f "xxx" 匹配到自己所在的 shell 命令行
# 而把脚本自身杀掉。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
VENV="$BACKEND/.venv/bin"
RUN="$ROOT/.run"
mkdir -p "$RUN"

stop_one() {
  local name="$1"
  local pidfile="$RUN/$name.pid"
  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      if [ "$name" = "move_worker" ]; then
        echo "  等待后台移动安全完成…"
        while kill -0 "$pid" 2>/dev/null; do
          [ "$(ps -o stat= -p "$pid" | cut -c1)" = "Z" ] && break
          sleep 1
        done
      fi
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
      done
      kill -9 "$pid" 2>/dev/null || true
      echo "  已停止 $name (pid $pid)"
    fi
    rm -f "$pidfile"
  fi
}

start_one() {
  local name="$1"; shift
  setsid nohup "$@" > "$RUN/$name.log" 2>&1 < /dev/null &
  echo $! > "$RUN/$name.pid"
  echo "  已启动 $name (pid $(cat "$RUN/$name.pid"))"
}

echo "==> 停止"
stop_one move_worker
stop_one api
stop_one worker
stop_one enhancement_worker

sleep 1

echo "==> 启动"
cd "$BACKEND"
start_one api "$VENV/uvicorn" app.main:app --host 127.0.0.1 --port 8420
start_one worker "$VENV/arq" app.workers.settings.WorkerSettings
start_one enhancement_worker "$VENV/arq" app.workers.enhancement_settings.WorkerSettings
start_one move_worker "$VENV/arq" app.workers.move_settings.WorkerSettings

echo "==> 等待就绪"
for i in $(seq 1 40); do
  if curl -s --noproxy '*' -m 2 http://127.0.0.1:8420/api/health -o /dev/null 2>/dev/null; then
    echo "  API 就绪（${i}00ms 内）"
    break
  fi
  sleep 0.5
  if [ "$i" = 40 ]; then
    echo "  ✗ API 未就绪，查看 $RUN/api.log"
    tail -20 "$RUN/api.log"
    exit 1
  fi
done

echo
echo "==> 健康检查"
curl -s --noproxy '*' -m 30 http://127.0.0.1:8420/api/health \
  | "$VENV/python" -c "import json,sys; d=json.load(sys.stdin); print(f\"  {d['status']}  db={d['database']} redis={d['redis']} openlist={d['openlist']} vaapi={d['vaapi']} ffmpeg={d['ffmpeg']}\")"

echo
echo "  界面   http://127.0.0.1:8420"
echo "  日志   $RUN/api.log  $RUN/worker.log"
