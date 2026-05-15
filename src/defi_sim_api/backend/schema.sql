-- Initial Postgres schema for defi-sim artifact storage.
-- See docs/postgres-migration-plan.md for the design rationale.
--
-- Applied idempotently on pool open via db.apply_schema(). No migration
-- tooling yet; if this schema changes pre-launch, just wipe and re-apply.

CREATE TABLE IF NOT EXISTS runs (
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
    spec                JSONB NOT NULL,
    summary             JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Typed slices peeled off the legacy ``result`` JSONB. Phase 5.3
    -- dropped the ``result`` column itself once every reader had moved
    -- onto these typed surfaces (and onto ``round_snapshots`` / ``fees``
    -- for per-round data). Each column is nullable: live / replay rows
    -- and pre-completion runs leave them NULL until a result write
    -- fires.
    price_history       JSONB,                       -- list[dict[token, price]] per round
    agent_final_states  JSONB,                       -- dict[agent_id, AgentState dict]
    derived_metrics     JSONB,                       -- result.metadata.derived_metrics (tiles)
    replay_diff         JSONB,                       -- result.replay_diff (top-level), replay runs only
    sandwich_summary    JSONB,                       -- 3 sandwich_* keys from result.metadata, or NULL
    -- Full ``result.metadata`` bag carried whole so the composer
    -- (`get_run_result`) can round-trip engine-internal fields the
    -- goldens pin (``fee_destination_balances``, ``parameter_state``,
    -- ``submission_priors``, ``oracle_costs_per_slot``). The narrow
    -- ``derived_metrics`` / ``sandwich_summary`` columns above are kept
    -- as denormalised hot-path reads for the overview view; the
    -- composer reads ``metadata`` directly.
    metadata            JSONB
);

-- Idempotent column additions for environments whose schema predates them.
-- Hosted Postgres pools may have a long-lived database that picked up
-- ``runs`` from an earlier apply_schema(); the IF NOT EXISTS keeps re-runs
-- safe.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS price_history      JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS agent_final_states JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS derived_metrics    JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS replay_diff        JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS sandwich_summary   JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS metadata           JSONB;
-- Phase 5.3: legacy monolithic ``result`` JSONB retired in favour of the
-- typed columns above. Dropping idempotently so a re-apply on a database
-- that predates 5.3 cleans up cleanly.
ALTER TABLE runs DROP COLUMN IF EXISTS result;

-- Privy v1: owner-scope rows to the logged-in user (NULL = anonymous /
-- open-mode / service-key write — see auth.py).
ALTER TABLE runs ADD COLUMN IF NOT EXISTS owner_id TEXT;

CREATE INDEX IF NOT EXISTS runs_created_at_idx ON runs (created_at DESC, run_id DESC);
CREATE INDEX IF NOT EXISTS runs_owner_created_idx
    ON runs (owner_id, created_at DESC, run_id DESC) WHERE owner_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS events (
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
    data            JSONB,
    PRIMARY KEY (run_id, event_id)
);

CREATE INDEX IF NOT EXISTS events_run_round       ON events (run_id, round);
CREATE INDEX IF NOT EXISTS events_run_agent_round ON events (run_id, agent_id, round) WHERE agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS events_run_type        ON events (run_id, type);
CREATE INDEX IF NOT EXISTS events_run_correlation ON events (run_id, correlation_id) WHERE correlation_id IS NOT NULL;

-- Populated by the engine via INSERT…SELECT at run completion.
-- agent_id is NOT NULL with a sentinel for whole-market rollup, because
-- Postgres treats NULL as distinct in unique constraints — a NULLable
-- agent_id would allow multiple rollup rows per (run_id, round). The
-- sentinel literal lives in :data:`pg_store.ROLLUP_AGENT_ID` (kept in
-- sync with the DEFAULT below); :meth:`_bulk_insert_events` rejects any
-- event whose data.agent_id matches it, so a user-named agent cannot
-- silently collide with the Phase 3 rollup INSERT.
CREATE TABLE IF NOT EXISTS round_metrics (
    run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    round           INT  NOT NULL,
    agent_id        TEXT NOT NULL DEFAULT '__defi_sim_rollup__',
    pnl             NUMERIC,
    volume          NUMERIC,
    num_actions     INT,
    num_failed      INT,
    gas_spent       NUMERIC,
    price_snapshot  JSONB,
    PRIMARY KEY (run_id, round, agent_id)
);

-- Pull the default forward for environments that already applied an earlier
-- schema with the old '__total__' sentinel — the table itself stays
-- compatible (no rollup rows have been written yet), but the DEFAULT and
-- the pg_store constant should match.
ALTER TABLE round_metrics ALTER COLUMN agent_id SET DEFAULT '__defi_sim_rollup__';

-- Per-(round, destination, token) fee splits, materialised at run completion
-- from ``SimulationResult.fee_history``. The engine maintains fees as a list
-- indexed by round whose entries are ``{destination: {token_id: amount}}``;
-- consumers (``sumLpFeesForToken`` at ``frontend/.../runs.ts``) read per-token,
-- so we preserve token granularity instead of summing here. See Phase 4.5 in
-- ``docs/postgres-migration-plan.md`` for the design rationale.
CREATE TABLE IF NOT EXISTS fees (
    run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    round        INT  NOT NULL,
    destination  TEXT NOT NULL,
    token_id     TEXT NOT NULL,
    amount       NUMERIC NOT NULL,
    PRIMARY KEY (run_id, round, destination, token_id)
);

CREATE INDEX IF NOT EXISTS fees_run_round ON fees (run_id, round);

CREATE TABLE IF NOT EXISTS round_snapshots (
    run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    round_number  INT  NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    state         JSONB NOT NULL,
    PRIMARY KEY (run_id, round_number)
);

CREATE TABLE IF NOT EXISTS named_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    source_run_id   TEXT,
    simulation_id   TEXT,
    round_number    INT NOT NULL,
    label           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    state           JSONB NOT NULL
);

ALTER TABLE named_snapshots ADD COLUMN IF NOT EXISTS owner_id TEXT;

CREATE INDEX IF NOT EXISTS named_snapshots_run_idx ON named_snapshots (run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS named_snapshots_owner_idx
    ON named_snapshots (owner_id, created_at DESC) WHERE owner_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id     TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    spec         JSONB NOT NULL,
    rows         JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary      JSONB NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE sweeps ADD COLUMN IF NOT EXISTS owner_id TEXT;

CREATE INDEX IF NOT EXISTS sweeps_created_at_idx ON sweeps (created_at DESC, sweep_id DESC);
CREATE INDEX IF NOT EXISTS sweeps_owner_created_idx
    ON sweeps (owner_id, created_at DESC, sweep_id DESC) WHERE owner_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS reports (
    report_id   TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    manifest    JSONB NOT NULL
);

ALTER TABLE reports ADD COLUMN IF NOT EXISTS owner_id TEXT;

CREATE INDEX IF NOT EXISTS reports_created_at_idx ON reports (created_at DESC, report_id DESC);
CREATE INDEX IF NOT EXISTS reports_owner_created_idx
    ON reports (owner_id, created_at DESC, report_id DESC) WHERE owner_id IS NOT NULL;
