#!/usr/bin/env bash
# Wipe every run from the local Postgres dev DB.
#
# Defaults to the docker-compose service `defi-sim-postgres`; override with
# DATABASE_URL to point at a different Postgres (e.g. a remote dev DB).
#
# Usage:
#   scripts/purge-local.sh                              # docker-compose pg
#   DATABASE_URL=postgres://... scripts/purge-local.sh  # any reachable pg

set -euo pipefail

SQL='TRUNCATE TABLE runs, sweeps, reports CASCADE;'

if [[ -n "${DATABASE_URL:-}" ]]; then
  if ! command -v psql >/dev/null 2>&1; then
    echo "[purge-local] DATABASE_URL set but psql is not installed" >&2
    exit 1
  fi
  echo "[purge-local] truncating via DATABASE_URL"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "$SQL"
  echo "[purge-local] done"
  exit 0
fi

CONTAINER="${DEFI_SIM_PG_CONTAINER:-defi-sim-postgres}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "[purge-local] container '$CONTAINER' is not running" >&2
  echo "[purge-local] start it with 'docker compose up -d postgres' or set DATABASE_URL" >&2
  exit 1
fi

echo "[purge-local] truncating via docker exec $CONTAINER"
docker exec -i "$CONTAINER" psql -U defisim -d defisim -v ON_ERROR_STOP=1 -c "$SQL"
echo "[purge-local] done"
