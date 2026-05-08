"""Solana-path PriorityOrdering tests.

`PriorityOrdering` sorts actions by their lamport-equivalent priority
fee, descending. Fixtures express priority via
`compute_unit_price_micro_lamports` (with `compute_unit_limit`).
"""

from __future__ import annotations

from defi_sim.core.types import SwapAction
from defi_sim.engine.ordering import OrderingContext, PriorityOrdering


def test_cu_price_higher_sorts_first_with_same_cu_limit() -> None:
    """Higher `compute_unit_price_micro_lamports` sorts first when all
    actions share the same `compute_unit_limit`.
    """
    actions = [
        SwapAction(
            agent_id="low",
            compute_unit_limit=200_000,
            compute_unit_price_micro_lamports=5,
        ),
        SwapAction(
            agent_id="high",
            compute_unit_limit=200_000,
            compute_unit_price_micro_lamports=100,
        ),
        SwapAction(
            agent_id="mid",
            compute_unit_limit=200_000,
            compute_unit_price_micro_lamports=25,
        ),
    ]

    ordered = PriorityOrdering().order(actions, round=0, context=OrderingContext())
    assert [a.agent_id for a in ordered] == ["high", "mid", "low"]


def test_cu_price_zero_actions_keep_insertion_order() -> None:
    """Actions with zero CU price tie at 0 lamports priority; Python's stable
    sort preserves their insertion order behind any non-zero-priority action."""
    actions = [
        SwapAction(agent_id="z1"),  # no CU price -> 0 priority lamports
        SwapAction(
            agent_id="paid",
            compute_unit_limit=200_000,
            compute_unit_price_micro_lamports=10,
        ),
        SwapAction(agent_id="z2"),  # no CU price -> 0 priority lamports
    ]

    ordered = PriorityOrdering().order(actions, round=0, context=OrderingContext())
    assert ordered[0].agent_id == "paid"
    assert [a.agent_id for a in ordered[1:]] == ["z1", "z2"]


def test_cu_price_total_lamports_drives_order_across_different_cu_limits() -> None:
    """When `compute_unit_limit` differs across actions, the resolved total
    priority lamports (`ceil(price_micro * cu_limit / 1_000_000)`) drives the
    sort — not the raw price-per-CU. A small CU limit at a high CU price can
    still rank below a larger CU limit at a moderate CU price.
    """
    cheap_per_cu_but_big = SwapAction(
        agent_id="big",
        compute_unit_limit=1_000_000,
        compute_unit_price_micro_lamports=10,
    )
    expensive_per_cu_but_small = SwapAction(
        agent_id="small",
        compute_unit_limit=1,
        compute_unit_price_micro_lamports=1,
    )
    # ceil(10 * 1_000_000 / 1_000_000) == 10 lamports
    assert cheap_per_cu_but_big.priority_lamports() == 10
    # ceil(1 * 1 / 1_000_000) == 1 lamport (ceil-rounding)
    assert expensive_per_cu_but_small.priority_lamports() == 1

    ordered = PriorityOrdering().order(
        [expensive_per_cu_but_small, cheap_per_cu_but_big],
        round=0,
        context=OrderingContext(),
    )
    assert [a.agent_id for a in ordered] == ["big", "small"]


def test_cu_price_ceil_rounding_does_not_invert_order() -> None:
    """The ceil-rounding boundary (`price_micro=1`, `cu_limit=1` → 1 lamport)
    must not promote a sub-microlamport priority above an action that resolves
    to a strictly higher integer lamport amount.
    """
    boundary = SwapAction(
        agent_id="boundary",
        compute_unit_limit=1,
        compute_unit_price_micro_lamports=1,
    )
    higher = SwapAction(
        agent_id="higher",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=10,
    )
    assert boundary.priority_lamports() == 1
    assert higher.priority_lamports() == 2  # ceil(10 * 200_000 / 1_000_000) == 2

    ordered = PriorityOrdering().order(
        [boundary, higher], round=0, context=OrderingContext()
    )
    assert [a.agent_id for a in ordered] == ["higher", "boundary"]
