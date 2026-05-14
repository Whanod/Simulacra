# Postgres Migration & Storage Refactor — Plan

Status: draft for review
Last updated: 2026-05-12

## Summary

Replace the current SQLite + filesystem-JSON storage layer with a Postgres-first design in which events are queryable rows, per-round metrics are pre-aggregated into a regular table (one table shared by all runs, populated once at run completion), and snapshots/specs live as JSONB. Drop monolithic `result.json` / `events.json` blobs entirely. No backward compatibility for user data — wipe and replace.

## Goals

- One source of truth: Postgres holds all run state, events, snapshots, summaries, and metadata.
- Events are structured rows, not opaque JSON blobs — enabling server-side filtering, retroactive metrics, and cross-run analytics.
- Charts query metric endpoints with parameters, instead of downloading multi-MB JSONs and slicing client-side.
- The system supports multi-replica / multi-user deployment without local-filesystem dependencies.
- Behavior preserved: same spec + same seed produces the same numbers everywhere a user sees them.

## Non-goals (v1)

- Object storage (S3 / Vercel Blob / R2). Not needed once snapshots and events live in Postgres; defer until scale demands it. Only candidate today is report ZIPs, which become generated-on-demand instead.
- Backward compatibility with existing artifact directories or SQLite databases. Wipe is acceptable.
- Multi-tenancy / per-user auth. Separate concern; not in scope.
- Schema migration tooling (Alembic). Single initial DDL; revisit if/when schema churn becomes a thing.

## Current state inventory

### Storage today

- **SQLite**: `.defi_sim_artifacts/artifacts.sqlite3`, accessed via raw `sqlite3` driver in `src/defi_sim_api/backend/store.py` (874 lines). No ORM, no migrations, schema created inline via `CREATE TABLE IF NOT EXISTS`.
- **Filesystem blobs**: `.defi_sim_artifacts/blobs/runs/<run_id>/` containing `spec.json`, `result.json`, `events.json`, and `rounds/<n>.json`.

### Tables (5)

- `runs` — 15 columns, metadata + filesystem pointers (`spec_path`, `result_path`, `events_path`) + small `summary_json`.
- `round_snapshots` — composite PK `(run_id, round_number)`, points to `rounds/<n>.json`.
- `named_snapshots` — user-bookmarked checkpoints; points to a saved-state blob.
- `sweeps` — parameter-sweep jobs; points to spec/rows files + `summary_json`.
- `reports` — generated comparison/analysis bundles; points to manifest + ZIP.

### Blob shapes & sizes (observed)

- 52 run directories on disk; only 2 rows in `runs` — **50 runs are orphaned blobs**, demonstrating the two-systems consistency problem in miniature.
- Per run: ~9 KB `spec.json` + ~3–4 MB `result.json` + ~6–7 MB `events.json` + 1000 × ~7 KB per-round snapshots ≈ ~17 MB/run.
- 26,156 JSON files totaling 735 MB local.
- Median file 7 KB, max 6.7 MB.

### Event shape (observed)

- 2,859 events per 500-round run, 9 distinct event types.
- Universal fields: `event_id`, `run_id`, `round`, `timestamp`, `type`, plus `data` (heterogeneous).
- Frequent additional fields: `agent_id`, `action_type`, `correlation_id`, `gas_cost`, `execution_cost`.
- `SIMULATION_END.data.result` contains the **entire** `result.json` duplicated inside `events.json` — pure redundancy; dropped in v1.

### Call sites (refactor surface)

- **Write path (single funnel):** `src/defi_sim_api/backend/runtime.py` → `store.save_run_artifacts()` (3 callers: `persist_sync_run`, `persist_replay_run`, `persist_streaming_run`). `EventBus.emit()` in `src/defi_sim/engine/events.py` collects events in memory; runtime flushes at completion.
- **API endpoints (~15):** `runs`, `simulations`, `reports`, `snapshots`, `calibration`, `share`, `embed`, `sweeps`, `wallet` routers. Read paths span every blob type.
- **Frontend services (5):** `simulationService.ts`, `replayService.ts`, `reportService.ts`, `sweepService.ts`, `runnerService.ts`. Plus 5 integration test files mirroring them.
- **Tests:** 181 Python test files; ~42 in `tests/api/` exercise the store via the full engine (not mocked). 15 engine tests touch storage. Tests use temp-dir-per-test SQLite fixtures via `conftest.py:20`.
- **External readers:** none — no CLI tools, scripts, or notebooks parse the JSON blobs directly. Everything routes through the API.

## Target architecture

### Schema sketch (DDL — final names/types decided in Phase 1)

```sql
CREATE TABLE runs (
  run_id              TEXT PRIMARY KEY,
  simulation_id       TEXT,
  source              TEXT NOT NULL,
  source_run_id       TEXT,
  source_snapshot_id  TEXT,
  status              TEXT NOT NULL,
  seed                BIGINT,
  market_type         TEXT,
  current_round       INT  NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  spec                JSONB NOT NULL,          -- replaces spec.json
  summary             JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE events (
  run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  event_id        BIGINT NOT NULL,
  round           INT NOT NULL,
  timestamp       DOUBLE PRECISION NOT NULL,
  type            TEXT NOT NULL,
  agent_id        TEXT,
  action_type     TEXT,
  asset           TEXT,
  amount          NUMERIC,
  price           DOUBLE PRECISION,
  gas_cost        NUMERIC,
  execution_cost  NUMERIC,
  succeeded       BOOLEAN,
  correlation_id  TEXT,
  data            JSONB,                       -- type-specific payload remainder
  PRIMARY KEY (run_id, event_id)
);

CREATE INDEX events_run_round       ON events (run_id, round);
CREATE INDEX events_run_agent_round ON events (run_id, agent_id, round) WHERE agent_id IS NOT NULL;
CREATE INDEX events_run_type        ON events (run_id, type);
CREATE INDEX events_run_correlation ON events (run_id, correlation_id) WHERE correlation_id IS NOT NULL;

-- Populated by the engine via INSERT…SELECT at run completion.
CREATE TABLE round_metrics (
  run_id         TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  round          INT  NOT NULL,
  agent_id       TEXT,                         -- NULL = whole-market roll-up
  pnl            NUMERIC,
  volume         NUMERIC,
  num_actions    INT,
  num_failed     INT,
  gas_spent      NUMERIC,
  price_snapshot JSONB,                        -- {asset: price}
  PRIMARY KEY (run_id, round, agent_id)
);

CREATE TABLE round_snapshots (
  run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  round_number  INT  NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  state         JSONB NOT NULL,                -- replaces rounds/<n>.json
  PRIMARY KEY (run_id, round_number)
);

CREATE TABLE named_snapshots (
  snapshot_id     TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL,
  source_run_id   TEXT,
  simulation_id   TEXT,
  round_number    INT NOT NULL,
  label           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  state           JSONB NOT NULL                -- replaces blob_path
);

CREATE TABLE sweeps (
  sweep_id     TEXT PRIMARY KEY,
  status       TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  spec         JSONB NOT NULL,
  rows         JSONB NOT NULL DEFAULT '[]'::jsonb,
  summary      JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE reports (
  report_id   TEXT PRIMARY KEY,
  status      TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  manifest    JSONB NOT NULL                    -- inputs + parameters; bundle generated on demand
);
```

Design notes:
- Spec/summary/state inlined as JSONB on their parent row. Eliminates the entire blob system except for report bundles (which are derived).
- Six hot event fields are promoted to typed columns; everything else stays in `data JSONB`.
- `ON DELETE CASCADE` everywhere — orphan-blob class of bug becomes impossible.
- `(run_id, event_id)` PK preserves insertion order without an autoincrement.
- `TIMESTAMPTZ` for real times, not ISO strings.

### Query layer (replaces blob fetches)

```sql
-- GET /runs/{id}/metrics/pnl?agent=victim-1&from=100&to=200
SELECT round, pnl
FROM round_metrics
WHERE run_id = %s AND agent_id = %s AND round BETWEEN %s AND %s
ORDER BY round;

-- GET /runs/{id}/events?type=ACTION_EXECUTED&agent=sandwich-1&cursor=...
SELECT round, timestamp, action_type, asset, amount, price, data
FROM events
WHERE run_id = %s AND type = 'ACTION_EXECUTED' AND agent_id = %s
ORDER BY event_id
LIMIT 500 OFFSET %s;

-- Cross-run comparison (what reports/sweeps want)
SELECT run_id,
       MAX(round) AS final_round,
       SUM(volume) AS total_volume,
       SUM(pnl) FILTER (WHERE agent_id = 'sandwich-1') AS sandwich_pnl
FROM round_metrics
WHERE run_id = ANY(%s)
GROUP BY run_id;
```

### API surface (new + modified)

Two tiers. **Views** are page-shaped bundles (one round trip per page); **resources** are ad-hoc, filter-driven endpoints used after the initial paint (round scrubber, event-log filters, correlation drill-down).

```
# View endpoints — one per page, returns a typed bundle
GET  /runs/{run_id}/views/overview        NEW   { run, spec_summary, tiles, series, event_summary }
GET  /runs/{run_id}/views/agent/{aid}     NEW (Phase 4 as demand surfaces)
POST /runs/views/compare                  NEW (Phase 4 as demand surfaces)

# Resource endpoints — granular, used for ad-hoc interactions
GET  /runs/{run_id}/metrics/{metric}     ?agent=&from=&to=&group_by=    NEW
GET  /runs/{run_id}/events                ?type=&agent=&from=&to=&cursor=&limit=
GET  /runs/{run_id}/correlations/{cid}                                  NEW
GET  /runs/{run_id}                       (spec is now an inline field)
GET  /runs/{run_id}/spec                  (thin SELECT)
GET  /runs/{run_id}/rounds/{n}            (SELECT state FROM round_snapshots)
POST /v1/reports                          (manifest stored; bundle generated on demand)
```

View handlers are ~20 lines: `asyncio.gather` over 3–6 SELECTs against `round_metrics` / `events` / `runs`, assemble, return. No new SQL primitives. `tiles` is the engine's `metadata.derived_metrics` map filtered to finite numerics — same shape `RecommendedMetricsGrid` consumes today, just delivered via the view instead of `run.metadata.derived_metrics`. Templates' own `recommended_metrics` field is a separate namespace (e.g. `final_yes_price`, `stopped_early`) that names result/spec fields, not engine-derived metrics; it isn't resolved here and stays a builder-UI hint until something downstream actually consumes it.

`GET /runs/{id}/result` is dropped outright — the view endpoint subsumes its consumers; no legacy-shape compatibility hedge.

### Decisions locked in

1. **Postgres holds everything.** Spec/summary/snapshots/events all in Postgres (typed columns + JSONB).
2. **No object storage in v1.** Only candidate is report ZIPs; those become generated-on-demand from queries. Revisit when scale demands.
3. **Wipe-and-replace.** Drop `.defi_sim_artifacts/`, drop SQLite, fresh schema. No backfill script.
4. **Driver: psycopg 3** (`psycopg[binary,pool]`), not psycopg2. Reasons: native async (pairs with FastAPI), native `COPY` ergonomics (used for bulk event inserts), first-class `psycopg_pool`, active development. No upstream forces psycopg2.
5. **Keep the store interface, swap the implementation.** `FileSystemArtifactStore` → `PostgresArtifactStore`. Everything upstream of `runtime.py:persist_*` is unchanged.
6. **`SIMULATION_END.data.result` duplication is dropped** during the migration — it's redundant with the result-equivalent query. Phase 2 parks the legacy result shape on a temporary `runs.result` JSONB column (the engine's `simulation_result_to_dict` keeps `__type__` tags that `events_to_list` strips); Phase 5 retires that column once Phase 4.5 has grown `/views/overview` to subsume every field the chart adapters read off `result`. (Earlier drafts placed the retirement in Phase 3 — moved out because Phase 3's `/views/overview` is intentionally a thin slice and doesn't yet cover `price_history`, `fee_history`, `liquidity_history`, `volume_history`, `agent_final_states`, or Whirlpool round-snapshot metrics.)

## Phased plan

Each phase leaves the system in a working (or explicitly half-finished but isolated) state.

### Phase 0 — Postgres foundation

- Add `psycopg[binary,pool]` dependency.
- `DATABASE_URL` env var; `docker-compose.yml` Postgres service; remove SQLite volume mount; update Dockerfile env.
- Connection pool factory replacing the `RLock` + single-connection pattern in `store.py:198`.
- **Test fixture:** `testcontainers-python` for a session-scoped Postgres container; per-test transactional rollback. Update `conftest.py:20`.
- **Golden-capture script (critical, see Testing section):** run canonical specs on `main`, dump every API response a chart consumes, commit `tests/golden/`.
- **Exit criterion:** `pytest` runs green against empty Postgres; golden files captured & committed; no production behavior changed yet.

### Phase 1 — Schema + new store

- Create the seven tables above as a single initial DDL file (no migration tooling yet).
- Implement `PostgresArtifactStore` matching the existing `store.py` protocol surface (same method signatures: `save_run_artifacts`, `get_run_result`, `get_run_events`, `list_runs`, etc.).
- Keep `FileSystemArtifactStore` wired in — new class exists side-by-side.
- Unit tests for `PostgresArtifactStore` in isolation.
- **Exit criterion:** `PostgresArtifactStore` unit tests pass; production code paths still use the old store.

### Phase 2 — Engine write path

- Rewire `runtime.py`:
  - `persist_sync_run` (lines 140–177), `persist_replay_run` (lines 44–137), `persist_streaming_run` — all write through `PostgresArtifactStore`.
  - Spec/summary written to `runs`; events bulk-inserted via psycopg3 `COPY` or batched `executemany`; snapshots written to `round_snapshots`.
  - On run completion: single `INSERT … SELECT … FROM events GROUP BY round, agent_id` populating `round_metrics`.
- ~~Drop the `SIMULATION_END.data.result` embed inside events.~~ — Done in the Phase 5 follow-up. The engine and runtime emit `SIMULATION_END` as a bare "done" marker; the full result lives in the typed columns + `round_snapshots` + `fees`. ~50% smaller events bundle per run.
- Live-streaming event reads (`GET /simulations/{id}/events`) unchanged — still served from in-memory `event_bus.history`.
- **Exit criterion:** new runs end up entirely in Postgres; `FileSystemArtifactStore` no longer called by production code paths.

### Phase 3 — API rewrite

Build the two-tier surface from the API section above. Resource endpoints first (they're the SQL primitives), then the overview view on top.

**Resource endpoints:**

- `GET /runs/{id}` — `SELECT` from `runs`; spec is a column.
- `GET /runs/{id}/spec` — `SELECT spec FROM runs`.
- `GET /runs/{id}/events` — paginated query with filters (`type`, `agent_id`, `round` range, `cursor`, `limit`).
- `GET /runs/{id}/rounds/{n}` — `SELECT state FROM round_snapshots`.
- `POST /v1/reports` / `GET /v1/reports/{id}` — manifest stored in DB; bundle generated on demand (`zipstream` of query results) rather than reading filesystem.
- New: `GET /runs/{id}/metrics/{metric}`, `GET /runs/{id}/correlations/{cid}`.
- Dropped: `GET /runs/{id}/result` — replaced by the overview view; no legacy-shape fallback.

**View endpoints:**

- `GET /runs/{id}/views/overview` — composes `run` row + `tiles` (resolved `recommended_metrics`) + `series` (time-series bundle from `round_metrics`) + `event_summary` (GROUP BY over `events`). Single round trip per results page.
- `/views/agent/{agent_id}` and `/views/compare` are stubbed for Phase 4; only land them when the frontend audit shows they're needed.

**Exit criterion:** existing API integration tests pass; new metric endpoints have unit + integration tests; `/views/overview` has integration tests covering tile resolution against at least two templates; golden suite starts going green as endpoints land.

### Phase 4 — Frontend rewrite

The audit is **page → view**, not chart → metric. Each route binds to one view endpoint; chart components are dumb consumers of typed slices off the view bundle. Granular resource endpoints are used only for post-paint interactions (scrubber, filtered event log, correlation drill-down).

- **Prerequisite:** add `@tanstack/react-query`. Non-optional. The view-vs-resource split assumes a client cache with dedup, stale-while-revalidate, and per-key invalidation; without it, scrubber + filters + revisits create thrash. Wire `QueryClientProvider` at the app root; pick conventions for query keys (`['run', runId, 'view', 'overview']` etc.) before any service rewrites land.
- **Reshape `frontend/src/lib/services/`:**
  - New `runViewService.ts` — `fetchOverview(runId)`, later `fetchAgentView(runId, agentId)`, `fetchCompareView(runIds)`.
  - New `metricsService.ts` + `eventsService.ts` — wrappers over the granular resource endpoints (scrubber, filtered event log, correlation drill-down).
  - `simulationService.ts` shrinks to lifecycle calls (start/stop/status). Its "fetch the giant result" methods are deleted.
  - `replayService.ts`, `reportService.ts`, `sweepService.ts`, `runnerService.ts` — keep their existing roles but drop any mega-result fetches.
- **Page audit (one row per route):** `/results/[runId]` → `views/overview`; `/compare` → `views/compare` (stub until needed); `/replay/[runId]` → `views/overview` + per-round resource fetches; `/sweeps/[id]` → sweep view (defined alongside).
- **`RecommendedMetricsGrid` stays.** Data source moves from `run.metadata.derived_metrics` to `view.tiles`. One-line change at the page level; component untouched.
- **Round scrubber and named-snapshot browser:** same wire shape, URL only changes. Now backed by `useQuery(['run', id, 'snapshot', n])` with a sliding cache window.
- **Risks:**
  - Components that did "fetch result, slice locally" — straightforward but tedious; the per-page view bundle is the destination shape.
  - Live-running runs need `refetchInterval` keyed off `status === 'running'` rather than `staleTime: Infinity`. Decide once, apply uniformly.
  - **`views/overview` as shipped in Phase 3 is too thin to drive the page rewire** — it carries `tiles`, four whitelisted `round_metrics` series, `event_summary`, and `spec_summary`, none of which feed `chartDataFromResult` or `metricsFromResult`. Phase 4.5 (below) closes the gap before the page audit lands. The service files (`runViewService`/`metricsService`/`eventsService`) and the react-query prerequisite have already landed; further page-rewire work blocks on 4.5.
- **Explicitly out of scope:** template-driven dashboard composition (`recommended_charts`, `recommended_events`). Defer until template count and per-template page divergence justify it; view endpoints have a fixed shape per page in v1, with only `tiles` varying by template.
- **Exit criterion:** UI exercised end-to-end in browser; charts, scrubber, compare view, and reports render correctly against fresh-Postgres data; one fetch per page on initial paint (verified via devtools network panel for the results page).

### Phase 4.5 — Overview view bundle expansion (blocker for page rewires)

Phase 3's `/runs/{id}/views/overview` covers only the tiles surface (`derived_metrics`) + four `round_metrics` columns + an event-type histogram. The chart components on `/results/[runId]`, `/compare`, and `/reports/[reportId]` consume seven more fields off the legacy `result` payload — none of which are reachable from the existing view, the existing `round_metrics` table, or any other typed surface today. Phase 4 cannot ship the page rewire ("one fetch per page on initial paint" exit criterion at line 294) until the view bundle grows to cover them.

Without this phase the only paths forward are: (a) keep `simulationService.getResult` alive permanently, freezing `runs.result` as a long-term column and stranding goals 1–3 from the plan summary; or (b) make each chart its own resource fetch and accept 8+ requests per page paint. Both reject Phase 4's design premise.

**Fields the bundle must add** (consumed by `chartDataFromResult` at `frontend/src/lib/api/adapters/runs.ts:2124` and `metricsFromResult` at `:1529`):

| Slice | Source today | Where it lives in Postgres | Chart/metric driven |
|---|---|---|---|
| `price_history` (Array<{token→price}>) | `result.price_history` | Per-round, embedded in `round_snapshots.state.prices` (verify shape during the cut) | Price chart, TWAP, maxDrawdown, rollingVol, compositeScore |
| `volume_history` (number[]) | `result.volume_history` | `round_metrics.volume` rollup rows (whole-market) | Cumulative-volume chart fallback |
| `liquidity_history` (number[]) | `result.liquidity_history` | `round_snapshots.state.market.total_liquidity` per round | Liquidity chart (non-Whirlpool), legacy `lpProfitability` fallback |
| `agent_final_states` (id→{role, balances, cumulative_volume, realized_pnl}) | `result.agent_final_states` | Final-round entries in `round_snapshots.state.agents` + final-round `round_metrics` per `agent_id` | PnL bars, agent rows, `lpFeeYieldRatio` quote-token detection |
| `whirlpool_snapshots` (Array<state.metrics.whirlpool>) | `result.round_snapshots[].metrics.whirlpool` | `round_snapshots.state.metrics.whirlpool` per round | Tick crossings, active L, total/baseline/agent LP L, feesA/feesB |
| `fee_history` (Array<Record<destination, Record<token, fee>>>) | `result.fee_history` | **Not in any table today.** See "fees aggregation" below. | Cumulative fees, fees-by-destination, primary `lpFeeYieldRatio` path (reads per-token) |
| Sandwich totals (`bundles_landed`, `bundles_submitted`, `realized_ev_lamports`) | `result.metadata.sandwich_*` | Already in `result.metadata`; cheapest path is to inline a `sandwich_summary` slice from `runs.result`'s metadata, OR aggregate `SANDWICH_*` events grouped by `(landed?, submitted?)`. | `stressScore`, sandwich bundle counters |
| `replay_diff` (per-metric ErrorBand) | `result.metadata.replay_diff` | Already in `result.metadata`. Pass through verbatim. | Calibration band overlays |

In theory the first five are recomputable from `round_metrics` + `round_snapshots` without schema change. In practice `round_snapshots.state` stores `agent_states` and `market_state` as opaque msgpack `to_bytes()` blobs (see `engine/snapshots.py:152-175`), so reconstructing per-token balances or market liquidity per round requires deserialising agent/market state inside SQL workers — a deferred bridge with its own correctness surface.

For v1, Phase 4.5 takes the smaller cut: `fee_history` lives in the new `fees` table; the other slices are JSONB plucks off `runs.result` exposed through the view. The user-visible contract (one fetch per page) holds. The cost is that `runs.result` cannot be deleted in Phase 5 without first migrating the remaining slices — either by reshaping `round_snapshots.state` to carry typed columns or by adding more aggregation tables alongside `fees`. That is now Phase 5's explicit pre-work, not a surprise.

**Fees aggregation (new):**

Add a `fees` aggregation table populated at run completion by the same engine-side flush that already builds `round_metrics`. Keyed on `(run_id, round, destination, token_id)` because consumers (e.g. `sumLpFeesForToken` at `runs.ts:1739`) read per-token (`fee_history[r]["lp"][token]`); summing across tokens at the storage layer would force every consumer to track token mix separately:

```sql
CREATE TABLE fees (
  run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  round        INT  NOT NULL,
  destination  TEXT NOT NULL,   -- 'lp', 'protocol', 'burn', etc.
  token_id     TEXT NOT NULL,   -- mirrors engine's `_fee_history[r][dest][token]`
  amount       NUMERIC NOT NULL,
  PRIMARY KEY (run_id, round, destination, token_id)
);

CREATE INDEX fees_run_round ON fees (run_id, round);
```

The engine maintains `_fee_history: list[dict[destination, dict[token_id, amount]]]` directly (see `engine/simulation.py:172`) — there are no per-event `FEE_*` events to aggregate. So `pg_store.save_run_artifacts` reads `result["fee_history"]` and inserts rows from it in the same transaction as `round_metrics`. Earlier drafts proposed `INSERT … SELECT FROM events WHERE type LIKE 'FEE_%'`; the engine doesn't emit those events today, so the source is the result payload that already flows through the save path.

Rejected alternatives:
- *Live aggregation on every view fetch*: the SELECT is cheap but runs on every overview render. Materializing once at completion matches `round_metrics`'s pattern and keeps the view's SQL plan flat.
- *New columns on `round_metrics`*: forces a fixed destination set, breaks the moment a new fee model lands.

**Backend changes:**

- `pg_store.py`: new `query_fee_history(run_id)` method that reconstructs the nested `Array<Record<destination, Record<token, amount>>>` shape from the `fees` table, plus `_maybe_materialise_fees` called from both `save_run_artifacts` (after a result write) and `update_run` (when status flips terminal — the live-streaming path). New `query_overview_result_slices(run_id)` helper that JSONB-plucks `price_history`, `volume_history`, `liquidity_history`, `agent_final_states`, `round_snapshots[].metrics.whirlpool`, and the sandwich/replay metadata fields off `runs.result` in one SELECT.
- `routers/runs.py:get_run_overview`: extend the response with the new slices. Bundle remains best-effort consistent for terminal runs; running runs still get a stale view (acceptable per existing docstring at line 354).

**Frontend changes:**

- Extend `OverviewView` in `frontend/src/lib/services/runViewService.ts` with typed slices matching the new wire shape.
- Move `chartDataFromResult` + `metricsFromResult` to read from `OverviewView` instead of `ApiRunResult`. Function signatures change; call sites in `results/[runId]/page.tsx`, `compare/page.tsx`, `reports/[reportId]/page.tsx` follow.
- `simulationService.getResult` / `getResultCharts` / `getMetrics` can then be deleted as part of Phase 5; until Phase 4.5 ships, leave them alive.

**Tests:**

- Golden-file diff for the new view shape against the existing fixtures under `tests/golden/`. Same canonical specs; the assertion target moves from `/runs/{id}/result` to `/runs/{id}/views/overview`. Float-comparison rules from the testing-strategy section apply.
- Integration test exercising the fees aggregation against at least one run that emits fees to ≥2 destinations.
- Property test: `fees` rows for a given `(run_id, round)` reconstruct `result.fee_history[round]` exactly — the table is the source of truth once it's been populated, so equality is the contract.

**Exit criterion:** `/runs/{id}/views/overview` carries every field today's `chartDataFromResult` and `metricsFromResult` read off `result`; the legacy `/runs/{id}/result` endpoint and `runs.result` column become candidates for Phase 5 deletion; results-page rewire is unblocked.

**Open questions:**
- Verify the engine's `round_snapshots.state` shape exposes `prices`, `total_liquidity`, and the Whirlpool metrics block at the keys the chart adapters expect. If not, decide whether to reshape the engine output or denormalize during the flush.
- Whether `fees` should also carry a `gross_amount` / `protocol_share_bps` so the table doubles as a fee-economics dashboard surface. Defer until a chart demands it.
- Whether `sandwich_summary` deserves its own column on `runs` (small, fixed schema) versus being aggregated from events. Lean toward column; the data is fixed-shape and trivially small.

### Phase 5 — Cleanup

- Delete `FileSystemArtifactStore` and JSON-blob helpers from `store.py`.
- Delete `.defi_sim_artifacts/` from repo; remove from Dockerfile and `docker-compose.yml` mounts.
- Rewrite `scripts/purge-local.sh` to `TRUNCATE … CASCADE`.
- Remove tests that asserted on filesystem layout; consolidate fixtures.
- Remove `metadata.derived_metrics` from the run response (lives on `view.tiles` now).
- Delete any frontend service methods that fetched the legacy mega-result.
- **Phase 5.1 — Typed columns + dual-write.** Added `runs.{price_history, agent_final_states, derived_metrics, replay_diff, sandwich_summary}` JSONB columns. `pg_store.save_run_artifacts` shreds each slice off the result payload alongside the existing `result` column write. No readers flipped yet — column still authoritative.
- **Phase 5.2 — Migrate internal callers off `runs.result`.** Overview view and `/runs/compare` read the typed columns directly (no more `query_overview_result_slices` JSONB pluck). `reports.py` / `share.py` / `embed.py` keep calling `store.get_run_result`, which is now a composer stitching the legacy shape back from typed columns + `round_snapshots` + `fees`. Added a `runs.metadata JSONB` column carrying the full metadata bag so engine-internal fields (`parameter_state`, `fee_destination_balances`, `submission_priors`, `oracle_costs_per_slot`) round-trip for golden parity. Stashed `predicted` on `runs.summary` so the replay payload survives the cutover. Dropped `volume_history` / `liquidity_history` from the view bundle and `metadata_diff` from `/runs/compare` — engine never populated the former, no frontend consumer for the latter.
- **Phase 5.3 — Retire the column + endpoint.** Dropped `runs.result` and `GET /runs/{id}/result`. `_maybe_materialise_fees` now takes `fee_history` directly via parameter instead of reading the deprecated column. Replay tests migrated to call `store.get_run_result` (the composer) directly. Golden harness captures `run_result` from the composer too — same shape, different source, regression coverage preserved.
- Update `docs/backend.md` to reflect new storage layer.

## Testing strategy — behavioral equivalence

The hardest constraint: same spec + same seed must produce **identical numbers** at every chart/metric the user sees. The data shape underneath changes; the user-facing output does not.

### Capture-before / compare-after (the linchpin)

Must happen on `main` before any refactor work begins. Once the refactor starts, "what did the old code do?" is no longer answerable.

1. Pick 3–5 canonical specs covering interesting regimes:
   - Noise-only baseline
   - Sandwich attacker + victim
   - LP rebalancing
   - Multi-asset market
   - Calibration scenario (mirrors existing dashboard)
2. Fix seeds. Commit specs to `tests/golden/specs/`.
3. Capture script runs each spec end-to-end on `main`, dumping every API response a chart consumes to `tests/golden/<spec_name>/<endpoint>.json`:
   - `/runs/{id}` / `/runs/{id}/spec` / `/runs/{id}/result`
   - `/runs/{id}/events` (paginated dump)
   - `/runs/{id}/rounds/{n}` for representative n
   - `/runs/compare`, report endpoints
4. Also snapshot raw engine output (full event list, final result object, per-round states) — ground truth downstream of the engine, upstream of storage.
5. Commit `tests/golden/` to the repo.

After the refactor, the same harness runs as a CI test; diffs fail the build.

**Float handling:** canonical round to a fixed precision (e.g. 12 sig figs) before comparison, or `math.isclose(rel_tol=1e-9, abs_tol=1e-12)` field-by-field. Pick once, apply everywhere.

**Discipline rule:** if a golden test diffs, it's a regression to investigate — **not** a fixture to regenerate. Regenerating goldens to make tests pass is the failure mode that defeats the whole strategy.

### Layered equivalence tests

Three layers catching different regression classes:

1. **Engine determinism.** Same spec + same seed → byte-identical event sequence. Verify on `main` before starting. Common nondeterminism sources to audit: dict iteration order in serialization, Python `hash()` randomization (`PYTHONHASHSEED`), `time.time()` in event timestamps, set iteration order in agent selection.
2. **Storage round-trip.** Write events to Postgres, read back via new endpoints, reconstruct legacy `result.json` / `events.json` shapes in a test helper, assert equality with the engine's in-memory output. Catches "we lost a field in translation."
3. **API contract.** Golden-file diff as described above. Catches "the chart endpoint now returns a slightly different shape."

### Property tests (catch what golden tests bake in)

Independent of storage, must hold for any run:

- Conservation laws (e.g. sum of agent PnL ≈ 0 in a closed system — or whatever the actual invariant is).
- `events.count(type=ACTION_EXECUTED) + events.count(type=ACTION_FAILED) == total_attempted_actions`.
- `round_metrics[r].volume == sum(events at round r where action_type='swap').amount`.
- Monotonicity (round numbers, event_ids).

These guard against subtle bugs that golden files would silently bake in.

### Test infrastructure

- **Engine layer** tests: run the simulator directly, no storage, no API. Verify determinism and math. Fast.
- **Stack layer** tests: full engine + persistence + HTTP fetch. Slower; only the golden suite runs here.
- **Postgres in tests:** session-scoped container via `testcontainers-python`; transactional rollback per test for speed. If that proves too slow, fall back to a shared CI Postgres with schema-per-worker.
- **Frontend visual regression (optional, recommended):** Playwright + screenshot diffing on 2–3 key chart pages against a deterministic run. Catches "numbers correct but chart rendered wrong."

### Sequencing of test work within the phased plan

- **Phase 0:** capture golden files from `main`; commit them. Add the harness as a test that runs against new Postgres (initially failing).
- **Phase 1:** golden tests still failing (storage changed, API unchanged). Expected.
- **Phase 3:** golden tests start going green as endpoints land. Investigate every diff.
- **Phase 5:** golden suite green; promoted to a CI gate.

## Cross-cutting concerns

- **Test runtime cost.** 181 Python test files, most exercise the real engine. Transactional rollback per test in a session-scoped Postgres container is the only way iteration stays fast. Get this right in Phase 0 — it dominates the rest of the project.
- **Memory envelope.** The engine already holds all events in memory before persistence; bulk-insert at end keeps the envelope unchanged. If runs grow 10× later, switch to streaming `COPY` during the run (small follow-up).
- **Live streaming endpoint** (`/simulations/{id}/events`) unchanged — still reads from in-memory `event_bus.history`. Only persistence at completion changes.
- **Reports.** Current ZIP-of-blobs design becomes generated-on-demand from SQL queries. If users later need shareable point-in-time bundles, add object storage then.
- **Indexes.** Verify `EXPLAIN` plans for the chart-driving queries once a real run's data is in. Indexes in the schema sketch should cover the planned filters; confirm before declaring done.

## Risk-ordered watch list

1. **Test infrastructure speed.** Biggest unknown. Fallback plan: shared CI Postgres with schema-per-worker if `testcontainers` ergonomics or speed disappoint.
2. **Frontend chart component assumptions.** Easy to underestimate how deeply UI code assumed `result.json`'s shape. Phase 4 may carry hidden scope.
3. **Reports endpoint redesign.** Current ZIP-of-blobs has no obvious 1:1 replacement. Decide early whether on-demand SQL export is enough or whether stored bundles are required.
4. **Engine determinism.** If the engine isn't actually deterministic today, the golden strategy collapses. Audit before Phase 0 starts.
5. **Bulk event insert performance.** 2,859 events/run today; psycopg3 `COPY` will handle it easily. Watch when run length scales.

## Cutover

No backward compatibility required.

1. Merge PR.
2. `docker compose down -v` (drops SQLite volume).
3. `docker compose up` (fresh Postgres, fresh schema).
4. App returns on empty state. Done.

## Open questions

- Should report manifests carry the exact metric-query parameters so reports are reproducible later, or is "snapshot of current data" sufficient?
- Vercel Postgres vs. external managed Postgres for production — does the deployment story constrain pool size / connection model?
- Is engine determinism actually a property today, or aspiration? (Must verify before Phase 0.)
- At what template count do we promote `recommended_charts` / `recommended_events` from "deferred" to scheduled work? Current floor: ~10 templates with materially different page shapes.
