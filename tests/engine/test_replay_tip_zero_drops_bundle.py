"""Validation: TipReplaceCounterfactual(bundle, new_tip=0) drops bundle from auction.

PRD US-002 validation bullet (line 339): a replay with
``TipReplaceCounterfactual(bundle, new_tip=0)`` removes that bundle from the
auction at the target slot (per ``BundleAuction`` admit logic from US-011), so
the predicted post-slot state diverges from the actual mainnet state where the
bundle did land.

Until a real mainnet bundle in the corpus is decoded by a Phase 3 hydrator
(PRD line 270), this validation exercises a synthetic bundle that traces the
same code path: bundle constructed with a winning tip, admitted into the
auction; counterfactual zeros its ``tip_payments``; the same auction now drops
it with ``BUNDLE_TIP_BELOW_MINIMUM``. The two outcomes (selected vs. dropped)
constitute the predicted-vs-actual divergence the bullet asserts.
"""

from __future__ import annotations

from defi_sim.core.types import SwapAction
from defi_sim.engine.bundle import Bundle, TipPayment
from defi_sim.engine.bundle_auction import (
    BundleAuction,
    BundleCandidate,
    BundleDropReason,
)
from defi_sim.engine.replay_execution import TipReplaceCounterfactual
from defi_sim.engine.transactions import VersionedTransaction


def _winning_bundle(searcher_id: str, *, tip_lamports: int = 5_000) -> Bundle:
    tx = VersionedTransaction(
        actions=[
            SwapAction(
                agent_id=searcher_id,
                token_in="SOL",
                token_out="USDC",
                amount_in=1,
                compute_unit_limit=10_000,
            )
        ]
    )
    return Bundle(
        txs=[tx],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=tip_lamports,
                recipient="tip-1",
            )
        ],
        searcher_id=searcher_id,
    )


def test_actual_mainnet_baseline_lands_the_bundle() -> None:
    bundle = _winning_bundle("searcher-1")
    auction = BundleAuction()

    admitted, dropped = auction.admit([bundle])
    assert admitted == [bundle]
    assert dropped == []

    result = auction.select_top_k(
        [BundleCandidate(bundle=bundle, submitted_index=0)],
        remaining_slot_cu=10_000_000,
    )
    assert [c.bundle for c in result.selected] == [bundle]
    assert result.dropped == []


def test_tip_replace_counterfactual_zeros_only_matching_bundle() -> None:
    target = _winning_bundle("searcher-1", tip_lamports=5_000)
    untouched = _winning_bundle("searcher-2", tip_lamports=7_500)

    cf = TipReplaceCounterfactual(target_bundle_id="searcher-1", new_tip_lamports=0)
    out = cf.apply_to_bundles([target, untouched])

    assert out == [target, untouched]
    assert target.tip_lamports == 0
    assert untouched.tip_lamports == 7_500


def test_replay_with_tip_zero_counterfactual_drops_bundle_from_auction() -> None:
    actual_bundle = _winning_bundle("searcher-1", tip_lamports=5_000)
    predicted_bundle = _winning_bundle("searcher-1", tip_lamports=5_000)
    cf = TipReplaceCounterfactual(target_bundle_id="searcher-1", new_tip_lamports=0)
    cf.apply_to_bundles([predicted_bundle])

    auction = BundleAuction()
    actual_admitted, actual_dropped = auction.admit([actual_bundle])
    pred_admitted, pred_dropped = auction.admit([predicted_bundle])

    assert actual_admitted == [actual_bundle]
    assert actual_dropped == []

    assert pred_admitted == []
    assert pred_dropped == [
        (predicted_bundle, BundleDropReason.BUNDLE_TIP_BELOW_MINIMUM)
    ]

    actual_landed = {b.searcher_id for b in actual_admitted}
    predicted_landed = {b.searcher_id for b in pred_admitted}
    assert actual_landed - predicted_landed == {"searcher-1"}
