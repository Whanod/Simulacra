"""PRD US-010 line 745: PriorityFeeMarketUpdatedEvent emission.

After the per-slot observe loop, ``SolanaLikeExecution.execute_slot`` must
emit ``EventType.PRIORITY_FEE_MARKET_UPDATED`` once per touched account
whose post-update percentile distribution has shifted by more than the
configured relative threshold (default 5%).
"""

from __future__ import annotations

from defi_sim.core.types import PriorityFeeMarketUpdatedEvent, SwapAction
from defi_sim.engine.events import Event, EventType
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.priority_fee_market import PriorityFeeMarket
from defi_sim.engine.scheduler import LockedAction
from defi_sim.engine.slot import ExecutedAction, SlotContext


def _executor(action, slot_index):
    return ExecutedAction(
        action=action, execution_cost=0, cost_token=None, succeeded=True
    )


def _run_slot(
    model: SolanaLikeExecution,
    *,
    slot: int,
    pool: str,
    cu_price: int,
    events: list[Event],
    count: int = 1,
) -> None:
    actions = [
        SwapAction(
            agent_id=f"trader_{i}",
            compute_unit_limit=200_000,
            compute_unit_price_micro_lamports=cu_price,
        )
        for i in range(count)
    ]

    def resolver(action):
        return LockedAction(action=action, write_locks=frozenset({pool}))

    ctx = SlotContext(
        slot=slot,
        pending_actions=actions,
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: events.append(event),
        resolve_locks=resolver,
    )
    model.execute_slot(ctx)


def test_first_observation_emits_update_event() -> None:
    """First observation for an account emits the event (no prior to compare)."""
    model = SolanaLikeExecution()
    pool = "Whirlpool/SOL/USDC"
    events: list[Event] = []

    _run_slot(model, slot=0, pool=pool, cu_price=10_000, events=events)

    update_events = [
        e for e in events if e.type == EventType.PRIORITY_FEE_MARKET_UPDATED
    ]
    assert len(update_events) == 1
    payload = update_events[0].data["priority_fee_market_updated"]
    assert isinstance(payload, PriorityFeeMarketUpdatedEvent)
    assert payload.account_id == pool
    assert payload.previous_percentiles is None
    assert payload.percentiles[50] == 10_000
    assert payload.threshold == 0.05


def test_small_change_does_not_emit_update_event() -> None:
    """A second observation that moves no percentile by more than 5% is silent."""
    model = SolanaLikeExecution()
    pool = "Whirlpool/SOL/USDC"
    events: list[Event] = []

    _run_slot(model, slot=0, pool=pool, cu_price=10_000, events=events)
    events.clear()
    _run_slot(model, slot=1, pool=pool, cu_price=10_200, events=events)

    update_events = [
        e for e in events if e.type == EventType.PRIORITY_FEE_MARKET_UPDATED
    ]
    assert update_events == []


def test_large_change_emits_update_event() -> None:
    """A second observation that moves a percentile by >5% emits the event."""
    model = SolanaLikeExecution()
    pool = "Whirlpool/SOL/USDC"
    events: list[Event] = []

    # Warm with a stable distribution of 10 observations at 10_000, then
    # deliver 10 more at 20_000 in a single slot — enough to shift every
    # rank-percentile in the (n=20) sorted buffer past the 5% threshold.
    _run_slot(model, slot=0, pool=pool, cu_price=10_000, events=events, count=10)
    events.clear()
    _run_slot(model, slot=1, pool=pool, cu_price=20_000, events=events, count=10)

    update_events = [
        e for e in events if e.type == EventType.PRIORITY_FEE_MARKET_UPDATED
    ]
    assert len(update_events) == 1
    payload = update_events[0].data["priority_fee_market_updated"]
    assert payload.previous_percentiles == {
        25: 10_000, 50: 10_000, 75: 10_000, 90: 10_000, 99: 10_000,
    }
    assert payload.percentiles[99] == 20_000


def test_threshold_is_configurable_via_market_constructor() -> None:
    """A larger threshold suppresses events that the default threshold would emit."""
    pool = "Whirlpool/SOL/USDC"

    # Same delta, two thresholds:
    permissive = SolanaLikeExecution(
        priority_fee_market=PriorityFeeMarket(update_event_threshold=2.0)
    )
    strict = SolanaLikeExecution(
        priority_fee_market=PriorityFeeMarket(update_event_threshold=0.05)
    )
    permissive_events: list[Event] = []
    strict_events: list[Event] = []

    # Both: warm with 10 observations at 10_000, then deliver 10 at 15_000
    # (a +50% jump) in one slot. Permissive threshold (200%) suppresses;
    # strict threshold (5%) emits.
    _run_slot(permissive, slot=0, pool=pool, cu_price=10_000, events=permissive_events, count=10)
    _run_slot(strict, slot=0, pool=pool, cu_price=10_000, events=strict_events, count=10)
    permissive_events.clear()
    strict_events.clear()
    _run_slot(permissive, slot=1, pool=pool, cu_price=15_000, events=permissive_events, count=10)
    _run_slot(strict, slot=1, pool=pool, cu_price=15_000, events=strict_events, count=10)

    assert [e for e in permissive_events if e.type == EventType.PRIORITY_FEE_MARKET_UPDATED] == []
    assert len([e for e in strict_events if e.type == EventType.PRIORITY_FEE_MARKET_UPDATED]) == 1
