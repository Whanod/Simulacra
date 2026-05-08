"""Snapshot-cost smoke test for ``atomic_state_boundary`` (PRD US-005 line 411).

The performance claim in the PRD: ``snapshot must be cheap enough that a
bundle of 5 txs can take 5 snapshots in the typical hot-path budget``.
This test pins the v1 ``copy.deepcopy``-based snapshot/restore cycle to a
generous wall-clock budget on a Phase-1-sized engine — wide enough to
absorb CI jitter but tight enough that an O(N) regression in
``_snapshot_bundle_mutable_state`` (e.g. accidentally deep-copying the
full price-history array per call) would trip it.

Rationale: this is a smoke test, not a benchmark. It guards against
order-of-magnitude regressions; precise calibration belongs in 2.4
profiling. If it flakes, the right move is to investigate the regression
first — only widen the bound if the budget itself is wrong (grep
``OPTIMIZE-2.4`` in src/defi_sim/engine/simulation.py).
"""

from __future__ import annotations

import copy
import time

from defi_sim.engine.api import build_engine


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
            "params": {"collateral": "USDC", "frequency": 1.0},
            "initial_balances": {"USDC": 1_000_000_000},
        },
        {
            "type": "noise",
            "agent_id": "noise-2",
            "params": {"collateral": "USDC", "frequency": 1.0},
            "initial_balances": {"USDC": 1_000_000_000},
        },
    ],
    "num_rounds": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
    },
}


def test_five_snapshot_restore_cycles_fit_hot_path_budget() -> None:
    """5 snapshot/restore cycles must complete well inside 250ms on a
    Phase-1-sized engine. The threshold is generous (~50ms/cycle) so it
    won't flake on slow CI runners; a regression that pushes deep-copy
    cost past O(state) into O(history) would still trip it."""
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))
    engine.run()

    start = time.perf_counter()
    for _ in range(5):
        snap = engine._snapshot_bundle_mutable_state()
        engine._restore_bundle_mutable_state(snap)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.250, (
        f"5 snapshot/restore cycles took {elapsed * 1000:.1f}ms "
        f"(budget: 250ms). See OPTIMIZE-2.4 in engine/simulation.py."
    )


def test_atomic_state_boundary_commit_path_under_budget() -> None:
    """End-to-end smoke through the public ``atomic_state_boundary`` CM.
    Five sequential commit-path entries must fit the same budget as the
    raw helpers — guards against accidental overhead being added to the
    context manager wrapper itself."""
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))
    engine.run()

    start = time.perf_counter()
    for _ in range(5):
        with engine.atomic_state_boundary() as boundary:
            assert not boundary.should_rollback
    elapsed = time.perf_counter() - start

    assert elapsed < 0.250, (
        f"5 atomic_state_boundary commit-path cycles took {elapsed * 1000:.1f}ms "
        f"(budget: 250ms)."
    )
