#!/usr/bin/env bash
# Windows 停止（Git Bash 运行）：./scripts/stop-windows.sh
# 停掉 API / workers；依赖容器与 WSL 保持运行（docker compose down 请手动执行）。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/.run"

stop_one() {
  local name="$1"
  local pidfile="$RUN/$name.pid"
  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      echo "  已停止 $name (pid $pid)"
    fi
    rm -f "$pidfile"
  fi
}

echo "==> 停止"
stop_one move_worker
stop_one enhancement_worker
stop_one worker
stop_one api
echo "  （WSL 与依赖容器仍在运行；需要彻底停止请执行 WSL 内 docker compose down）"
