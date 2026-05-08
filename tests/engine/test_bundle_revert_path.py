"""Bundle revert path tests for ``_execute_bundle_atomically`` (PRD US-005 line 424).

The bundle revert path used by US-011 (Jito bundle auction) step 4 is
implemented in terms of the existing ``atomic_state_boundary`` rollback
primitive. These tests exercise the helper end-to-end:

  * all-success commits the cumulative bundle effect
  * any-failure reverts every state mutation from positions ``0..j``
"""

from __future__ import annotations

import copy

from defi_sim.core.types import SwapAction
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
            "agent_id": "trader-1",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
    ],
    "num_rounds": 1,
    "seed": 11,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
    },
}


def _balance_of(engine, agent_id: str, token: str):
    for agent in engine._agents:
        if agent.agent_id == agent_id:
            return agent.state.balances.get(token, 0)
    raise AssertionError(f"agent {agent_id} not found")


def test_bundle_atomic_execution_commits_on_all_success() -> None:
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))

    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    pre_sol = _balance_of(engine, "trader-1", "SOL")
    agent = next(a for a in engine._agents if a.agent_id == "trader-1")
    pre_volume = agent.state.cumulative_volume

    bundle = [
        SwapAction(agent_id="trader-1", token_in="USDC", token_out="SOL", amount_in=1_000),
        SwapAction(agent_id="trader-1", token_in="USDC", token_out="SOL", amount_in=2_000),
    ]

    outcome = engine._execute_bundle_atomically(bundle, round_num=0, ts=0)

    assert outcome["reverted"] is False
    assert outcome["failed_at_index"] is None
    assert len(outcome["executed"]) == 2

    # Both swaps committed: SOL out, market reserves shifted, cumulative
    # volume increased.
    post_sol = _balance_of(engine, "trader-1", "SOL")
    assert post_sol > pre_sol
    assert engine._market._reserves != pre_market_reserves  # type: ignore[attr-defined]
    assert agent.state.cumulative_volume > pre_volume


def test_bundle_atomic_execution_reverts_on_failure() -> None:
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))

    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    pre_sol = _balance_of(engine, "trader-1", "SOL")
    agent = next(a for a in engine._agents if a.agent_id == "trader-1")
    pre_volume = agent.state.cumulative_volume

    bundle = [
        # First action succeeds.
        SwapAction(agent_id="trader-1", token_in="USDC", token_out="SOL", amount_in=5_000),
        # Second action fails: token_in does not exist on this market.
        SwapAction(agent_id="trader-1", token_in="DOES_NOT_EXIST", token_out="SOL", amount_in=1),
    ]

    outcome = engine._execute_bundle_atomically(bundle, round_num=0, ts=0)

    assert outcome["reverted"] is True
    assert outcome["failed_at_index"] == 1
    assert outcome["failed_reason"]
    assert outcome["executed"] == []

    # State must be exactly the pre-bundle state — first action's effects
    # (SOL acquired, cumulative_volume bumped, reserves shifted) are undone.
    assert _balance_of(engine, "trader-1", "SOL") == pre_sol
    assert engine._market._reserves == pre_market_reserves  # type: ignore[attr-defined]
    assert agent.state.cumulative_volume == pre_volume


def test_bundle_atomic_execution_reverts_on_first_action_failure() -> None:
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))

    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    pre_sol = _balance_of(engine, "trader-1", "SOL")

    bundle = [
        SwapAction(agent_id="trader-1", token_in="DOES_NOT_EXIST", token_out="SOL", amount_in=1),
        SwapAction(agent_id="trader-1", token_in="USDC", token_out="SOL", amount_in=1_000),
    ]

    outcome = engine._execute_bundle_atomically(bundle, round_num=0, ts=0)

    assert outcome["reverted"] is True
    assert outcome["failed_at_index"] == 0
    assert outcome["executed"] == []
    assert _balance_of(engine, "trader-1", "SOL") == pre_sol
    assert engine._market._reserves == pre_market_reserves  # type: ignore[attr-defined]
