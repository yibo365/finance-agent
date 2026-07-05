#!/usr/bin/env bash
# 一键启动前后端（开发模式）：
#   后端 FastAPI  → http://127.0.0.1:${BACKEND_PORT:-8765}（仅 API）
#   前端 Vite dev → http://127.0.0.1:5173（/api 代理到后端，改前端代码热更新）
# Ctrl+C 同时结束两者。生产模式见 README（npm run build 后仅起后端即可）。
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_PORT="${BACKEND_PORT:-8765}"

if [ ! -d webapp/node_modules ]; then
  echo "▸ 首次运行：安装前端依赖…"
  npm --prefix webapp install
fi

echo "▸ 启动后端 http://127.0.0.1:${BACKEND_PORT}"
uv run finance-agent --web --port "${BACKEND_PORT}" &
BACKEND_PID=$!
trap 'kill "${BACKEND_PID}" 2>/dev/null || true' EXIT INT TERM

echo "▸ 启动前端 Vite dev server（/api → 后端 ${BACKEND_PORT}）"
BACKEND_PORT="${BACKEND_PORT}" npm --prefix webapp run dev
