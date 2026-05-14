#!/bin/bash
# Starts the defi-sim backend (uvicorn) and frontend (Next.js standalone
# server) inside a single container. Signals are forwarded to both children;
# if either exits the container exits with its status.
set -euo pipefail

: "${BACKEND_HOST:=127.0.0.1}"
: "${BACKEND_PORT:=8000}"
: "${PORT:=3000}"
: "${HOSTNAME:=0.0.0.0}"

log() { printf '[entrypoint] %s\n' "$*"; }

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  local rc=${1:-0}
  trap - TERM INT EXIT
  [[ -n "$FRONTEND_PID" ]] && kill -TERM "$FRONTEND_PID" 2>/dev/null || true
  [[ -n "$BACKEND_PID"  ]] && kill -TERM "$BACKEND_PID"  2>/dev/null || true
  wait 2>/dev/null || true
  exit "$rc"
}
trap 'cleanup $?' TERM INT

log "starting backend (uvicorn) on ${BACKEND_HOST}:${BACKEND_PORT}"
uvicorn defi_sim_api.main:app \
  --host "${BACKEND_HOST}" \
  --port "${BACKEND_PORT}" \
  --log-level "${UVICORN_LOG_LEVEL:-info}" &
BACKEND_PID=$!

log "waiting for backend /health..."
for _ in $(seq 1 60); do
  if curl -fsS "http://${BACKEND_HOST}:${BACKEND_PORT}/health" >/dev/null 2>&1; then
    log "backend healthy"
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    log "backend died before becoming healthy" >&2
    wait "$BACKEND_PID" || true
    exit 1
  fi
  sleep 1
done

log "starting frontend (next) on ${HOSTNAME}:${PORT}"
cd /app/frontend
HOSTNAME="${HOSTNAME}" PORT="${PORT}" \
  BACKEND_INTERNAL_URL="http://${BACKEND_HOST}:${BACKEND_PORT}" \
  node server.js &
FRONTEND_PID=$!

wait -n "$BACKEND_PID" "$FRONTEND_PID"
EXITED_RC=$?
log "a child exited with $EXITED_RC — shutting down"
cleanup "$EXITED_RC"
