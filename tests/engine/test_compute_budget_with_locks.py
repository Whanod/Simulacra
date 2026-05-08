"""Per-writable-account CU cap with lock resolution (US-008, PRD lines 622-624)."""

from __future__ import annotations

from defi_sim.core.types import ComputeBudgetExhaustedEvent, SwapAction
from defi_sim.engine.compute_budget import ComputeBudget
from defi_sim.engine.events import Event, EventType
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.scheduler import LockedAction
from defi_sim.engine.slot import ExecutedAction, SlotContext


def _executor(action, slot_index):
    return ExecutedAction(
        action=action,
        execution_cost=0,
        cost_token=None,
        succeeded=True,
    )


def test_per_writable_account_cap_drops_hot_pool_overflow() -> None:
    # PRD US-008 line 622: 10 actions write-locking the same
    # `Whirlpool/SOL/USDC` account at 1_500_000 CU each. Per-account cap is
    # 12_000_000 → exactly 8 fit (8 × 1_500_000 = 12_000_000); the remaining 2
    # are deferred with `cu_per_account_exceeded`.
    budget = ComputeBudget(per_slot=60_000_000, per_tx=2_000_000, per_writable_account=12_000_000)
    model = SolanaLikeExecution(compute_budget=budget)
    hot_account = "Whirlpool/SOL/USDC"
    swaps = [
        SwapAction(agent_id=f"a{i}", compute_unit_limit=1_500_000) for i in range(10)
    ]

    def resolver(action):
        return LockedAction(action=action, write_locks=frozenset({hot_account}))

    captured: list[Event] = []
    ctx = SlotContext(
        slot=0,
        pending_actions=list(swaps),
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: captured.append(event),
        resolve_locks=resolver,
    )
    outcome = model.execute_slot(ctx)

    assert len(outcome.executed) == 8
    assert len(outcome.deferred) == 2
    assert outcome.dropped == []
    per_account_events = [
        e for e in captured
        if e.type == EventType.COMPUTE_BUDGET_EXHAUSTED
        and e.data["budget_kind"] == "per_writable_account"
    ]
    assert len(per_account_events) == 2
    for event in per_account_events:
        assert event.data["account"] == hot_account


def test_separate_writable_accounts_dont_share_budget() -> None:
    # PRD US-008 line 623: 10 actions × 1_500_000 CU each, but each one
    # write-locks a different pool. Per-account budgets are independent so all
    # 10 must land.
    budget = ComputeBudget(per_slot=60_000_000, per_tx=2_000_000, per_writable_account=12_000_000)
    model = SolanaLikeExecution(compute_budget=budget)
    swaps = [
        SwapAction(agent_id=f"a{i}", compute_unit_limit=1_500_000) for i in range(10)
    ]
    pool_for_agent = {f"a{i}": f"Whirlpool/pool-{i}" for i in range(10)}

    def resolver(action):
        return LockedAction(
            action=action,
            write_locks=frozenset({pool_for_agent[action.agent_id]}),
        )

    ctx = SlotContext(
        slot=0,
        pending_actions=list(swaps),
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        resolve_locks=resolver,
    )
    outcome = model.execute_slot(ctx)

    assert len(outcome.executed) == 10
    assert outcome.deferred == []
    assert outcome.dropped == []


def test_per_account_event_emitted_on_breach() -> None:
    # PRD US-008 line 624: exactly one ComputeBudgetExhaustedEvent with
    # budget_kind="per_writable_account" per breach.
    budget = ComputeBudget(per_slot=60_000_000, per_tx=2_000_000, per_writable_account=12_000_000)
    model = SolanaLikeExecution(compute_budget=budget)
    hot_account = "Whirlpool/SOL/USDC"
    swaps = [
        SwapAction(agent_id=f"a{i}", compute_unit_limit=1_500_000) for i in range(10)
    ]

    def resolver(action):
        return LockedAction(action=action, write_locks=frozenset({hot_account}))

    captured: list[Event] = []
    ctx = SlotContext(
        slot=7,
        pending_actions=list(swaps),
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: captured.append(event),
        resolve_locks=resolver,
    )
    outcome = model.execute_slot(ctx)

    per_account_events = [
        e for e in captured
        if e.type == EventType.COMPUTE_BUDGET_EXHAUSTED
        and e.data["budget_kind"] == "per_writable_account"
    ]
    # Exactly one event per deferred action — and counts must match.
    assert len(per_account_events) == len(outcome.deferred)
    assert len(per_account_events) == 2
    for event in per_account_events:
        payload = event.data["compute_budget_exhausted"]
        assert isinstance(payload, ComputeBudgetExhaustedEvent)
        assert payload.budget_kind == "per_writable_account"
        assert payload.slot == 7
        assert payload.attempted == 1_500_000
        assert payload.remaining == 0
        assert event.data["account"] == hot_account
    # No other budget_kinds emitted in this scenario (per-slot cap not breached).
    cb_events = [e for e in captured if e.type == EventType.COMPUTE_BUDGET_EXHAUSTED]
    assert len(cb_events) == len(per_account_events)
