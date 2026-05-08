"""Tests for bundle outcomes surfaced on ``RoundSnapshot``.

PRD US-011 line 891: per-slot list of selected bundles with tip + revenue
split, per-bundle outcome (landed / reverted / dropped), and ALT usage per
bundle.
"""

from __future__ import annotations

from defi_sim.core.types import BundleOutcome, RoundSnapshot, SwapAction
from defi_sim.engine.bundle import Bundle, TipPayment
from defi_sim.engine.bundle_auction import BundleAuction, BundleDropReason
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.slot import (
    BundleExecutionResult,
    ExecutedAction,
    SlotContext,
)
from defi_sim.engine.snapshots import (
    _deserialize_round_snapshot,
    _serialize_round_snapshot,
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
    lookup_tables: list[str] | None = None,
) -> Bundle:
    txs = [
        VersionedTransaction(
            actions=[_swap(cu_limit=cu_per_tx)],
            lookup_tables=list(lookup_tables or []),
        )
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


def _collect_via_engine(execution: SolanaLikeExecution) -> list[BundleOutcome]:
    """Build a minimal SimulationEngine and call _collect_bundle_outcomes."""
    from defi_sim.engine.api import build_engine

    spec: dict = {
        "market": {
            "type": "cfamm",
            "tokens": [
                {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
                {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
            ],
            "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
        },
        "agents": [],
        "num_rounds": 1,
        "snapshot_interval": 1,
        "seed": 1,
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit"},
            "params": {"cost_token": "USDC"},
        },
    }
    engine = build_engine(spec)
    engine._execution_model = execution
    return engine._collect_bundle_outcomes(current_slot=42, round_num=42)


def test_snapshot_round_trip_preserves_bundle_outcomes() -> None:
    """msgpack snapshot serialization round-trips BundleOutcomes."""
    original = RoundSnapshot(
        round=1,
        timestamp=100,
        epoch=0,
        bundle_outcomes=[
            BundleOutcome(
                slot=42,
                bundle_index=0,
                status="landed",
                tip_lamports=10_000,
                validator_revenue_lamports=9_500,
                stake_pool_revenue_lamports=500,
                alt_ids=("alt-A", "alt-B"),
                num_txs=3,
                total_cu=600_000,
            ),
            BundleOutcome(
                slot=42,
                bundle_index=1,
                status="dropped",
                tip_lamports=2_000,
                validator_revenue_lamports=0,
                stake_pool_revenue_lamports=0,
                alt_ids=(),
                num_txs=1,
                total_cu=0,
                drop_reason=BundleDropReason.BUNDLE_LOCK_CONFLICT,
            ),
        ],
    )
    serialized = _serialize_round_snapshot(original)
    restored = _deserialize_round_snapshot(serialized)
    assert restored.bundle_outcomes == original.bundle_outcomes


def test_snapshot_default_bundle_outcomes_is_empty() -> None:
    """A snapshot without a bundle pre-stage has an empty bundle_outcomes list."""
    snap = RoundSnapshot(round=1)
    assert snap.bundle_outcomes == []
    restored = _deserialize_round_snapshot(_serialize_round_snapshot(snap))
    assert restored.bundle_outcomes == []


def test_collect_returns_empty_when_no_auction_configured() -> None:
    """A SolanaLikeExecution without bundle_auction yields zero outcomes."""
    execution = SolanaLikeExecution()
    outcomes = _collect_via_engine(execution)
    assert outcomes == []


def test_collect_landed_bundle_revenue_split_default_share() -> None:
    """Default jito_stake_pool_share=0.05: tip=10_000 -> validator 9_500, pool 500."""
    auction = BundleAuction()
    execution = SolanaLikeExecution(bundle_auction=auction)
    bundle = _bundle(tip_lamports=10_000, lookup_tables=["alt-A"])
    execution.submit_bundle(bundle)

    def exec_bundle(b: Bundle, slot: int) -> BundleExecutionResult:
        return BundleExecutionResult(
            reverted=False,
            failed_at_index=None,
            failed_reason=None,
            executed=[
                ExecutedAction(action=tx.actions[0], execution_cost=0, cost_token=None, succeeded=True)
                for tx in b.txs
            ],
        )

    ctx = SlotContext(
        slot=42,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=exec_bundle,
    )
    execution.execute_slot(ctx)

    outcomes = _collect_via_engine(execution)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.status == "landed"
    assert o.tip_lamports == 10_000
    assert o.validator_revenue_lamports == 9_500
    assert o.stake_pool_revenue_lamports == 500
    assert o.alt_ids == ("alt-A",)
    assert o.num_txs == 1
    assert o.failed_at_index is None
    assert o.drop_reason is None


def test_collect_reverted_bundle_pays_no_revenue() -> None:
    """Reverted bundles surface zero revenue and the failing position."""
    auction = BundleAuction()
    execution = SolanaLikeExecution(bundle_auction=auction)
    execution.submit_bundle(_bundle(tip_lamports=10_000))

    def exec_bundle(b: Bundle, slot: int) -> BundleExecutionResult:
        return BundleExecutionResult(
            reverted=True,
            failed_at_index=0,
            failed_reason="oops",
            executed=[],
        )

    ctx = SlotContext(
        slot=7,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=exec_bundle,
    )
    execution.execute_slot(ctx)

    outcomes = _collect_via_engine(execution)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.status == "reverted"
    assert o.failed_at_index == 0
    assert o.validator_revenue_lamports == 0
    assert o.stake_pool_revenue_lamports == 0
    assert o.tip_lamports == 10_000


def test_collect_dropped_bundle_has_drop_reason() -> None:
    """Bundles dropped at admission appear with status=dropped + reason."""
    auction = BundleAuction(min_bundle_tip_lamports=10_000)
    execution = SolanaLikeExecution(bundle_auction=auction)
    bundle = _bundle(tip_lamports=5_000)
    execution.submit_bundle(bundle)

    ctx = SlotContext(
        slot=3,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=lambda b, s: BundleExecutionResult(
            reverted=False, failed_at_index=None, failed_reason=None, executed=[]
        ),
    )
    execution.execute_slot(ctx)

    outcomes = _collect_via_engine(execution)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.status == "dropped"
    assert o.drop_reason == BundleDropReason.BUNDLE_TIP_BELOW_MINIMUM
    assert o.tip_lamports == 5_000
    assert o.validator_revenue_lamports == 0


def test_collect_alt_usage_unions_across_inner_txs() -> None:
    """ALT ids on the outcome are the sorted union of all txs' lookup_tables."""
    auction = BundleAuction()
    execution = SolanaLikeExecution(bundle_auction=auction)

    txs = [
        VersionedTransaction(actions=[_swap()], lookup_tables=["alt-B", "alt-A"]),
        VersionedTransaction(actions=[_swap()], lookup_tables=["alt-C", "alt-A"]),
    ]
    bundle = Bundle(
        txs=txs,
        tip_payments=[
            TipPayment(tx_index=0, location="standalone_tx", lamports=5_000, recipient="tip-1")
        ],
    )
    execution.submit_bundle(bundle)

    def exec_bundle(b: Bundle, slot: int) -> BundleExecutionResult:
        return BundleExecutionResult(
            reverted=False,
            failed_at_index=None,
            failed_reason=None,
            executed=[
                ExecutedAction(action=tx.actions[0], execution_cost=0, cost_token=None, succeeded=True)
                for tx in b.txs
            ],
        )

    ctx = SlotContext(
        slot=0,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=exec_bundle,
    )
    execution.execute_slot(ctx)

    outcomes = _collect_via_engine(execution)
    assert len(outcomes) == 1
    assert outcomes[0].alt_ids == ("alt-A", "alt-B", "alt-C")


def test_collect_orders_selected_before_dropped() -> None:
    """Selected bundles are emitted first; dropped bundles follow."""
    auction = BundleAuction(min_bundle_tip_lamports=10_000)
    execution = SolanaLikeExecution(bundle_auction=auction)
    landed_bundle = _bundle(tip_lamports=20_000)
    dropped_bundle = _bundle(tip_lamports=5_000)
    execution.submit_bundle(landed_bundle)
    execution.submit_bundle(dropped_bundle)

    def exec_bundle(b: Bundle, slot: int) -> BundleExecutionResult:
        return BundleExecutionResult(
            reverted=False,
            failed_at_index=None,
            failed_reason=None,
            executed=[
                ExecutedAction(action=tx.actions[0], execution_cost=0, cost_token=None, succeeded=True)
                for tx in b.txs
            ],
        )

    ctx = SlotContext(
        slot=99,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=exec_bundle,
    )
    execution.execute_slot(ctx)

    outcomes = _collect_via_engine(execution)
    assert [o.status for o in outcomes] == ["landed", "dropped"]
    assert outcomes[0].bundle_index == 0
    assert outcomes[1].bundle_index == 1
    assert outcomes[1].drop_reason == BundleDropReason.BUNDLE_TIP_BELOW_MINIMUM
