"""Spec-builder forwarding for ChainReorgForkSpec / BlockhashHistory (PRD US-014
line 1101 + line 1109).

Before this fix, Solana specs accepted execution params for compute
budget, scheduler, submission priors, priority fee market and bundle
auction — but had no spec surface for fork probability or for the
rolling blockhash history. Tests resorted to mutating
``execution._fork_spec`` and ``execution._blockhash_history`` directly,
which the public spec contract did not support. This file pins the
spec-builder forwarding so a public spec can configure both.
"""

from __future__ import annotations

import copy

from defi_sim.engine.api import build_engine
from defi_sim.engine.blockhash import BlockhashHistory
from defi_sim.engine.fork import ChainReorgForkSpec


SOLANA_SPEC: dict = {
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
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
    ],
    "num_rounds": 3,
    "snapshot_interval": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "fifo"},
        "gas_model": {"type": "compute_unit"},
        "params": {"cost_token": "USDC"},
    },
}


def test_spec_forwards_fork_spec_dict_to_execution_model() -> None:
    spec = copy.deepcopy(SOLANA_SPEC)
    spec["execution"]["params"]["fork_spec"] = {
        "fork_probability_per_slot": 0.25,
        "max_reorg_depth_slots": 4,
        "seed": 11,
    }
    engine = build_engine(spec)
    fork_spec = engine._execution_model._fork_spec
    assert isinstance(fork_spec, ChainReorgForkSpec)
    assert fork_spec.fork_probability_per_slot == 0.25
    assert fork_spec.max_reorg_depth_slots == 4
    assert fork_spec.seed == 11


def test_spec_forwards_blockhash_history_bool_to_execution_model() -> None:
    spec = copy.deepcopy(SOLANA_SPEC)
    spec["execution"]["params"]["blockhash_history"] = True
    engine = build_engine(spec)
    history = engine._execution_model._blockhash_history
    assert isinstance(history, BlockhashHistory)


def test_spec_forwards_blockhash_history_validity_window() -> None:
    spec = copy.deepcopy(SOLANA_SPEC)
    spec["execution"]["params"]["blockhash_history"] = {"validity_slots": 50}
    engine = build_engine(spec)
    history = engine._execution_model._blockhash_history
    assert isinstance(history, BlockhashHistory)
    assert history.validity_slots == 50


def test_engine_records_blockhash_per_slot_when_history_enabled() -> None:
    # PRD US-014 line 1101: when a BlockhashHistory is configured, the
    # engine populates it as it advances slots. After three rounds the
    # latest recorded blockhash references the current slot, so admit-time
    # expiry checks have something to compare against.
    spec = copy.deepcopy(SOLANA_SPEC)
    spec["execution"]["params"]["blockhash_history"] = True
    engine = build_engine(spec)
    engine.run()
    history = engine._execution_model._blockhash_history
    assert isinstance(history, BlockhashHistory)
    # `latest()` raises LookupError on an empty history; reaching this
    # assertion is itself the proof that per-slot recording occurred.
    assert history.latest() == "bh-3"
