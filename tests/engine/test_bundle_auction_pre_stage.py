"""Integration tests for the bundle pre-stage in ``SolanaLikeExecution.execute_slot``.

PRD US-011 line 840: bundles execute before the regular scheduler-driven
phase; the auction selects under the slot-CU budget and seeds the conflict
set the regular queue sees.
"""

from __future__ import annotations

from defi_sim.core.types import SwapAction
from defi_sim.engine.bundle import Bundle, TipPayment
from defi_sim.engine.bundle_auction import BundleAuction, BundleDropReason
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.scheduler import LockedAction
from defi_sim.engine.slot import (
    BundleExecutionResult,
    ExecutedAction,
    SlotContext,
)
from defi_sim.engine.transactions import VersionedTransaction


def _executor(action, slot_index):
    return ExecutedAction(
        action=action,
        execution_cost=0,
        cost_token=None,
        succeeded=True,
    )


def _swap(*, agent_id: str = "searcher", cu_limit: int = 0) -> SwapAction:
    return SwapAction(
        agent_id=agent_id,
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        compute_unit_limit=cu_limit if cu_limit > 0 else None,
    )


def _bundle(
    *,
    tip_lamports: int = 5_000,
    n_txs: int = 1,
    cu_per_tx: int = 0,
    agent_id: str = "searcher",
) -> Bundle:
    txs = [
        VersionedTransaction(actions=[_swap(agent_id=agent_id, cu_limit=cu_per_tx)])
        for _ in range(n_txs)
    ]
    return Bundle(
        txs=txs,
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=tip_lamports,
                recipient="tip-1",
            )
        ],
    )


def _bundle_executor_recorder():
    """Returns (executor_fn, list-of-executed-bundles)."""
    seen: list[Bundle] = []

    def exec_bundle(bundle: Bundle, slot: int) -> BundleExecutionResult:
        seen.append(bundle)
        return BundleExecutionResult(
            reverted=False,
            failed_at_index=None,
            failed_reason=None,
            executed=[
                ExecutedAction(
                    action=tx.actions[0],
                    execution_cost=0,
                    cost_token=None,
                    succeeded=True,
                )
                for tx in bundle.txs
            ],
        )

    return exec_bundle, seen


def test_pre_stage_skipped_when_no_bundle_auction() -> None:
    """No bundle_auction -> no pre-stage; submit_bundle raises."""
    model = SolanaLikeExecution()
    assert model.bundle_auction is None
    try:
        model.submit_bundle(_bundle())
    except RuntimeError:
        return
    raise AssertionError("submit_bundle without bundle_auction should raise")


def test_pre_stage_runs_admitted_bundles_through_executor() -> None:
    auction = BundleAuction(max_bundles_per_slot=5)
    model = SolanaLikeExecution(bundle_auction=auction)
    model.submit_bundle(_bundle(tip_lamports=2_000))
    model.submit_bundle(_bundle(tip_lamports=3_000))

    exec_bundle, seen = _bundle_executor_recorder()

    ctx = SlotContext(
        slot=0,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=exec_bundle,
    )
    outcome = model.execute_slot(ctx)

    assert len(seen) == 2
    # Two single-tx bundles -> two ExecutedAction entries.
    assert len(outcome.executed) == 2


def test_pre_stage_drains_pending_bundles_each_slot() -> None:
    auction = BundleAuction()
    model = SolanaLikeExecution(bundle_auction=auction)
    model.submit_bundle(_bundle())

    exec_bundle, seen = _bundle_executor_recorder()
    ctx_kwargs = dict(
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=exec_bundle,
    )
    model.execute_slot(SlotContext(slot=0, pending_actions=[], **ctx_kwargs))
    assert len(seen) == 1
    # Next slot has no pending bundles — pre-stage drains the queue.
    model.execute_slot(SlotContext(slot=1, pending_actions=[], **ctx_kwargs))
    assert len(seen) == 1


def test_pre_stage_surfaces_admit_drops_via_telemetry() -> None:
    """A bundle below the auction's tip threshold is dropped at admission."""
    auction = BundleAuction(min_bundle_tip_lamports=10_000)
    model = SolanaLikeExecution(bundle_auction=auction)
    bundle = _bundle(tip_lamports=5_000)
    model.submit_bundle(bundle)

    exec_bundle, seen = _bundle_executor_recorder()
    ctx = SlotContext(
        slot=0,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=exec_bundle,
    )
    model.execute_slot(ctx)

    assert seen == []
    assert model._last_slot_dropped_bundles == [
        (bundle, BundleDropReason.BUNDLE_TIP_BELOW_MINIMUM)
    ]
    assert model._last_slot_selected_bundles == []


def test_pre_stage_drops_bundle_conflicting_with_regular_queue() -> None:
    """A bundle whose write-lock collides with the regular queue is dropped."""
    auction = BundleAuction()
    model = SolanaLikeExecution(bundle_auction=auction)
    pool = "Whirlpool/SOL/USDC"

    swap = _swap(cu_limit=200_000)
    bundle = _bundle(tip_lamports=5_000)
    model.submit_bundle(bundle)

    def resolver(action):
        return LockedAction(action=action, write_locks=frozenset({pool}))

    exec_bundle, seen = _bundle_executor_recorder()
    ctx = SlotContext(
        slot=0,
        pending_actions=[swap],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        resolve_locks=resolver,
        execute_bundle=exec_bundle,
    )
    outcome = model.execute_slot(ctx)

    # Bundle dropped; regular swap still executes.
    assert seen == []
    assert (bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT) in model._last_slot_dropped_bundles
    assert len(outcome.executed) == 1


def test_pre_stage_runs_before_regular_phase() -> None:
    """Selected bundles' ExecutedActions appear before regular-phase ones."""
    auction = BundleAuction()
    model = SolanaLikeExecution(bundle_auction=auction)

    bundle = _bundle(tip_lamports=5_000)
    bundle_inner = bundle.txs[0].actions[0]
    model.submit_bundle(bundle)

    swap = _swap(agent_id="trader")

    exec_bundle, seen = _bundle_executor_recorder()
    ctx = SlotContext(
        slot=0,
        pending_actions=[swap],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=exec_bundle,
    )
    outcome = model.execute_slot(ctx)

    # Bundle's executed action lands first; regular swap second.
    assert len(outcome.executed) == 2
    assert outcome.executed[0].action is bundle_inner
    assert outcome.executed[1].action is swap


def test_pre_stage_no_op_without_execute_bundle_callback() -> None:
    """Without ctx.execute_bundle, pre-stage drains pending but executes nothing."""
    auction = BundleAuction()
    model = SolanaLikeExecution(bundle_auction=auction)
    model.submit_bundle(_bundle())

    ctx = SlotContext(
        slot=0,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
    )
    outcome = model.execute_slot(ctx)

    assert outcome.executed == []
    # Pending queue was drained even when no executor wired (so a stale
    # bundle doesn't accumulate forever).
    assert model._pending_bundles == []
