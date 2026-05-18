"""Alembic environment for defi-sim.

The store layer is raw psycopg + SQL; we don't have ORM models, so
``target_metadata`` is ``None`` and autogenerate is disabled. Migrations live
in ``alembic/versions/`` and use ``op.execute(...)`` for DDL.

URL resolution:
- ``DATABASE_URL`` from the environment wins over ``sqlalchemy.url`` in
  ``alembic.ini`` (which is intentionally empty).
- We rewrite ``postgres://`` / ``postgresql://`` to ``postgresql+psycopg://``
  so SQLAlchemy uses psycopg v3 (the driver shipped via the ``[api]`` extra)
  rather than defaulting to the absent psycopg2.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would silence every
    # logger the host application has already created (including
    # defi_sim_api.* loggers wired up at import time). Keep them intact —
    # we only want Alembic's own logger config layered on top.
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _resolve_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        ini_url = config.get_main_option("sqlalchemy.url")
        if not ini_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Alembic cannot run migrations without a "
                "target database (set DATABASE_URL or sqlalchemy.url in alembic.ini)."
            )
        url = ini_url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


config.set_main_option("sqlalchemy.url", _resolve_database_url())

target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (``alembic upgrade --sql``)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against the live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
