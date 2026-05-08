"""Tests for the ``BundleAuction`` execution mode (PRD US-011 line 832)."""

from __future__ import annotations

import copy

import pytest

from defi_sim.core.types import SwapAction
from defi_sim.engine.api import build_engine
from defi_sim.engine.bundle import (
    MAX_BUNDLE_TXS,
    MIN_BUNDLE_TIP_LAMPORTS,
    Bundle,
    TipPayment,
)
from defi_sim.engine.bundle_auction import (
    BundleAuction,
    BundleCandidate,
    BundleDropReason,
)
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.slot import (
    BundleExecutionResult,
    ExecutedAction,
    SlotContext,
)
from defi_sim.engine.transactions import VersionedTransaction


def _vtx(*, cu_limit: int = 0) -> VersionedTransaction:
    action = SwapAction(
        agent_id="searcher",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        compute_unit_limit=cu_limit if cu_limit > 0 else None,
    )
    return VersionedTransaction(actions=[action])


def _bundle(
    *,
    tip_lamports: int = MIN_BUNDLE_TIP_LAMPORTS,
    n_txs: int = 1,
    cu_per_tx: int = 0,
) -> Bundle:
    txs = [_vtx(cu_limit=cu_per_tx) for _ in range(n_txs)]
    payment = TipPayment(
        tx_index=0,
        location="standalone_tx",
        lamports=tip_lamports,
        recipient="tip-1",
    )
    return Bundle(txs=txs, tip_payments=[payment])


def _candidate(
    bundle: Bundle,
    *,
    write_locks: frozenset[str] = frozenset(),
    read_locks: frozenset[str] = frozenset(),
    submitted_index: int = 0,
) -> BundleCandidate:
    return BundleCandidate(
        bundle=bundle,
        write_locks=write_locks,
        read_locks=read_locks,
        submitted_index=submitted_index,
    )


# --- admission --------------------------------------------------------------


def test_admit_accepts_within_limits() -> None:
    auction = BundleAuction()
    bundle = _bundle()
    admitted, dropped = auction.admit([bundle])
    assert admitted == [bundle]
    assert dropped == []


def test_admit_drops_oversized_bundle_when_max_lowered() -> None:
    # Construct a 3-tx bundle, then lower the auction's cap to 2.
    bundle = _bundle(n_txs=3)
    auction = BundleAuction(max_bundle_txs=2)
    admitted, dropped = auction.admit([bundle])
    assert admitted == []
    assert dropped == [(bundle, BundleDropReason.BUNDLE_TOO_LARGE)]


def test_admit_drops_below_min_tip_when_threshold_raised() -> None:
    bundle = _bundle(tip_lamports=MIN_BUNDLE_TIP_LAMPORTS)
    auction = BundleAuction(min_bundle_tip_lamports=MIN_BUNDLE_TIP_LAMPORTS + 1)
    admitted, dropped = auction.admit([bundle])
    assert admitted == []
    assert dropped == [(bundle, BundleDropReason.BUNDLE_TIP_BELOW_MINIMUM)]


# --- selection: ranking -----------------------------------------------------


def test_top_k_selection_by_tip_when_cu_identical() -> None:
    """PRD line 906: 5 bundles, identical CU, max_bundles_per_slot=3 picks top
    3 by tip — the highest three are tips [200, 150, 100]."""
    tips = [100, 50, 200, 150, 75]
    candidates = [
        _candidate(
            _bundle(tip_lamports=tip * 1_000, cu_per_tx=10_000),
            write_locks=frozenset({f"acct-{i}"}),
            submitted_index=i,
        )
        for i, tip in enumerate(tips)
    ]
    auction = BundleAuction(max_bundles_per_slot=3)
    result = auction.select_top_k(
        candidates, remaining_slot_cu=10_000_000
    )
    selected_tips = [c.tip_lamports for c in result.selected]
    assert selected_tips == [200_000, 150_000, 100_000]


def test_local_auction_ranks_by_tip_per_cu_before_total_tip() -> None:
    """PRD line 907: lower total tip but higher tip/CU ranks first.

    Both bundles fit and don't conflict, so both get selected — but bundle
    B (lower tip, better tip/CU) must rank first in the selected list.
    """
    # Bundle A: tip=10_000, cu=200_000 -> tip/cu = 0.05
    # Bundle B: tip=5_000,  cu=50_000  -> tip/cu = 0.10  (wins on rank)
    a = _candidate(
        _bundle(tip_lamports=10_000, cu_per_tx=200_000),
        write_locks=frozenset({"acct-a"}),
        submitted_index=0,
    )
    b = _candidate(
        _bundle(tip_lamports=5_000, cu_per_tx=50_000),
        write_locks=frozenset({"acct-b"}),
        submitted_index=1,
    )
    auction = BundleAuction(max_bundles_per_slot=2)
    result = auction.select_top_k([a, b], remaining_slot_cu=10_000_000)
    assert result.selected[0] is b
    assert result.selected[1] is a


def test_tie_breaker_uses_total_tip_then_submission_order() -> None:
    # Identical tip/CU: rank by total tip, then submitted_index.
    a = _candidate(
        _bundle(tip_lamports=10_000, cu_per_tx=100_000),
        write_locks=frozenset({"acct-a"}),
        submitted_index=2,
    )
    b = _candidate(
        _bundle(tip_lamports=10_000, cu_per_tx=100_000),
        write_locks=frozenset({"acct-b"}),
        submitted_index=0,
    )
    auction = BundleAuction(max_bundles_per_slot=2)
    result = auction.select_top_k([a, b], remaining_slot_cu=10_000_000)
    # Same total tip, same tip/CU -> b wins on submitted_index=0.
    assert result.selected[0] is b
    assert result.selected[1] is a


# --- selection: lock conflicts ---------------------------------------------


def test_lock_conflict_drops_lower_efficiency_bundle() -> None:
    """PRD line 908: two bundles share write-lock; only higher tip/CU survives."""
    # B has higher tip/CU than A, but they collide on pool_A.
    a = _candidate(
        _bundle(tip_lamports=10_000, cu_per_tx=100_000),
        write_locks=frozenset({"pool_A"}),
        submitted_index=0,
    )
    b = _candidate(
        _bundle(tip_lamports=20_000, cu_per_tx=50_000),
        write_locks=frozenset({"pool_A"}),
        submitted_index=1,
    )
    auction = BundleAuction(max_bundles_per_slot=2)
    result = auction.select_top_k([a, b], remaining_slot_cu=10_000_000)
    assert result.selected == [b]
    assert (a.bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT) in result.dropped


def test_lock_conflict_with_tied_efficiency_drops_lower_total_tip() -> None:
    """PRD line 900 parenthetical: when two write-lock-conflicting bundles
    have identical tip/CU efficiency, the one with the lower total tip is
    dropped with ``bundle_lock_conflict``."""
    # Same tip/CU = 0.10; total tips differ -> higher total tip wins.
    low = _candidate(
        _bundle(tip_lamports=10_000, cu_per_tx=100_000),
        write_locks=frozenset({"pool_A"}),
        submitted_index=0,
    )
    high = _candidate(
        _bundle(tip_lamports=20_000, cu_per_tx=200_000),
        write_locks=frozenset({"pool_A"}),
        submitted_index=1,
    )
    assert low.tip_per_cu == high.tip_per_cu
    auction = BundleAuction(max_bundles_per_slot=2)
    result = auction.select_top_k([low, high], remaining_slot_cu=10_000_000)
    assert result.selected == [high]
    assert result.dropped == [(low.bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT)]


def test_bundle_lock_conflict_with_regular_action_drops_bundle() -> None:
    """PRD line 909: regular action queue holds a write that the bundle needs."""
    candidate = _candidate(
        _bundle(tip_lamports=50_000, cu_per_tx=100_000),
        write_locks=frozenset({"pool_X"}),
    )
    auction = BundleAuction()
    result = auction.select_top_k(
        [candidate],
        remaining_slot_cu=10_000_000,
        non_bundle_pending_writes=frozenset({"pool_X"}),
    )
    assert result.selected == []
    assert result.dropped == [
        (candidate.bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT)
    ]


def test_coexisting_locks_exempt_bundle_from_regular_conflict() -> None:
    """PRD US-013: a back-run bundle declares its victim as a coexisting
    action. The auction subtracts the victim's locks from the non-bundle
    conflict set for THIS candidate so the bundle is not dropped for racing
    its own target."""
    candidate = BundleCandidate(
        bundle=_bundle(tip_lamports=50_000, cu_per_tx=100_000),
        write_locks=frozenset({"pool_X"}),
        coexisting_write_locks=frozenset({"pool_X"}),
    )
    auction = BundleAuction()
    result = auction.select_top_k(
        [candidate],
        remaining_slot_cu=10_000_000,
        non_bundle_pending_writes=frozenset({"pool_X"}),
    )
    assert result.selected == [candidate]
    assert result.dropped == []


def test_coexisting_locks_only_exempt_declared_bundle() -> None:
    """The exemption is per-candidate. A second bundle that does NOT declare
    coexistence still hits the conflict on the same regular-queue write."""
    coexisting = BundleCandidate(
        bundle=_bundle(tip_lamports=50_000, cu_per_tx=100_000),
        write_locks=frozenset({"pool_X"}),
        coexisting_write_locks=frozenset({"pool_X"}),
        submitted_index=0,
    )
    plain = BundleCandidate(
        bundle=_bundle(tip_lamports=40_000, cu_per_tx=100_000),
        write_locks=frozenset({"pool_X"}),
        submitted_index=1,
    )
    auction = BundleAuction(max_bundles_per_slot=2)
    result = auction.select_top_k(
        [coexisting, plain],
        remaining_slot_cu=10_000_000,
        non_bundle_pending_writes=frozenset({"pool_X"}),
    )
    # coexisting bundle wins; plain is dropped both due to bundle-vs-bundle
    # conflict (write_locks overlap with reserved_writes) AND would have
    # been dropped on the regular-queue conflict anyway.
    assert result.selected == [coexisting]
    assert (plain.bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT) in result.dropped


def test_read_lock_conflict_with_regular_write_drops_bundle() -> None:
    """A bundle's read-lock collides with a non-bundle pending write."""
    candidate = _candidate(
        _bundle(tip_lamports=50_000, cu_per_tx=100_000),
        read_locks=frozenset({"oracle-1"}),
    )
    auction = BundleAuction()
    result = auction.select_top_k(
        [candidate],
        remaining_slot_cu=10_000_000,
        non_bundle_pending_writes=frozenset({"oracle-1"}),
    )
    assert result.selected == []
    assert result.dropped[0][1] == BundleDropReason.BUNDLE_LOCK_CONFLICT


def test_read_only_overlap_between_bundles_does_not_conflict() -> None:
    """Two bundles that only share a *read* lock execute concurrently."""
    a = _candidate(
        _bundle(tip_lamports=20_000, cu_per_tx=100_000),
        write_locks=frozenset({"pool_a"}),
        read_locks=frozenset({"oracle-shared"}),
        submitted_index=0,
    )
    b = _candidate(
        _bundle(tip_lamports=10_000, cu_per_tx=100_000),
        write_locks=frozenset({"pool_b"}),
        read_locks=frozenset({"oracle-shared"}),
        submitted_index=1,
    )
    auction = BundleAuction(max_bundles_per_slot=2)
    result = auction.select_top_k([a, b], remaining_slot_cu=10_000_000)
    assert {id(c.bundle) for c in result.selected} == {id(a.bundle), id(b.bundle)}
    assert result.dropped == []


# --- selection: CU budget ---------------------------------------------------


def test_slot_cu_budget_limits_selected_bundle_set() -> None:
    """PRD line 910: high-efficiency bundle skipped if it can't fit slot CU."""
    # Big bundle has higher tip/CU but won't fit; smaller still selected.
    big = _candidate(
        _bundle(tip_lamports=1_000_000, cu_per_tx=2_000_000),
        write_locks=frozenset({"pool_a"}),
        submitted_index=0,
    )
    small = _candidate(
        _bundle(tip_lamports=10_000, cu_per_tx=100_000),
        write_locks=frozenset({"pool_b"}),
        submitted_index=1,
    )
    auction = BundleAuction(max_bundles_per_slot=5)
    result = auction.select_top_k(
        [big, small], remaining_slot_cu=500_000
    )
    assert result.selected == [small]
    assert (big.bundle, BundleDropReason.BUNDLE_SLOT_CU_EXCEEDED) in result.dropped


def test_max_bundles_per_slot_caps_selection() -> None:
    candidates = [
        _candidate(
            _bundle(tip_lamports=(10 - i) * 1_000, cu_per_tx=10_000),
            write_locks=frozenset({f"acct-{i}"}),
            submitted_index=i,
        )
        for i in range(5)
    ]
    auction = BundleAuction(max_bundles_per_slot=2)
    result = auction.select_top_k(candidates, remaining_slot_cu=10_000_000)
    assert len(result.selected) == 2


# --- construction validation -----------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_bundle_txs": 0},
        {"min_bundle_tip_lamports": -1},
        {"max_bundles_per_slot": 0},
        {"jito_stake_pool_share": 1.5},
        {"jito_stake_pool_share": -0.1},
    ],
)
def test_invalid_construction_raises(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        BundleAuction(**kwargs)


def test_default_jito_stake_pool_share_is_five_percent() -> None:
    """PRD line 890: default ``jito_stake_pool_share`` is 0.05 (5%)."""
    auction = BundleAuction()
    assert auction.jito_stake_pool_share == 0.05


def test_default_max_bundle_txs_matches_jito() -> None:
    auction = BundleAuction()
    assert auction.max_bundle_txs == MAX_BUNDLE_TXS
    assert auction.min_bundle_tip_lamports == MIN_BUNDLE_TIP_LAMPORTS


# --- atomic execution: success path -----------------------------------------


_SOLANA_SPEC: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "params": {
            "initial_liquidity": 1_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "searcher",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
    ],
    "num_rounds": 1,
    "seed": 11,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
    },
}


def test_bundle_executes_atomically_on_success() -> None:
    """PRD line 911: all txs succeed; final state reflects all changes; tip paid."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    searcher = next(a for a in engine._agents if a.agent_id == "searcher")
    pre_sol = searcher.state.balances.get("SOL", 0)
    pre_volume = searcher.state.cumulative_volume

    swap_a = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=1_000)
    swap_b = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=2_000)
    bundle = Bundle(
        txs=[VersionedTransaction(actions=[swap_a]), VersionedTransaction(actions=[swap_b])],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=MIN_BUNDLE_TIP_LAMPORTS,
                recipient="tip-acct-1",
            )
        ],
    )

    actions = [a for tx in bundle.txs for a in tx.actions]
    outcome = engine._execute_bundle_atomically(actions, round_num=0, ts=0)

    assert outcome["reverted"] is False
    assert outcome["failed_at_index"] is None
    assert len(outcome["executed"]) == 2

    # Final state reflects all changes from both inner txs.
    assert searcher.state.balances.get("SOL", 0) > pre_sol
    assert engine._market._reserves != pre_market_reserves  # type: ignore[attr-defined]
    assert searcher.state.cumulative_volume > pre_volume

    # Tip is paid: bundle did not revert, so all tip payments credit the recipient.
    paid = bundle.paid_tip_payments(
        reverted=outcome["reverted"], failed_at_index=outcome["failed_at_index"]
    )
    assert len(paid) == 1
    assert paid[0].recipient == "tip-acct-1"
    assert paid[0].lamports == MIN_BUNDLE_TIP_LAMPORTS
    assert sum(tp.lamports for tp in paid) == bundle.tip_lamports


def test_bundle_reverts_on_any_tx_failure() -> None:
    """PRD line 912: last tx reverts; final state matches pre-bundle; tip not paid."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    searcher = next(a for a in engine._agents if a.agent_id == "searcher")
    pre_sol = searcher.state.balances.get("SOL", 0)
    pre_volume = searcher.state.cumulative_volume

    swap_a = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=1_000)
    # Last tx fails: token_in does not exist on this market.
    swap_b = SwapAction(
        agent_id="searcher", token_in="DOES_NOT_EXIST", token_out="SOL", amount_in=1
    )
    bundle = Bundle(
        txs=[VersionedTransaction(actions=[swap_a]), VersionedTransaction(actions=[swap_b])],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=MIN_BUNDLE_TIP_LAMPORTS,
                recipient="tip-acct-1",
            )
        ],
    )

    actions = [a for tx in bundle.txs for a in tx.actions]
    outcome = engine._execute_bundle_atomically(actions, round_num=0, ts=0)

    assert outcome["reverted"] is True
    assert outcome["failed_at_index"] == 1
    assert outcome["executed"] == []

    # Final state matches pre-bundle exactly — first tx's effects undone.
    assert searcher.state.balances.get("SOL", 0) == pre_sol
    assert engine._market._reserves == pre_market_reserves  # type: ignore[attr-defined]
    assert searcher.state.cumulative_volume == pre_volume

    # Tip is not paid: bundle reverted.
    paid = bundle.paid_tip_payments(
        reverted=outcome["reverted"], failed_at_index=outcome["failed_at_index"]
    )
    assert paid == []


def test_revenue_split() -> None:
    """PRD line 913: tip=1000, share=0.1 -> validator 900, stake-pool 100."""
    auction = BundleAuction(jito_stake_pool_share=0.1)
    execution = SolanaLikeExecution(bundle_auction=auction)
    bundle = _bundle(tip_lamports=1_000)
    execution.submit_bundle(bundle)

    def exec_bundle(b: Bundle, slot: int) -> BundleExecutionResult:
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
                for tx in b.txs
            ],
        )

    def _executor(action, slot_index):
        return ExecutedAction(
            action=action, execution_cost=0, cost_token=None, succeeded=True
        )

    ctx = SlotContext(
        slot=1,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=exec_bundle,
    )
    execution.execute_slot(ctx)

    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))
    engine._execution_model = execution
    outcomes = engine._collect_bundle_outcomes(current_slot=1, round_num=1)

    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.status == "landed"
    assert o.tip_lamports == 1_000
    assert o.validator_revenue_lamports == 900
    assert o.stake_pool_revenue_lamports == 100


def test_tip_quote_uses_local_lock_cohort_percentiles() -> None:
    """Tip optimizer inputs are local-auction scoped, not global.

    A hot Whirlpool lock gets its own observed tip distribution while an
    unseen pool falls back to the Jito floor.
    """
    auction = BundleAuction()
    hot_pool = {"Whirlpool/SOL/USDC"}
    quiet_pool = {"Whirlpool/BONK/USDC"}
    for tip in range(10_000, 1_020_000, 10_000):
        auction.observe_tip(hot_pool, tip)

    assert auction.tip_quote(hot_pool, 75) == 760_000
    assert auction.tip_quote(hot_pool, 90) == 910_000
    assert auction.tip_quote(hot_pool, 99) == 1_000_000
    assert auction.tip_quote(quiet_pool, 90) == auction.min_bundle_tip_lamports
