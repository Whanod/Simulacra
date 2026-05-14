#!/usr/bin/env python3
"""Capture golden API responses for the Postgres-backed artifact store.

Boots a throwaway Postgres container via testcontainers, runs each canonical
spec end-to-end against the live FastAPI app, and writes the normalised
responses under ``tests/golden/``. The committed output is the cross-backend
equivalence contract enforced by ``tests/api/test_goldens.py``.

Usage::

    python scripts/capture_goldens.py            # capture all specs
    python scripts/capture_goldens.py noise-baseline
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from defi_sim_api import state as sim_state  # noqa: E402
from defi_sim_api.backend import db as db_module  # noqa: E402
from defi_sim_api.backend.store import (  # noqa: E402
    STORE_BACKEND_ENV,
    get_artifact_store,
    reset_artifact_store,
)
from defi_sim_api.main import app  # noqa: E402

from tests.golden.harness import (  # noqa: E402
    GOLDEN_SPECS,
    golden_dir,
    normalise_capture,
    run_spec_and_capture,
    write_captures,
)


def _normalise_pg_url(url: str) -> str:
    """testcontainers returns ``postgresql+psycopg2://``; psycopg wants the plain scheme."""
    scheme, rest = url.split("://", 1)
    if "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    return f"{scheme}://{rest}"


def _truncate_all(pool) -> None:
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE events, round_metrics, fees, round_snapshots, "
                "named_snapshots, runs, sweeps, reports CASCADE"
            )
        conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "specs",
        nargs="*",
        help="Optional list of spec names to capture. Defaults to all.",
    )
    args = ap.parse_args()

    selected = set(args.specs) if args.specs else None
    targets = [s for s in GOLDEN_SPECS if selected is None or s.name in selected]
    if selected and not targets:
        print(f"no matching specs: {sorted(selected)}", file=sys.stderr)
        return 2

    with PostgresContainer("postgres:16-alpine") as container:
        url = _normalise_pg_url(container.get_connection_url())
        os.environ[db_module.DATABASE_URL_ENV] = url
        os.environ[STORE_BACKEND_ENV] = "postgres"
        db_module.reset_pool()
        pool = db_module.ensure_ready()

        for golden in targets:
            _truncate_all(pool)
            reset_artifact_store()
            get_artifact_store()
            sim_state._engines.clear()
            with TestClient(app) as client:
                captures = run_spec_and_capture(client, golden.spec)
            normalised = normalise_capture(captures)

            out_dir = golden_dir(REPO_ROOT, golden.name)
            write_captures(out_dir, normalised)
            print(f"captured {len(normalised)} files for {golden.name} → {out_dir}")

        db_module.reset_pool()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
