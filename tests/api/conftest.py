"""Shared fixtures for API tests."""

from __future__ import annotations

import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient

from defi_sim_api.main import app
from defi_sim_api import state as sim_state
from defi_sim_api.backend.store import ARTIFACT_ROOT_ENV, get_artifact_store, reset_artifact_store


@pytest.fixture()
def client(monkeypatch):
    """Synchronous test client — resets engine store between tests."""
    sim_state._engines.clear()
    artifact_root = tempfile.mkdtemp(prefix="defi-sim-artifacts-", dir="/tmp")
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, artifact_root)
    reset_artifact_store()
    get_artifact_store()
    with TestClient(app) as c:
        yield c
    sim_state._engines.clear()
    reset_artifact_store()
    shutil.rmtree(artifact_root, ignore_errors=True)


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
