"""US-010 PRD line 747: builder forwards `priority_fee_market` spec.

Verifies that `_build_solana_like_execution` consumes
`execution.params.priority_fee_market` and applies the values to the
resulting `SolanaLikeExecution.priority_fee_market`.
"""

from __future__ import annotations

from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.specs import (
    ExecutionSpec,
    PriorityFeeMarketSpec,
    build_execution_model,
)


def test_solana_execution_consumes_priority_fee_market_dict() -> None:
    spec = ExecutionSpec(
        type="solana_like",
        params={
            "priority_fee_market": {
                "window_slots": 200,
                "ewma_half_life_slots": 60,
                "floor_micro_lamports": 42,
                "update_event_threshold": 0.1,
            }
        },
    )
    exec_model = build_execution_model(spec)
    assert isinstance(exec_model, SolanaLikeExecution)
    market = exec_model.priority_fee_market
    # Floor is applied as a quote on never-seen accounts.
    assert market.quote("never_seen", 50) == 42
    assert market.update_event_threshold == 0.1


def test_solana_execution_priority_fee_market_defaults_when_unspecified() -> None:
    spec = ExecutionSpec(type="solana_like", params={})
    exec_model = build_execution_model(spec)
    assert isinstance(exec_model, SolanaLikeExecution)
    market = exec_model.priority_fee_market
    assert market.quote("never_seen", 50) == 1
    assert market.update_event_threshold == 0.05


def test_solana_execution_accepts_typed_priority_fee_market_spec() -> None:
    typed = PriorityFeeMarketSpec(
        window_slots=300,
        ewma_half_life_slots=75,
        floor_micro_lamports=7,
        update_event_threshold=0.2,
    )
    spec = ExecutionSpec(
        type="solana_like",
        params={"priority_fee_market": typed},
    )
    exec_model = build_execution_model(spec)
    assert isinstance(exec_model, SolanaLikeExecution)
    market = exec_model.priority_fee_market
    assert market.quote("never_seen", 50) == 7
    assert market.update_event_threshold == 0.2
