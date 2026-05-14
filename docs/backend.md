# Backend Bootstrap

The FastAPI surface (`defi_sim_api`) persists all run state in Postgres via
`PostgresArtifactStore`. There is no filesystem-blob fallback — the backend
will fail to start if it cannot reach the configured database.

## Install

Install the package with the API and development extras:

```bash
pip install -e '.[api,dev]'
```

This installs the core simulation library, the FastAPI surface in `defi_sim_api`, and the local test dependencies used by the backend test suite.

## Run The API

The backend expects two environment variables:

- `DATABASE_URL` — `postgresql://user:pass@host:port/db`
- `DEFI_SIM_STORE_BACKEND=postgres` (required; the older `local` SQLite/filesystem store has been retired)

### Docker (recommended)

`docker compose up -d --build` boots a Postgres container, applies the
schema on first connect, and exposes the API on `:8000`. See
`docker-compose.yml` for the wiring; `docker-compose.coolify.yml` is the
production-shaped equivalent.

### Local Python

```bash
# 1. Start Postgres (uses the docker-compose service so the schema match
#    matches CI):
docker compose up -d postgres

# 2. Point the API at it and launch uvicorn:
export DATABASE_URL=postgresql://defisim:defisim@localhost:5432/defisim
export DEFI_SIM_STORE_BACKEND=postgres
python -m uvicorn defi_sim_api.main:app --reload
```

The schema in `src/defi_sim_api/backend/schema.sql` is applied idempotently
when the connection pool opens; no migration tool is wired up yet, so if you
change the schema mid-dev, run `scripts/purge-local.sh` to wipe and re-apply.

## Smoke Check

After the server starts, verify the health endpoint:

```bash
curl http://127.0.0.1:8000/health
```

## Wipe local data

```bash
scripts/purge-local.sh                            # docker-compose postgres
DATABASE_URL=postgres://... scripts/purge-local.sh
```

Truncates `runs`, `sweeps`, `reports` (CASCADE handles events,
round_snapshots, round_metrics, fees, named_snapshots).

## Backend Test Commands

Tests run against a session-scoped Postgres container provisioned by
`testcontainers-python`. Docker must be running; the suite skips with a
warning when it isn't.

```bash
pytest tests/api
pytest tests/engine tests/report tests/validation tests/test_review_regressions.py
```
