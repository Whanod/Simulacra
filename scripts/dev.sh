#!/usr/bin/env bash
# Standalone dev boot: postgres + backend + frontend, all via docker compose.
# Idempotent — tears down anything already up and brings the full stack back.
#
# Only prerequisites:
#   - docker (with `docker compose`) running
#   - repo-root .env with PRIVY_ID / PRIVY_SECRET (and any other server vars)

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.dev-logs"
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "[dev] ERROR: 'docker' not found on PATH. Install Docker Desktop." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "[dev] ERROR: 'docker compose' (v2) not available. Update Docker Desktop." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "[dev] ERROR: Docker daemon not running. Start Docker Desktop and retry." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# load repo-root .env so PRIVY_ID / PRIVY_SECRET reach docker compose.
# docker compose reads .env automatically for variable substitution, but
# our compose file currently inlines values. We still export so any future
# ${PRIVY_ID} references in compose resolve.
# ---------------------------------------------------------------------------
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
else
  echo "[dev] WARN: $ROOT/.env not found — backend will run without Privy verification."
fi

cd "$ROOT"

# ---------------------------------------------------------------------------
# tear down anything already running so this script is truly idempotent.
# `docker compose down` only operates on this project, but the compose file
# pins hardcoded container_names (defi-sim-postgres/backend/frontend) — those
# can collide with containers from another checkout of this repo at a
# different path. Force-remove by name as well to cover that case.
# Data volumes are project-scoped, so this does not touch postgres data.
# ---------------------------------------------------------------------------
echo "[dev] stopping any existing stack..."
docker compose down --remove-orphans >"$LOG_DIR/compose-down.log" 2>&1 || true
docker rm -f defi-sim-postgres defi-sim-backend defi-sim-frontend \
  >>"$LOG_DIR/compose-down.log" 2>&1 || true

# ---------------------------------------------------------------------------
# build + start the full stack
# ---------------------------------------------------------------------------
echo "[dev] building images and starting postgres + backend + frontend..."
if ! docker compose up -d --build 2>&1 | tee "$LOG_DIR/compose-up.log"; then
  echo "[dev] ERROR: docker compose up failed. See $LOG_DIR/compose-up.log" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# wait for each service's healthcheck (defined in docker-compose.yml)
# ---------------------------------------------------------------------------
wait_healthy() {
  local svc="$1" container="$2" tries="${3:-60}"
  echo -n "[dev]   waiting for $svc"
  for _ in $(seq 1 "$tries"); do
    local status
    status="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || echo missing)"
    if [[ "$status" == "healthy" ]]; then
      echo " — ok"
      return 0
    fi
    echo -n "."
    sleep 1
  done
  echo
  echo "[dev] ERROR: $svc did not become healthy. Try: docker logs $container" >&2
  return 1
}

wait_healthy postgres defi-sim-postgres 40 || exit 1
wait_healthy backend  defi-sim-backend  60 || exit 1
wait_healthy frontend defi-sim-frontend 90 || exit 1

# ---------------------------------------------------------------------------
# done
# ---------------------------------------------------------------------------
echo
echo "[dev] stack up:"
docker compose ps

cat <<EOF

[dev] ready:
  frontend  http://localhost:3001
  backend   http://localhost:8000
  postgres  127.0.0.1:5432         (container: defi-sim-postgres)

Logs:
  docker compose logs -f backend
  docker compose logs -f frontend
  docker compose logs -f postgres

To stop everything:
  docker compose down            # keep db volume
  docker compose down -v         # wipe db volume too
EOF
