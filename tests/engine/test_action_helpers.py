"""Helper methods on `Action` for Solana CU ergonomics.

`set_compute_unit_limit` / `set_compute_unit_price` are fluent setters.
`priority_lamports()` returns the Solana priority-fee math, resolved
against `DEFAULT_CU_LIMIT_FALLBACK` until the per-action-type registry
(task 0.4.3) lands. `validator_reward_lamports()` adds the validator's
half of the 5_000-per-signer base fee.
"""

from __future__ import annotations

import math

from defi_sim.core.types import (
    DEFAULT_CU_LIMIT_FALLBACK,
    Action,
    SwapAction,
)


def test_set_compute_unit_limit_returns_self_and_assigns() -> None:
    action = SwapAction(agent_id="a")
    result = action.set_compute_unit_limit(150_000)
    assert result is action
    assert action.compute_unit_limit == 150_000


def test_set_compute_unit_price_returns_self_and_assigns() -> None:
    action = SwapAction(agent_id="a")
    result = action.set_compute_unit_price(2_500)
    assert result is action
    assert action.compute_unit_price_micro_lamports == 2_500


def test_setters_chainable() -> None:
    action = (
        SwapAction(agent_id="a")
        .set_compute_unit_limit(100_000)
        .set_compute_unit_price(10)
    )
    assert action.compute_unit_limit == 100_000
    assert action.compute_unit_price_micro_lamports == 10


def test_priority_lamports_zero_when_no_price_set() -> None:
    assert SwapAction(agent_id="a").priority_lamports() == 0


def test_priority_lamports_uses_explicit_fields() -> None:
    action = SwapAction(
        agent_id="a",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )
    # 1_000_000 micro * 200_000 cu / 1_000_000 = 200_000 lamports
    assert action.priority_lamports() == 200_000


def test_priority_lamports_falls_back_to_default_cu_limit() -> None:
    action = SwapAction(agent_id="a", compute_unit_price_micro_lamports=1_000_000)
    expected = math.ceil(1_000_000 * DEFAULT_CU_LIMIT_FALLBACK / 1_000_000)
    assert action.priority_lamports() == expected


def test_priority_lamports_ceil_rounding() -> None:
    action = SwapAction(
        agent_id="a",
        compute_unit_limit=1,
        compute_unit_price_micro_lamports=1,
    )
    # ceil(1 * 1 / 1_000_000) == 1
    assert action.priority_lamports() == 1


def test_validator_reward_includes_half_base_fee_and_priority() -> None:
    action = SwapAction(
        agent_id="a",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=1_000_000,
    )
    # base = 5_000 * 1, validator base share = 5_000 - 2_500 = 2_500
    # priority = 200_000
    assert action.validator_reward_lamports() == 200_000 + 2_500


def test_validator_reward_multi_signer_baseline_no_priority() -> None:
    action = Action(agent_id="a", num_required_signatures=3)
    # base = 15_000, validator share = 15_000 - 7_500 = 7_500, no priority
    assert action.validator_reward_lamports() == 7_500
