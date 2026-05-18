"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-18

Single consolidated revision that captures the defi-sim artifact-store schema
as of the postgres-migration branch. The pre-alembic ``schema.sql`` was a
running collage of CREATE + ALTER + DROP statements meant to be re-applied
idempotently against long-lived databases; this revision folds the resolved
state of those statements into clean CREATEs.

Subsequent revisions should be standalone files generated with
``alembic revision -m "<message>"``.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UPGRADE_DDL = """
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
    -- Typed slices peeled off the legacy ``result`` JSONB. Each column is
    -- nullable: live / replay rows and pre-completion runs leave them NULL
    -- until a result write fires.
    price_history       JSONB,
    agent_final_states  JSONB,
    derived_metrics     JSONB,
    replay_diff         JSONB,
    sandwich_summary    JSONB,
    -- Full ``result.metadata`` bag carried whole so the composer
    -- (``get_run_result``) can round-trip engine-internal fields the goldens
    -- pin. The narrow ``derived_metrics`` / ``sandwich_summary`` columns
    -- above are denormalised hot-path reads for the overview view.
    metadata            JSONB,
    -- Privy v1: owner-scope rows to the logged-in user (NULL = anonymous /
    -- open-mode / service-key write — see auth.py).
    owner_id            TEXT
);

CREATE INDEX IF NOT EXISTS runs_created_at_idx
    ON runs (created_at DESC, run_id DESC);
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

CREATE INDEX IF NOT EXISTS events_run_round
    ON events (run_id, round);
CREATE INDEX IF NOT EXISTS events_run_agent_round
    ON events (run_id, agent_id, round) WHERE agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS events_run_type
    ON events (run_id, type);
CREATE INDEX IF NOT EXISTS events_run_correlation
    ON events (run_id, correlation_id) WHERE correlation_id IS NOT NULL;

-- agent_id is NOT NULL with a sentinel for whole-market rollup, because
-- Postgres treats NULL as distinct in unique constraints — a NULLable
-- agent_id would allow multiple rollup rows per (run_id, round). The
-- sentinel literal lives in :data:`pg_store.ROLLUP_AGENT_ID` and must stay
-- in sync with the DEFAULT below.
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

-- Per-(round, destination, token) fee splits, materialised at run completion
-- from ``SimulationResult.fee_history``.
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
    state           JSONB NOT NULL,
    owner_id        TEXT
);

CREATE INDEX IF NOT EXISTS named_snapshots_run_idx
    ON named_snapshots (run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS named_snapshots_owner_idx
    ON named_snapshots (owner_id, created_at DESC) WHERE owner_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id     TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    spec         JSONB NOT NULL,
    rows         JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary      JSONB NOT NULL DEFAULT '{}'::jsonb,
    owner_id     TEXT
);

CREATE INDEX IF NOT EXISTS sweeps_created_at_idx
    ON sweeps (created_at DESC, sweep_id DESC);
CREATE INDEX IF NOT EXISTS sweeps_owner_created_idx
    ON sweeps (owner_id, created_at DESC, sweep_id DESC) WHERE owner_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS reports (
    report_id   TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    manifest    JSONB NOT NULL,
    owner_id    TEXT
);

CREATE INDEX IF NOT EXISTS reports_created_at_idx
    ON reports (created_at DESC, report_id DESC);
CREATE INDEX IF NOT EXISTS reports_owner_created_idx
    ON reports (owner_id, created_at DESC, report_id DESC) WHERE owner_id IS NOT NULL;
"""


_DOWNGRADE_DDL = """
DROP TABLE IF EXISTS reports CASCADE;
DROP TABLE IF EXISTS sweeps CASCADE;
DROP TABLE IF EXISTS named_snapshots CASCADE;
DROP TABLE IF EXISTS round_snapshots CASCADE;
DROP TABLE IF EXISTS fees CASCADE;
DROP TABLE IF EXISTS round_metrics CASCADE;
DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS runs CASCADE;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_DDL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_DDL)
