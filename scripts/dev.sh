#!/usr/bin/env bash
# 一键启动开发环境：API + worker + 前端 dev server
#
#   ./scripts/dev.sh
#
# 生产模式不需要这个脚本：前端 `pnpm build` 后由 FastAPI 直接托管，
# 只需跑 api + worker 两个进程即可（见 README）。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv/bin"

if [ ! -x "$VENV/uvicorn" ]; then
  echo "✗ 后端依赖未安装。请先执行：cd backend && uv sync"
  exit 1
fi

if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "✗ 前端依赖未安装。请先执行：cd frontend && pnpm install"
  exit 1
fi

# 依赖服务（Redis + OpenList）
if command -v docker >/dev/null 2>&1; then
  if ! docker compose -f "$ROOT/docker-compose.yml" ps --status running 2>/dev/null | grep -q redis; then
    echo "==> 启动依赖容器（redis / openlist）"
    docker compose -f "$ROOT/docker-compose.yml" up -d
    sleep 3
  fi
fi

cleanup() {
  echo
  echo "==> 停止子进程"
  # 只杀自己启动的子进程，避免影响系统上其他服务
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> API       http://127.0.0.1:8420  (文档 /docs)"
echo "==> 前端      http://127.0.0.1:5173"
echo "==> Worker    后台任务"
echo

cd "$BACKEND"
"$VENV/uvicorn" app.main:app --host 127.0.0.1 --port 8420 --reload &

cd "$BACKEND"
"$VENV/arq" app.workers.settings.WorkerSettings &
"$VENV/arq" app.workers.enhancement_settings.WorkerSettings &
"$VENV/arq" app.workers.move_settings.WorkerSettings &

cd "$FRONTEND"
pnpm dev &

wait
