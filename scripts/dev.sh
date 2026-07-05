#!/usr/bin/env bash
# 一键启动前后端（开发模式）：
#   后端 FastAPI  → http://127.0.0.1:${BACKEND_PORT:-8765}（仅 API）
#   前端 Vite dev → http://127.0.0.1:${FRONTEND_PORT:-5173}（/api 代理到后端，热更新）
# Ctrl+C 同时结束两者。生产模式见 README（npm run build 后仅起后端即可）。
#
# 重启语义：固定端口被占用时，自动结束占用进程后启动新的开发服务。
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_PORT="${BACKEND_PORT:-8765}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

free_port() {
  local port="$1" label="$2" pids pid cmd
  pids=$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)
  [ -z "${pids}" ] && return 0
  echo "▸ ${label} 端口 ${port} 被占用，结束占用进程后重启"
  for pid in ${pids}; do
    cmd=$(ps -o command= -p "${pid}" 2>/dev/null || true)
    echo "  - pid ${pid}: ${cmd:-未知命令}"
    kill "${pid}" 2>/dev/null || true
  done
  # 等待端口释放；顽固进程 SIGKILL 兜底。
  for _ in $(seq 1 25); do
    pids=$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)
    [ -z "${pids}" ] && return 0
    sleep 0.2
  done
  echo "▸ ${label} 旧进程未退出，强制结束"
  kill -9 ${pids} 2>/dev/null || true
  sleep 0.5
}

wait_for_backend() {
  local url="http://127.0.0.1:${BACKEND_PORT}/api/state"
  local attempts="${BACKEND_WAIT_ATTEMPTS:-100}"
  echo "▸ 等待后端就绪 ${url}"
  for _ in $(seq 1 "${attempts}"); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
      echo "✘ 后端进程提前退出，请检查上方日志。"
      exit 1
    fi
    sleep 0.2
  done
  echo "✘ 后端启动超时，未能访问 ${url}"
  exit 1
}

free_port "${BACKEND_PORT}" "后端"
free_port "${FRONTEND_PORT}" "前端"

if [ ! -d webapp/node_modules ]; then
  echo "▸ 首次运行：安装前端依赖…"
  npm --prefix webapp install
fi

echo "▸ 启动后端 http://127.0.0.1:${BACKEND_PORT}"
uv run finance-agent --web --port "${BACKEND_PORT}" &
BACKEND_PID=$!
trap 'kill "${BACKEND_PID}" 2>/dev/null || true' EXIT INT TERM
wait_for_backend

echo "▸ 启动前端 Vite dev server http://127.0.0.1:${FRONTEND_PORT}（/api → 后端 ${BACKEND_PORT}）"
BACKEND_PORT="${BACKEND_PORT}" npm --prefix webapp run dev -- --port "${FRONTEND_PORT}" --strictPort
