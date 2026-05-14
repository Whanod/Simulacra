"""Postgres connection-pool factory and schema bootstrap.

Portable: any libpq-compatible DATABASE_URL works (Vercel/Neon/RDS/local).
Pool sizing defaults are conservative so the same module runs unchanged in
short-lived serverless functions and long-lived containers.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Final

import psycopg
from psycopg_pool import ConnectionPool

DATABASE_URL_ENV: Final = "DATABASE_URL"
POOL_MIN_SIZE_ENV: Final = "DEFI_SIM_PG_POOL_MIN"
POOL_MAX_SIZE_ENV: Final = "DEFI_SIM_PG_POOL_MAX"

_SCHEMA_PATH: Final = Path(__file__).parent / "schema.sql"

_lock = threading.Lock()
_pool: ConnectionPool | None = None
_schema_applied = False

logger = logging.getLogger(__name__)


def database_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(
            f"{DATABASE_URL_ENV} is not set. Postgres backend cannot start without it."
        )
    return url


def _pool_size_bounds() -> tuple[int, int]:
    min_size = int(os.environ.get(POOL_MIN_SIZE_ENV) or "1")
    max_size = int(os.environ.get(POOL_MAX_SIZE_ENV) or "10")
    if min_size < 0 or max_size < min_size:
        raise ValueError(
            f"Invalid pool sizing: min={min_size} max={max_size}; "
            f"min must be >= 0 and max must be >= min."
        )
    return min_size, max_size


def get_pool() -> ConnectionPool:
    """Return the process-wide connection pool, creating it lazily."""
    global _pool
    if _pool is not None:
        return _pool
    with _lock:
        if _pool is None:
            min_size, max_size = _pool_size_bounds()
            _pool = ConnectionPool(
                conninfo=database_url(),
                min_size=min_size,
                max_size=max_size,
                open=True,
            )
    return _pool


def reset_pool() -> None:
    """Close the pool and clear the schema-applied flag. Tests call this."""
    global _pool, _schema_applied
    with _lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:  # pragma: no cover - defensive on shutdown
                logger.exception("error closing pool")
        _pool = None
        _schema_applied = False


def apply_schema(pool: ConnectionPool | None = None) -> None:
    """Apply schema.sql idempotently. Safe to call repeatedly."""
    global _schema_applied
    if _schema_applied:
        return
    target = pool or get_pool()
    ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
    with target.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    _schema_applied = True


def ensure_ready() -> ConnectionPool:
    """Convenience: get the pool and ensure schema is applied."""
    pool = get_pool()
    apply_schema(pool)
    return pool


__all__ = [
    "DATABASE_URL_ENV",
    "POOL_MIN_SIZE_ENV",
    "POOL_MAX_SIZE_ENV",
    "apply_schema",
    "database_url",
    "ensure_ready",
    "get_pool",
    "reset_pool",
]
