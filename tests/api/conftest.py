"""Shared fixtures for API tests.

All API tests run against a session-scoped Postgres container (see
``tests/conftest.py``'s ``pg_pool``); the ``client`` fixture wires
``DEFI_SIM_STORE_BACKEND=postgres`` and resets the cached artifact store so
each test gets a clean slate.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from defi_sim_api.main import app
from defi_sim_api import state as sim_state
from defi_sim_api.backend.store import (
    STORE_BACKEND_ENV,
    get_artifact_store,
    reset_artifact_store,
)


@pytest.fixture()
def client(pg_pool, monkeypatch):
    """Synchronous test client backed by the session-scoped Postgres pool.

    ``pg_pool`` (from ``tests/conftest.py``) provisions the container and
    truncates artifact tables between tests; we flip the store backend and
    reset the cached store so the FastAPI app sees a fresh
    ``PostgresArtifactStore`` per test.
    """
    monkeypatch.setenv(STORE_BACKEND_ENV, "postgres")
    sim_state._engines.clear()
    reset_artifact_store()
    get_artifact_store()
    with TestClient(app) as c:
        yield c
    sim_state._engines.clear()
    reset_artifact_store()


CFAMM_SPEC: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "params": {
            "initial_liquidity": 1_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "noise-1",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000},
        },
    ],
    "num_rounds": 5,
    "snapshot_interval": 1,
    "seed": 42,
}

WORLD_SPEC: dict = {
    "market": {
        "type": "world",
        "markets": {
            "amm": {
                "type": "cfamm",
                "tokens": [
                    {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
                    {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
                ],
                "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
            },
            "book": {
                "type": "clob",
                "pairs": [
                    {
                        "base": {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
                        "quote": {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
                    }
                ],
            },
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "obs",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000},
        },
    ],
    "num_rounds": 3,
    "snapshot_interval": 1,
    "seed": 7,
}
