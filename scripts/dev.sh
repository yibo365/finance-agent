#!/usr/bin/env bash
# 一键启动前后端（开发模式）：
#   后端 FastAPI  → http://127.0.0.1:${BACKEND_PORT:-8765}（仅 API）
#   前端 Vite dev → http://127.0.0.1:${FRONTEND_PORT:-5173}（/api 代理到后端，热更新）
# Ctrl+C 同时结束两者。生产模式见 README（npm run build 后仅起后端即可）。
#
# 重启语义：端口被【本项目的旧进程】（finance-agent --web / vite dev）占用时，
# 自动结束旧进程后启动新的；被其他程序占用则拒绝代杀、给出指引。
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_PORT="${BACKEND_PORT:-8765}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

free_port() {
  local port="$1" label="$2" pids pid cmd
  pids=$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)
  [ -z "${pids}" ] && return 0
  for pid in ${pids}; do
    cmd=$(ps -o command= -p "${pid}" 2>/dev/null || true)
    case "${cmd}" in
      *finance-agent*|*vite*)
        echo "▸ ${label} 端口 ${port} 被旧进程占用（pid ${pid}），先结束它再启动"
        kill "${pid}" 2>/dev/null || true
        ;;
      *)
        echo "✘ ${label} 端口 ${port} 被非本项目进程占用（pid ${pid}）："
        echo "    ${cmd}"
        echo "  为安全起见不代杀。请自行处理，或换端口重试："
        echo "    BACKEND_PORT=$((BACKEND_PORT + 1)) FRONTEND_PORT=$((FRONTEND_PORT + 1)) ./scripts/dev.sh"
        exit 1
        ;;
    esac
  done
  # 等待端口释放；顽固进程 SIGKILL 兜底（只针对上面已识别的本项目进程）
  for _ in $(seq 1 25); do
    pids=$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)
    [ -z "${pids}" ] && return 0
    sleep 0.2
  done
  echo "▸ ${label} 旧进程未退出，强制结束"
  kill -9 ${pids} 2>/dev/null || true
  sleep 0.5
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

echo "▸ 启动前端 Vite dev server http://127.0.0.1:${FRONTEND_PORT}（/api → 后端 ${BACKEND_PORT}）"
BACKEND_PORT="${BACKEND_PORT}" npm --prefix webapp run dev -- --port "${FRONTEND_PORT}" --strictPort
