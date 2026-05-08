#!/usr/bin/env bash
# Start backend (uvicorn :8000) and frontend (next :3000).
# If either is already up, kill them first and restart both.

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.dev-logs"
mkdir -p "$LOG_DIR"

kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "[dev] killing pids on :$port -> $pids"
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      kill -9 $pids 2>/dev/null || true
    fi
  fi
}

echo "[dev] stopping any running backend/frontend..."
kill_port 8000
kill_port 3000

echo "[dev] starting backend on :8000"
cd "$ROOT"
nohup ./.venv/bin/uvicorn defi_sim_api.main:app \
  --host 127.0.0.1 --port 8000 --reload --reload-dir src \
  --reload-exclude '.claude/*' \
  >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "[dev]   backend pid=$BACKEND_PID  log=$LOG_DIR/backend.log"

echo "[dev] starting frontend on :3000"
cd "$ROOT/frontend"
nohup npm run dev >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "[dev]   frontend pid=$FRONTEND_PID log=$LOG_DIR/frontend.log"

sleep 5
echo "[dev] listening ports:"
lsof -i :3000 -i :8000 -sTCP:LISTEN 2>/dev/null || echo "[dev]   (nothing yet — check logs)"
