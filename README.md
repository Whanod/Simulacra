# Simulacra

A Solana-native simulation engine for DeFi protocols. Simulacra forks mainnet state, replays it through a deterministic engine, and calibrates the engine's output against on-chain ground truth so you can stress-test protocol behavior, agent strategies, and economic parameters before they hit production.

Ships as a Python library, a FastAPI backend, and a Next.js studio for configuring runs and comparing results.

## Run with Docker

```bash
docker compose up -d --build
docker compose ps        # wait until both services are healthy
```

- Backend: http://localhost:8000 (`/health`, `/docs`)
- Frontend: http://localhost:3001

```bash
docker compose logs -f backend
docker compose down       # keep artifacts volume
docker compose down -v    # wipe artifacts too
```

## Run locally

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[api,dev]'

cd frontend && npm install && cd ..

./scripts/dev.sh
```

Starts `uvicorn defi_sim_api.main:app` on `:8000` and `next dev` on `:3000`. Logs land in `.dev-logs/`; re-running the script restarts both.

## Tests

```bash
./scripts/test-all.sh       # full suite
pytest                      # unit + fast integration
pytest -m forked_state      # snapshotted mainnet replays
pytest -m calibration       # mainnet ground-truth comparisons
```

## Authentication (Privy)

Email-based auth is wired through Privy. It is **off by default** —
local dev, the test suite, and any deployment that doesn't set the env
vars below run in *open mode*: every API list returns all rows, no JWT
is required, no gate modal mounts on the frontend.

To enable enforced auth, set both of these env vars:

| Var | Where | Purpose |
|---|---|---|
| `PRIVY_APP_ID` (or `PRIVY_ID` alias) | backend (uvicorn / docker) — repo-root `.env` | JWT verification against the app's JWKS |
| `NEXT_PUBLIC_PRIVY_APP_ID` | frontend (build-time) — `frontend/.env.local` | Loads the Privy SDK + mounts the gate modal |

Both names refer to the same Privy app id (the value at the top of
your Privy dashboard). `scripts/dev.sh` sources the repo-root `.env`
on startup so the backend picks it up; Next.js auto-loads
`frontend/.env.local` so the frontend gets `NEXT_PUBLIC_PRIVY_APP_ID`
without any extra step.

When set:
- Studio routes are blocked by a non-dismissable email-OTP modal
  (Flow 01). Anonymous users cannot reach `/dashboard`, `/builder`,
  `/runs`, etc. without signing in.
- Run / sweep / report / snapshot lists scope to the signed-in user's
  Privy DID. Cross-owner reads return 404 (not 403 — never leak
  existence).
- Share-link routes (`/r/[runId]`, `/embed/*`) stay public so embedded
  widgets and external sharing keep working.
- API keys (`DEFI_SIM_API_KEYS`) keep working alongside Privy as a
  service-level path; their writes leave `owner_id` NULL and are
  globally readable to any signed-in user.

Optional:

| Var | Default | Purpose |
|---|---|---|
| `PRIVY_APP_SECRET` | unset | Reserved for future server-side Privy API calls (user lookup, deletion webhooks). Not required for JWT verification. |
