"""Postgres connection-pool factory and schema bootstrap.

Portable: any libpq-compatible DATABASE_URL works (Vercel/Neon/RDS/local).
Pool sizing defaults are conservative so the same module runs unchanged in
short-lived serverless functions and long-lived containers.

Schema is managed by Alembic. ``apply_schema()`` runs ``alembic upgrade head``
programmatically (idempotent — Alembic checks ``alembic_version`` and no-ops
when already at head). The public surface (``apply_schema``, ``ensure_ready``,
``_schema_applied``) is preserved so existing callers — pg_store, tests,
scripts/capture_goldens — do not change.
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
REPO_ROOT_ENV: Final = "DEFI_SIM_REPO_ROOT"
ALEMBIC_INI_ENV: Final = "DEFI_SIM_ALEMBIC_INI"

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


def _locate_alembic_ini() -> Path:
    """Resolve alembic.ini across dev (CWD == repo root), tests, and the
    installed-wheel runtime where ``__file__`` lives in site-packages.

    Precedence:
      1. ``$DEFI_SIM_ALEMBIC_INI`` — explicit override.
      2. ``$DEFI_SIM_REPO_ROOT/alembic.ini`` — set in both Dockerfiles.
      3. ``$CWD/alembic.ini`` — covers ``pytest`` / ``alembic`` invocations
         run from the repo root.
      4. ``parents[3]/alembic.ini`` from this file — covers editable installs
         (``pip install -e .``) where ``__file__`` is under ``src/``.
    """
    candidates: list[Path] = []
    explicit = os.environ.get(ALEMBIC_INI_ENV)
    if explicit:
        candidates.append(Path(explicit))
    repo_root = os.environ.get(REPO_ROOT_ENV)
    if repo_root:
        candidates.append(Path(repo_root) / "alembic.ini")
    candidates.append(Path.cwd() / "alembic.ini")
    try:
        candidates.append(Path(__file__).resolve().parents[3] / "alembic.ini")
    except IndexError:  # pragma: no cover - defensive
        pass
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError(
        "Could not locate alembic.ini. Set $DEFI_SIM_ALEMBIC_INI or "
        "$DEFI_SIM_REPO_ROOT, or run from a working directory that contains "
        "alembic.ini. Searched: " + ", ".join(str(p) for p in candidates)
    )


def apply_schema(pool: ConnectionPool | None = None) -> None:
    """Run ``alembic upgrade head`` against DATABASE_URL. Idempotent.

    The ``pool`` argument is retained for API compatibility with the
    pre-Alembic implementation; Alembic owns its own connection lifecycle, so
    the argument is intentionally ignored. Repeated calls within the same
    process short-circuit via ``_schema_applied``.
    """
    global _schema_applied
    if _schema_applied:
        return
    # Validate DATABASE_URL up front so the failure mode matches the
    # pre-Alembic implementation (RuntimeError, not an obscure Alembic error).
    database_url()

    from alembic import command
    from alembic.config import Config

    ini_path = _locate_alembic_ini()
    cfg = Config(str(ini_path))
    # env.py reads DATABASE_URL itself; no need to set sqlalchemy.url here.
    command.upgrade(cfg, "head")

    _schema_applied = True


def ensure_ready() -> ConnectionPool:
    """Convenience: get the pool and ensure schema is applied."""
    apply_schema()
    return get_pool()


__all__ = [
    "DATABASE_URL_ENV",
    "POOL_MIN_SIZE_ENV",
    "POOL_MAX_SIZE_ENV",
    "REPO_ROOT_ENV",
    "ALEMBIC_INI_ENV",
    "apply_schema",
    "database_url",
    "ensure_ready",
    "get_pool",
    "reset_pool",
]
