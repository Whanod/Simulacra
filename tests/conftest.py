"""Top-level pytest configuration shared across the test tree.

Hosts the session-scoped Postgres fixture used by the new artifact-store and
API tests. Container startup is lazy — only tests that request the
``postgres_url`` (or a downstream) fixture pay the cost.

Skipping rules: if Docker is unavailable or testcontainers fails to start the
container, dependent tests are skipped (not failed). Postgres-free tests are
unaffected.
"""

from __future__ import annotations

import os

import pytest

try:  # testcontainers may be unavailable in some envs
    from testcontainers.postgres import PostgresContainer
except Exception:  # pragma: no cover - optional dep
    PostgresContainer = None  # type: ignore[assignment]


@pytest.fixture(scope="session")
def postgres_container():
    """Boot a Postgres 16 container for the test session. Skips if unavailable."""
    if PostgresContainer is None:
        pytest.skip("testcontainers[postgres] is not installed")
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # docker missing, etc.
        pytest.skip(f"could not start postgres container: {exc}")
    try:
        yield container
    finally:
        try:
            container.stop()
        except Exception:
            pass


@pytest.fixture(scope="session")
def postgres_url(postgres_container) -> str:
    """libpq URL to the session-scoped container, normalised to ``postgresql://``."""
    url = postgres_container.get_connection_url()
    # testcontainers returns SQLAlchemy-style URL (``postgresql+psycopg2://``);
    # psycopg wants the plain scheme.
    if "+" in url.split("://", 1)[0]:
        scheme, rest = url.split("://", 1)
        scheme = scheme.split("+", 1)[0]
        url = f"{scheme}://{rest}"
    return url


@pytest.fixture()
def pg_pool(postgres_url, monkeypatch):
    """Per-test pool: applies schema, truncates artifact tables between tests."""
    from defi_sim_api.backend import db as db_module

    monkeypatch.setenv(db_module.DATABASE_URL_ENV, postgres_url)
    db_module.reset_pool()
    pool = db_module.ensure_ready()
    # Truncate all artifact tables for a clean slate, but keep the schema.
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE events, round_metrics, fees, round_snapshots, "
                "named_snapshots, runs, sweeps, reports CASCADE"
            )
        conn.commit()
    try:
        yield pool
    finally:
        db_module.reset_pool()
