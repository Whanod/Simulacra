# defi-sim frontend

Next.js 15 (App Router) studio UI for the DeFi simulation engine. Every page
talks to the FastAPI backend under `../src/defi_sim_api`; there are no mocks.

## Running against the API

Fastest path:

```bash
pnpm dev:stack
# → starts uvicorn on http://127.0.0.1:8000 and next dev on http://127.0.0.1:3000
```

If you prefer separate terminals, run:

```bash
pnpm dev:api
# → uvicorn defi_sim_api.main:app on http://127.0.0.1:8000 with --reload
```

In another terminal:

```bash
pnpm dev
# → next dev on http://127.0.0.1:3000
```

`NEXT_PUBLIC_API_URL` overrides the API base URL the browser talks to
(default: `http://127.0.0.1:8000`). Point it at any reachable backend to
work against staged artifacts:

```bash
NEXT_PUBLIC_API_URL=http://some-other-host:8000 pnpm dev
```

The backend requires a Python virtualenv at `../.venv` with the project
installed in editable mode (`pip install -e .`). Run from the repo root
the first time:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Testing

- `pnpm test` — adapter unit tests (vitest, `src/**/*.test.ts`).
- `pnpm test:int` — service-level integration tests that spawn uvicorn
  against a temp artifact dir (`test/integration`).
- `pnpm test:e2e` — Playwright specs against `next dev` + a spawned
  uvicorn. The harness manages both servers; see
  `playwright.config.ts`.
- `pnpm test:all` — the full frontend pyramid.
- `../scripts/test-all.sh` — backend API tests first, then the frontend pyramid.

Ports used by the e2e harness (to avoid colliding with your own dev
servers): `127.0.0.1:3100` for Next and `127.0.0.1:8100` for uvicorn.
Override with `PLAYWRIGHT_FRONTEND_PORT` / `PLAYWRIGHT_API_PORT`.

## Layout

- `src/app/(studio)/**` — the routes: dashboard, builder, runner,
  results, compare, sweeps, registry, reports.
- `src/features/**` — per-route feature components (not shared).
- `src/components/**` — shared UI primitives (Card, Tabs, Modal, Charts).
- `src/lib/api/client.ts` — `apiFetch` / `apiFetchBlob`. Reads
  `NEXT_PUBLIC_API_URL`.
- `src/lib/api/adapters/**` — pure functions that turn backend JSON into
  the frontend types under `src/lib/types/**`. All tests live next to
  the adapters as `*.test.ts`.
- `src/lib/services/**` — the four services (`simulationService`,
  `sweepService`, `reportService`, `registryService`, `runnerService`)
  that wrap `apiFetch` calls behind domain-level methods.
- `src/lib/state/useStudioStore.tsx` — zustand-style React context store
  for cross-route UI state (selected run, compare targets, interactive
  engine handles).
- `src/lib/hooks/useAsync.ts` — the standard loading/error/data hook
  used by every `useAsync(...)` call.
- `src/lib/descriptions/registry.ts` — static per-component descriptions
  for the Registry page. The backend owns the list of component _types_;
  this file owns the human-readable blurbs.

## Migration notes

`plan-api.md` tracks the mock → real-API migration. Phases 0–5 are
complete in this checkout.
