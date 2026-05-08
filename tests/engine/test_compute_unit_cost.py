"""ComputeUnitCost real Solana fee formula (PRD task 0.4.5).

Locks `cost(action, round)` and `breakdown(action, round)` to the mainnet
formula `5_000 * num_signers + ceil(price_micro * cu_limit / 1_000_000)`,
including the 50/50 base-fee burn/validator split and ceil-rounding of
the priority fee.
"""

from __future__ import annotations

import warnings

import pytest

from defi_sim.core.types import Action, SwapAction
from defi_sim.engine.gas import (
    ALT_LOOKUP_CU_PER_ENTRY,
    ALT_LOOKUP_CU_PER_TABLE,
    DEFAULT_CU_LIMITS,
    ComputeUnitCost,
    FeeBreakdown,
    alt_lookup_cu,
)


def test_single_signer_baseline_no_priority() -> None:
    cost = ComputeUnitCost().cost(SwapAction(agent_id="a"), 0)
    assert cost == 5_000


def test_multi_signer_baseline() -> None:
    action = SwapAction(agent_id="a", num_required_signatures=3)
    assert ComputeUnitCost().cost(action, 0) == 15_000


def test_priority_fee_exact() -> None:
    action = SwapAction(
        agent_id="a",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )
    assert ComputeUnitCost().cost(action, 0) == 5_000 + 200_000


def test_priority_fee_ceil_rounding() -> None:
    action = SwapAction(
        agent_id="a",
        compute_unit_limit=1,
        compute_unit_price_micro_lamports=1,
    )
    assert ComputeUnitCost().cost(action, 0) == 5_001


def test_priority_fee_zero_price_yields_no_compute_cost() -> None:
    action = SwapAction(
        agent_id="a",
        compute_unit_limit=1_000_000,
        compute_unit_price_micro_lamports=0,
    )
    assert ComputeUnitCost().cost(action, 0) == 5_000


def test_fee_breakdown_splits_base_fee_and_priority_fee() -> None:
    fb = ComputeUnitCost().breakdown(SwapAction(agent_id="a"), 0)
    assert isinstance(fb, FeeBreakdown)
    assert fb.base_fee_lamports == 5_000
    assert fb.base_fee_burned_lamports == 2_500
    assert fb.base_fee_to_validator_lamports == 2_500
    assert fb.priority_fee_lamports == 0
    assert fb.total_lamports == 5_000


def test_breakdown_with_priority_fee() -> None:
    action = SwapAction(
        agent_id="a",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )
    fb = ComputeUnitCost().breakdown(action, 0)
    assert fb.priority_fee_lamports == 200_000
    assert fb.total_lamports == 205_000


def test_default_cu_limit_fallback_per_action_type() -> None:
    # SwapAction with explicit price but no limit → uses DEFAULT_CU_LIMITS[SwapAction]=200_000.
    action = SwapAction(agent_id="a", compute_unit_price_micro_lamports=1_000_000)
    expected_priority = DEFAULT_CU_LIMITS[SwapAction]
    fb = ComputeUnitCost().breakdown(action, 0)
    assert fb.priority_fee_lamports == expected_priority
    assert fb.total_lamports == 5_000 + expected_priority


def test_unknown_action_type_uses_default_units() -> None:
    class UnregisteredAction(Action):
        pass

    action = UnregisteredAction(agent_id="a", compute_unit_price_micro_lamports=1_000_000)
    # default_units defaults to 1 → priority = ceil(1_000_000 * 1 / 1_000_000) = 1.
    fb = ComputeUnitCost().breakdown(action, 0)
    assert fb.priority_fee_lamports == 1
    # Override default_units shifts the fallback (deprecated path emits a
    # DeprecationWarning per task 0.4.6 but the override still applies for
    # the deprecation window).
    with pytest.warns(DeprecationWarning):
        fb_override = ComputeUnitCost(default_units=50_000).breakdown(action, 0)
    assert fb_override.priority_fee_lamports == 50_000


def test_legacy_constructor_args_are_accepted_but_ignored_with_warning() -> None:
    # PRD task 0.4.6 — legacy ctor args still accepted for one release
    # cycle, must emit DeprecationWarning, and must not change the fee
    # computed for actions whose subtype is in DEFAULT_CU_LIMITS.
    action = SwapAction(agent_id="a")
    with pytest.warns(DeprecationWarning, match="parameter-free"):
        model = ComputeUnitCost(
            unit_costs={SwapAction: 999_999},
            base_cost=42,
        )
    # Registered subtype → DEFAULT_CU_LIMITS wins; ignored args have no effect.
    assert model.cost(action, 0) == 5_000


def test_constructing_without_legacy_args_emits_no_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        ComputeUnitCost()  # must not raise


def test_multi_signer_with_priority_fee() -> None:
    # 3 signers + 200_000 CU at 1_000_000 micro-lamports/CU:
    # base = 15_000, priority = 200_000, total = 215_000.
    action = SwapAction(
        agent_id="a",
        num_required_signatures=3,
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )
    fb = ComputeUnitCost().breakdown(action, 0)
    assert fb.base_fee_lamports == 15_000
    assert fb.priority_fee_lamports == 200_000
    assert fb.total_lamports == 215_000


def test_breakdown_multi_signer_burn_validator_split() -> None:
    # 4 signers → base = 20_000; even split = 10_000 burned / 10_000 validator.
    action = SwapAction(agent_id="a", num_required_signatures=4)
    fb = ComputeUnitCost().breakdown(action, 0)
    assert fb.base_fee_lamports == 20_000
    assert fb.base_fee_burned_lamports == 10_000
    assert fb.base_fee_to_validator_lamports == 10_000
    assert fb.priority_fee_lamports == 0
    assert fb.total_lamports == 20_000


def test_validator_reward_helper_includes_priority_and_half_base_fee() -> None:
    # 2 signers (base 10_000, validator share 5_000) + 200_000 priority lamports.
    action = SwapAction(
        agent_id="a",
        num_required_signatures=2,
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )
    assert action.priority_lamports() == 200_000
    # validator gets priority + half base fee = 200_000 + 5_000.
    assert action.validator_reward_lamports() == 205_000


def test_default_cu_limit_is_marked_synthetic() -> None:
    # PRD task 0.4 line 518: fallback-derived CU limits must surface
    # `cu_limit_source` metadata so reports do not imply observed mainnet
    # CU usage.
    explicit = ComputeUnitCost().breakdown(
        SwapAction(agent_id="a", compute_unit_limit=42_000), 0
    )
    assert explicit.cu_limit_source == "explicit"

    synthetic = ComputeUnitCost().breakdown(SwapAction(agent_id="a"), 0)
    assert synthetic.cu_limit_source == "synthetic_default"

    class UnregisteredAction(Action):
        pass

    with pytest.warns(DeprecationWarning):
        legacy_model = ComputeUnitCost(default_units=12_345)
    legacy = legacy_model.breakdown(UnregisteredAction(agent_id="a"), 0)
    assert legacy.cu_limit_source == "legacy_fallback"


def test_alt_lookup_cu_helper_formula() -> None:
    # CALIBRATE-2.1: 100 CU per used table + 10 CU per resolved entry.
    assert alt_lookup_cu(0, 0) == 0
    assert alt_lookup_cu(1, 0) == ALT_LOOKUP_CU_PER_TABLE
    assert alt_lookup_cu(0, 1) == ALT_LOOKUP_CU_PER_ENTRY
    assert alt_lookup_cu(2, 30) == 2 * 100 + 30 * 10


def test_alt_lookup_cu_rejects_negative_counts() -> None:
    with pytest.raises(ValueError):
        alt_lookup_cu(-1, 0)
    with pytest.raises(ValueError):
        alt_lookup_cu(0, -1)


def test_breakdown_charges_alt_lookup_surcharge() -> None:
    # Action annotated with 1 ALT covering 30 entries should pay
    # ceil(price_micro * (100 + 30*10) / 1_000_000) extra priority lamports.
    action = SwapAction(
        agent_id="a",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )
    object.__setattr__(action, "lookup_tables", ["alt-1"])
    object.__setattr__(action, "alt_resolved_entries", 30)
    expected_alt_cu = 100 + 30 * 10  # 400 CU
    fb = ComputeUnitCost().breakdown(action, 0)
    # priority = base 200_000 + 400 (1 micro-lamport-per-CU rate scaled).
    assert fb.priority_fee_lamports == 200_000 + expected_alt_cu


def test_breakdown_no_alt_surcharge_when_no_alt_metadata() -> None:
    # Vanilla action without lookup_tables / alt_resolved_entries → no surcharge.
    action = SwapAction(
        agent_id="a",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )
    fb = ComputeUnitCost().breakdown(action, 0)
    assert fb.priority_fee_lamports == 200_000


@pytest.mark.parametrize("cu_limit", [1, 1_000, 50_000, 200_000, 1_400_000])
def test_cost_is_monotonic_in_cu_limit(cu_limit: int) -> None:
    # For any fixed price_micro > 0, increasing cu_limit strictly increases cost.
    price_micro = 1_000_000
    model = ComputeUnitCost()
    low = SwapAction(
        agent_id="lo",
        compute_unit_limit=cu_limit,
        compute_unit_price_micro_lamports=price_micro,
    )
    high = SwapAction(
        agent_id="hi",
        compute_unit_limit=cu_limit + 1,
        compute_unit_price_micro_lamports=price_micro,
    )
    assert model.cost(high, 0) > model.cost(low, 0)


@pytest.mark.parametrize("signers", [1, 2, 3, 5, 10, 32])
def test_cost_is_monotonic_in_signers(signers: int) -> None:
    # Strict monotonicity in num_required_signatures with priority fee held flat.
    action_low = SwapAction(agent_id="lo", num_required_signatures=signers)
    action_high = SwapAction(agent_id="hi", num_required_signatures=signers + 1)
    model = ComputeUnitCost()
    assert model.cost(action_high, 0) > model.cost(action_low, 0)
    # Increment is exactly 5_000 per signer, regardless of priority fee.
    assert model.cost(action_high, 0) - model.cost(action_low, 0) == 5_000
