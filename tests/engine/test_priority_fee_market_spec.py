"""PriorityFeeMarketSpec mirrors PriorityFeeMarket (US-010, PRD line 746)."""

from __future__ import annotations

from defi_sim.engine.priority_fee_market import PriorityFeeMarket
from defi_sim.engine.specs import PriorityFeeMarketSpec


def test_priority_fee_market_spec_defaults_match_prd() -> None:
    spec = PriorityFeeMarketSpec()
    assert spec.window_slots == 150
    assert spec.ewma_half_life_slots == 30
    assert spec.floor_micro_lamports == 1


def test_priority_fee_market_spec_to_market_uses_spec_values() -> None:
    spec = PriorityFeeMarketSpec(
        window_slots=200, ewma_half_life_slots=50, floor_micro_lamports=42
    )
    market = spec.to_priority_fee_market()
    assert isinstance(market, PriorityFeeMarket)
    assert market.quote("never_seen", 50) == 42


def test_priority_fee_market_spec_from_dict_uses_defaults_when_partial() -> None:
    spec = PriorityFeeMarketSpec.from_dict({})
    assert spec == PriorityFeeMarketSpec()


def test_priority_fee_market_spec_from_dict_round_trips_overrides() -> None:
    spec = PriorityFeeMarketSpec.from_dict(
        {
            "window_slots": 300,
            "ewma_half_life_slots": 60,
            "floor_micro_lamports": 7,
        }
    )
    assert spec == PriorityFeeMarketSpec(
        window_slots=300, ewma_half_life_slots=60, floor_micro_lamports=7
    )
