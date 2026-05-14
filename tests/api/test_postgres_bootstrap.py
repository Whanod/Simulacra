"""Smoke test for the Postgres backend bootstrap.

Validates that the testcontainers fixture starts, the DDL applies cleanly, and
all expected tables exist. This is the entry point for the Phase 0 work — if
this passes on CI, the rest of the migration has a place to stand.
"""

from __future__ import annotations

EXPECTED_TABLES = {
    "runs",
    "events",
    "round_metrics",
    "round_snapshots",
    "named_snapshots",
    "sweeps",
    "reports",
}


def test_schema_creates_expected_tables(pg_pool):
    with pg_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            tables = {row[0] for row in cur.fetchall()}
    assert EXPECTED_TABLES.issubset(tables), f"missing: {EXPECTED_TABLES - tables}"


def test_apply_schema_is_idempotent(pg_pool):
    from defi_sim_api.backend import db as db_module

    db_module._schema_applied = False  # force re-apply
    db_module.apply_schema(pg_pool)  # should not raise

    with pg_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM runs")
            assert cur.fetchone()[0] == 0


def test_truncate_between_tests_clears_runs(pg_pool):
    """If a row leaks into the next test, the pg_pool fixture has failed."""
    with pg_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (run_id, source, status, spec) "
                "VALUES ('leak-canary', 'test', 'completed', '{}'::jsonb)"
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM runs WHERE run_id = 'leak-canary'")
            assert cur.fetchone()[0] == 1


def test_canary_row_does_not_leak(pg_pool):
    with pg_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM runs WHERE run_id = 'leak-canary'")
            assert cur.fetchone()[0] == 0
