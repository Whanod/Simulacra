"""Integration tests: PriorityFeeMarket wired into SolanaLikeExecution.execute_slot.

PRD US-010 line 738: every admitted locked action's write-lock set must update
the engine's priority fee market. Read-locks are observational only.
"""

from __future__ import annotations

import numpy as np

from defi_sim.core.types import SwapAction
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.priority_fee_market import PriorityFeeMarket
from defi_sim.engine.scheduler import LockedAction
from defi_sim.engine.slot import ExecutedAction, SlotContext


def _executor(action, slot_index):
    return ExecutedAction(
        action=action,
        execution_cost=0,
        cost_token=None,
        succeeded=True,
    )


def test_execute_slot_observes_write_locks() -> None:
    """PRD line 738: write-locked accounts on admitted actions update the market."""
    model = SolanaLikeExecution()
    pool = "Whirlpool/SOL/USDC"
    swap = SwapAction(
        agent_id="trader",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=12_345,
    )

    def resolver(action):
        return LockedAction(action=action, write_locks=frozenset({pool}))

    ctx = SlotContext(
        slot=7,
        pending_actions=[swap],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        resolve_locks=resolver,
    )
    outcome = model.execute_slot(ctx)

    assert len(outcome.executed) == 1
    assert model.priority_fee_market.quote(pool, 50) == 12_345


def test_execute_slot_does_not_observe_read_only_account() -> None:
    """PRD line 743: read-locked accounts must not move the market."""
    model = SolanaLikeExecution()
    read_pool = "Oracle/PythSOL"
    write_pool = "Whirlpool/SOL/USDC"
    swap = SwapAction(
        agent_id="trader",
        compute_unit_limit=200_000,
        compute_unit_price_micro_lamports=9_999,
    )

    def resolver(action):
        return LockedAction(
            action=action,
            read_locks=frozenset({read_pool}),
            write_locks=frozenset({write_pool}),
        )

    ctx = SlotContext(
        slot=3,
        pending_actions=[swap],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        resolve_locks=resolver,
    )
    model.execute_slot(ctx)

    assert model.priority_fee_market.quote(read_pool, 50) == 1
    assert model.priority_fee_market.quote(write_pool, 50) == 9_999


def test_hot_account_fee_floor_rises_under_simulated_congestion() -> None:
    """PRD line 765: 50 agents/slot for 200 slots write-locking the same hot
    pool with priority prices uniformly random in [100k, 1M] micro-lamports.

    The p50 quote must rise from the floor to within [400k, 600k] —
    the expected median of a uniform distribution over [100k, 1M].
    """
    rng = np.random.default_rng(seed=42)
    pool = "Whirlpool/SOL/USDC"
    model = SolanaLikeExecution()

    def resolver(action):
        return LockedAction(action=action, write_locks=frozenset({pool}))

    for slot in range(200):
        actions = [
            SwapAction(
                agent_id=f"agent_{i}",
                compute_unit_limit=200_000,
                compute_unit_price_micro_lamports=int(rng.integers(100_000, 1_000_001)),
            )
            for i in range(50)
        ]
        ctx = SlotContext(
            slot=slot,
            pending_actions=actions,
            ordering_context=OrderingContext(),
            executor=_executor,
            emit=lambda event: None,
            resolve_locks=resolver,
        )
        model.execute_slot(ctx)

    p50 = model.priority_fee_market.quote(pool, 50)
    assert 400_000 <= p50 <= 600_000, f"hot pool p50 should land in [400k, 600k], got {p50}"


def test_synthetic_congestion_p50_matches_50th_percentile_recent_action() -> None:
    """PRD line 751: 100 actions write-locking ``pool_A`` with ascending
    priority prices spread across 200 slots. After the run, the engine's
    priority fee market quote at p50 should land at the price level of the
    50th-percentile recent action.

    Drives the full ``SolanaLikeExecution.execute_slot`` admit/order/observe
    path so the assertion exercises the integration contract (PRD line 738),
    not the unit-level ``observe`` call.
    """
    pool = "Whirlpool/hot"
    model = SolanaLikeExecution()

    def resolver(action):
        return LockedAction(action=action, write_locks=frozenset({pool}))

    prices = [1_000 * (i + 1) for i in range(100)]  # 1_000, 2_000, ..., 100_000
    # 100 actions across 200 slots: one every other slot.
    for idx, price in enumerate(prices):
        slot = idx * 2
        action = SwapAction(
            agent_id=f"agent_{idx}",
            compute_unit_limit=200_000,
            compute_unit_price_micro_lamports=price,
        )
        ctx = SlotContext(
            slot=slot,
            pending_actions=[action],
            ordering_context=OrderingContext(),
            executor=_executor,
            emit=lambda event: None,
            resolve_locks=resolver,
        )
        model.execute_slot(ctx)

    # 50th-percentile of ascending prices [1_000..100_000] is ~50_000.
    p50 = model.priority_fee_market.quote(pool, 50)
    assert abs(p50 - 50_000) <= 1_000, f"expected p50 ~= 50_000, got {p50}"


def test_synthetic_congestion_read_locks_do_not_move_quote() -> None:
    """PRD line 752: read-locking actions on ``pool_A`` do not affect its quote.

    Companion validation to line 751: the same synthetic congestion shape, but
    every action only read-locks the pool. The quote must stay at the floor
    because read-locks are observational only (PRD line 743).
    """
    pool = "Whirlpool/read-only"
    floor = 11
    model = SolanaLikeExecution(
        priority_fee_market=PriorityFeeMarket(floor_micro_lamports=floor)
    )

    def resolver(action):
        return LockedAction(action=action, read_locks=frozenset({pool}))

    for idx in range(100):
        action = SwapAction(
            agent_id=f"reader_{idx}",
            compute_unit_limit=200_000,
            compute_unit_price_micro_lamports=1_000 * (idx + 1),
        )
        ctx = SlotContext(
            slot=idx * 2,
            pending_actions=[action],
            ordering_context=OrderingContext(),
            executor=_executor,
            emit=lambda event: None,
            resolve_locks=resolver,
        )
        model.execute_slot(ctx)

    assert model.priority_fee_market.quote(pool, 50) == floor


def test_synthetic_congestion_untouched_pool_returns_floor() -> None:
    """PRD line 753: a separate ``pool_B`` with no traffic returns the
    configured market floor, even after heavy congestion on ``pool_A``.
    """
    hot_pool = "Whirlpool/hot"
    cold_pool = "Whirlpool/cold"
    floor = 1
    model = SolanaLikeExecution(
        priority_fee_market=PriorityFeeMarket(floor_micro_lamports=floor)
    )

    def resolver(action):
        return LockedAction(action=action, write_locks=frozenset({hot_pool}))

    for idx in range(100):
        action = SwapAction(
            agent_id=f"agent_{idx}",
            compute_unit_limit=200_000,
            compute_unit_price_micro_lamports=1_000 * (idx + 1),
        )
        ctx = SlotContext(
            slot=idx * 2,
            pending_actions=[action],
            ordering_context=OrderingContext(),
            executor=_executor,
            emit=lambda event: None,
            resolve_locks=resolver,
        )
        model.execute_slot(ctx)

    assert model.priority_fee_market.quote(cold_pool, 50) == floor


def test_cold_account_fee_floor_stays_at_base() -> None:
    """PRD line 766: rare low-bid traffic on a cold pool keeps p50 below the
    configured floor — i.e. quote stays clamped at the floor.
    """
    rng = np.random.default_rng(seed=7)
    cold_pool = "Whirlpool/cold"
    floor = 50_000
    model = SolanaLikeExecution(
        priority_fee_market=PriorityFeeMarket(floor_micro_lamports=floor)
    )

    def resolver(action):
        return LockedAction(action=action, write_locks=frozenset({cold_pool}))

    for slot in range(200):
        # Touch the cold pool only ~5% of the time, with low CU prices.
        if rng.random() >= 0.05:
            continue
        action = SwapAction(
            agent_id="rare_trader",
            compute_unit_limit=200_000,
            compute_unit_price_micro_lamports=int(rng.integers(0, 1_000)),
        )
        ctx = SlotContext(
            slot=slot,
            pending_actions=[action],
            ordering_context=OrderingContext(),
            executor=_executor,
            emit=lambda event: None,
            resolve_locks=resolver,
        )
        model.execute_slot(ctx)

    p50 = model.priority_fee_market.quote(cold_pool, 50)
    assert p50 < floor + 1, f"cold pool p50 should stay clamped at floor {floor}, got {p50}"
