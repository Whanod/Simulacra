"""PRD US-003 step 4: ``conflicts`` predicate over LockedActions.

These tests pin down the conflict-graph predicate used by
``PriorityScheduler`` to build connected components. Read/write
semantics:
- read-read overlap on the same account does NOT conflict
- read-write overlap DOES conflict (symmetrically)
- write-write overlap DOES conflict
"""

from __future__ import annotations

from defi_sim.core.types import Action, SwapAction
from defi_sim.engine.execution import DropReason, SolanaLikeExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.scheduler import (
    LockedAction,
    PriorityScheduler,
    SerialScheduler,
    conflicts,
)
from defi_sim.engine.slot import ExecutedAction, SlotContext


def _action(agent: str = "A") -> SwapAction:
    return SwapAction(
        agent_id=agent,
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
    )


def test_conflicts_read_read_no_conflict() -> None:
    a = LockedAction(action=_action("A"), read_locks=frozenset({"acct"}))
    b = LockedAction(action=_action("B"), read_locks=frozenset({"acct"}))
    assert conflicts(a, b) is False
    assert conflicts(b, a) is False


def test_conflicts_read_write_conflict() -> None:
    a = LockedAction(action=_action("A"), read_locks=frozenset({"acct"}))
    b = LockedAction(action=_action("B"), write_locks=frozenset({"acct"}))
    assert conflicts(a, b) is True
    assert conflicts(b, a) is True


def test_conflicts_write_write_conflict() -> None:
    a = LockedAction(action=_action("A"), write_locks=frozenset({"acct"}))
    b = LockedAction(action=_action("B"), write_locks=frozenset({"acct"}))
    assert conflicts(a, b) is True
    assert conflicts(b, a) is True


def test_serial_scheduler_returns_one_lane_with_input_order() -> None:
    """PRD US-003 line 291 / 298: ``SerialScheduler`` returns one lane
    containing all input actions in their original input order.

    The scheduler is order-preserving and must not reorder, drop, or
    split inputs across lanes — even when actions have arbitrary lock
    sets that *would* form multiple connected components under
    ``PriorityScheduler``. Serial mode is the deterministic baseline
    that chain-neutral scenarios fall back to.
    """
    locked_actions = [
        LockedAction(
            action=_action(f"agent-{i}"),
            write_locks=frozenset({f"acct-{i % 3}"}),
        )
        for i in range(7)
    ]

    lanes = SerialScheduler().schedule(locked_actions, slot=0)

    assert len(lanes) == 1
    assert lanes[0].actions == list(locked_actions)
    assert [la.action.agent_id for la in lanes[0].actions] == [
        f"agent-{i}" for i in range(7)
    ]


def test_serial_scheduler_empty_input_yields_one_empty_lane() -> None:
    """Edge case for line 291: zero inputs still produce a single
    (empty) lane so downstream lane-iteration code stays uniform.
    """
    lanes = SerialScheduler().schedule([], slot=0)
    assert len(lanes) == 1
    assert lanes[0].actions == []


def test_priority_scheduler_independent_actions_yield_n_lanes() -> None:
    """PRD US-003 line 299: 10 actions with no shared write-locks yield
    10 single-action lanes — full parallelism when no conflicts exist.

    Each LockedAction holds a distinct ``write_locks={f"pool-{i}"}`` so
    the conflict graph is edgeless; ``PriorityScheduler`` must emit one
    connected component per node.
    """
    locked_actions = [
        LockedAction(
            action=_action(f"agent-{i}"),
            write_locks=frozenset({f"pool-{i}"}),
        )
        for i in range(10)
    ]

    lanes = PriorityScheduler().schedule(locked_actions, slot=0)

    assert len(lanes) == 10
    assert all(len(lane.actions) == 1 for lane in lanes)
    agent_ids = {lane.actions[0].action.agent_id for lane in lanes}
    assert agent_ids == {f"agent-{i}" for i in range(10)}


def test_priority_scheduler_50_independent_swaps_yield_50_lanes() -> None:
    """PRD US-003 line 292 (validation): 50 random non-conflicting swaps
    produce 50 lanes — full parallelism when there's no contention.
    """
    locked_actions = [
        LockedAction(
            action=_action(f"agent-{i}"),
            write_locks=frozenset({f"pool-{i}"}),
        )
        for i in range(50)
    ]

    lanes = PriorityScheduler().schedule(locked_actions, slot=0)

    assert len(lanes) == 50
    assert all(len(lane.actions) == 1 for lane in lanes)


def _priced_action(agent: str, price_micro_lamports: int) -> SwapAction:
    """Swap with a specific compute-unit price so the action has a distinct
    ``scheduler_priority_score``. Same num_required_signatures (default 1)
    and default cu_limit means score differs only in priority fee.
    """
    return SwapAction(
        agent_id=agent,
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        compute_unit_price_micro_lamports=price_micro_lamports,
    )


def test_priority_scheduler_shared_write_lock_yields_one_lane() -> None:
    """PRD US-003 line 300: 10 actions all write-locking account ``A``
    yield exactly 1 lane containing all 10 actions.

    Every pair conflicts on the shared write-lock, so the conflict graph
    is fully connected and union-find merges every node into a single
    component.
    """
    locked_actions = [
        LockedAction(
            action=_action(f"agent-{i}"),
            write_locks=frozenset({"A"}),
        )
        for i in range(10)
    ]

    lanes = PriorityScheduler().schedule(locked_actions, slot=0)

    assert len(lanes) == 1
    assert len(lanes[0].actions) == 10
    agent_ids = {la.action.agent_id for la in lanes[0].actions}
    assert agent_ids == {f"agent-{i}" for i in range(10)}


def test_priority_scheduler_50_shared_pool_swaps_yield_one_lane_sorted() -> None:
    """PRD US-003 line 293 (validation): 50 swaps all on the same pool
    produce 1 lane with all 50 actions sorted by scheduler priority
    descending.

    All actions share ``write_locks={"pool"}`` so there is one connected
    component. Each action carries a distinct
    ``compute_unit_price_micro_lamports`` so each has a distinct priority
    score; within-lane sort must be by score descending.
    """
    # Reverse insertion order vs. score so any test that "passes" with
    # input-order ordering would fail — input price is ascending, expected
    # output price is descending.
    locked_actions = [
        LockedAction(
            action=_priced_action(f"agent-{i}", price_micro_lamports=(i + 1) * 1_000),
            write_locks=frozenset({"pool"}),
        )
        for i in range(50)
    ]

    lanes = PriorityScheduler().schedule(locked_actions, slot=0)

    assert len(lanes) == 1
    assert len(lanes[0].actions) == 50

    prices = [
        la.action.compute_unit_price_micro_lamports for la in lanes[0].actions
    ]
    assert prices == sorted(prices, reverse=True)
    assert prices[0] == 50_000
    assert prices[-1] == 1_000


def test_priority_scheduler_sorts_by_score_within_lane_only() -> None:
    """PRD US-003 line 304: two lanes, each with 3 actions; within-lane
    sort is by ``scheduler_priority_score`` descending; lanes themselves
    have no defined inter-order.

    Six actions partition into two connected components by write-lock:
    three share ``write_locks={"pool-A"}``, three share
    ``write_locks={"pool-B"}``. The two pools never conflict with each
    other so PriorityScheduler must emit exactly two lanes. Within each
    lane, distinct ``compute_unit_price_micro_lamports`` values give
    distinct priority scores; the sort must be by score descending. The
    inter-lane order is not asserted (lanes are an unordered set under
    the parallel-execution contract).

    Insertion order is constructed to interleave the two pools and to
    place lower-priced actions first within each pool, so any
    implementation that preserves input order — or that fails to sort
    within-lane — would fail.
    """
    pool_a_prices = [1_000, 5_000, 3_000]  # ascending-ish; not sorted
    pool_b_prices = [2_000, 8_000, 4_000]  # ascending-ish; not sorted

    locked_actions: list[LockedAction] = []
    for i, price in enumerate(pool_a_prices):
        locked_actions.append(
            LockedAction(
                action=_priced_action(f"a-{i}", price_micro_lamports=price),
                write_locks=frozenset({"pool-A"}),
            )
        )
    for i, price in enumerate(pool_b_prices):
        locked_actions.append(
            LockedAction(
                action=_priced_action(f"b-{i}", price_micro_lamports=price),
                write_locks=frozenset({"pool-B"}),
            )
        )

    lanes = PriorityScheduler().schedule(locked_actions, slot=0)

    assert len(lanes) == 2
    assert all(len(lane.actions) == 3 for lane in lanes)

    # Identify lanes by agent-id prefix; do not rely on inter-lane order.
    lanes_by_pool: dict[str, list[LockedAction]] = {}
    for lane in lanes:
        prefix = lane.actions[0].action.agent_id.split("-")[0]
        # All actions in a lane should share the same prefix (= same pool).
        assert all(
            la.action.agent_id.startswith(prefix + "-") for la in lane.actions
        )
        lanes_by_pool[prefix] = list(lane.actions)

    assert set(lanes_by_pool.keys()) == {"a", "b"}

    a_prices = [
        la.action.compute_unit_price_micro_lamports for la in lanes_by_pool["a"]
    ]
    b_prices = [
        la.action.compute_unit_price_micro_lamports for la in lanes_by_pool["b"]
    ]
    assert a_prices == sorted(pool_a_prices, reverse=True)
    assert b_prices == sorted(pool_b_prices, reverse=True)


def test_unresolved_action_rejected_before_scheduler() -> None:
    """PRD US-003 line 305: an executable action whose market has no
    lock resolver is rejected at admission with ``missing_lock_resolver``
    rather than silently degrading to serial-with-empty-locks.

    This test drives ``SolanaLikeExecution.execute_slot`` directly with a
    ``SlotContext.resolve_locks`` callback that returns ``None`` for the
    unresolved action and a real ``LockedAction`` for the resolved one.
    The strict rejection path (``execution.py:480-491``) appends the
    action to the outcome's ``dropped`` list with
    ``DropReason.MISSING_LOCK_RESOLVER`` and never invokes the executor
    for it — the scheduler only sees the resolved action.
    """
    unresolved = _action("unresolved")
    resolved = _action("resolved")

    def resolver(action: Action) -> LockedAction | None:
        if action is unresolved:
            return None
        return LockedAction(action=action, write_locks=frozenset({"pool"}))

    executed_actions: list[Action] = []

    def executor(action: Action, slot: int) -> ExecutedAction:
        executed_actions.append(action)
        return ExecutedAction(
            action=action,
            execution_cost=0,
            cost_token=None,
            succeeded=True,
            failure_reason=None,
        )

    ctx = SlotContext(
        slot=1,
        pending_actions=[unresolved, resolved],
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda evt: None,
        resolve_locks=resolver,
    )

    outcome = SolanaLikeExecution().execute_slot(ctx)

    assert (unresolved, DropReason.MISSING_LOCK_RESOLVER) in outcome.dropped
    assert all(action is not unresolved for action in executed_actions)
    assert executed_actions == [resolved]
    assert [ea.action for ea in outcome.executed] == [resolved]


def test_batch_execution_defaults_to_serial_scheduler() -> None:
    """PRD US-003 line 320: ``BatchExecution`` consumers default to
    ``SerialScheduler`` unless overridden — preserves chain-neutral test
    scenarios as a building-block primitive even though
    ``EthereumLikeExecution`` is being deleted.

    Plain ``BatchExecution()`` exposes a ``_scheduler`` attribute typed as
    ``SerialScheduler``. When an explicit scheduler is passed, it is used
    verbatim (no silent fallback).
    """
    from defi_sim.engine.execution import BatchExecution

    default_exec = BatchExecution()
    assert isinstance(default_exec._scheduler, SerialScheduler)

    custom = PriorityScheduler()
    overridden_exec = BatchExecution(scheduler=custom)
    assert overridden_exec._scheduler is custom
