"""Bundle landing rate aligns with submission-path priors (PRD US-013 line 1057).

The bundle pre-stage applies a Bernoulli draw per bundle using
``SubmissionPathPriors.jito_relayer_landing_prob_baseline`` so every
admitted bundle's observed landing rate matches the configured prior
(modulo auction selection effects, which this test isolates by
submitting one no-conflict bundle per slot).
"""

from __future__ import annotations

import math

import numpy as np

from defi_sim.engine.bundle import Bundle, TipPayment
from defi_sim.engine.bundle_auction import BundleAuction
from defi_sim.engine.execution import DropReason, SolanaLikeExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.slot import (
    BundleExecutionResult,
    ExecutedAction,
    SlotContext,
)
from defi_sim.engine.submission_priors import SubmissionPathPriors
from defi_sim.engine.transactions import VersionedTransaction


def _executor(action, slot_index):
    return ExecutedAction(
        action=action, execution_cost=0, cost_token=None, succeeded=True
    )


def _exec_bundle(bundle: Bundle, slot: int) -> BundleExecutionResult:
    return BundleExecutionResult(
        reverted=False,
        failed_at_index=None,
        failed_reason=None,
        executed=[
            ExecutedAction(action=tx.actions[0], execution_cost=0, cost_token=None, succeeded=True)
            for tx in bundle.txs
            if tx.actions
        ],
    )


def _make_bundle(tip_lamports: int = 5_000) -> Bundle:
    return Bundle(
        txs=[VersionedTransaction(actions=[])],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=tip_lamports,
                recipient="tip-1",
            )
        ],
    )


def test_bundle_landing_rate_aligns_with_jito_relayer_prior() -> None:
    """Submit N bundles across N slots; observed land rate ≈ configured prior.

    Uses a 0.5 prior to make the assertion tight: with 2_000 trials the
    landed count should sit within ±2σ (≈ ±45) of 1_000. The test seeds the
    submission RNG so the assertion is reproducible.
    """
    n_slots = 2_000
    landing_prob = 0.5
    priors = SubmissionPathPriors(jito_relayer_landing_prob_baseline=landing_prob)
    rng = np.random.default_rng(seed=42)

    auction = BundleAuction()
    execution = SolanaLikeExecution(
        bundle_auction=auction,
        submission_priors=priors,
        submission_rng=rng,
    )

    landed = 0
    dropped_via_prior = 0
    for slot in range(n_slots):
        execution.submit_bundle(_make_bundle())
        ctx = SlotContext(
            slot=slot,
            pending_actions=[],
            ordering_context=OrderingContext(),
            executor=_executor,
            emit=lambda event: None,
            execute_bundle=_exec_bundle,
        )
        execution.execute_slot(ctx)
        landed += len(execution._last_slot_selected_bundles)
        dropped_via_prior += sum(
            1
            for _, reason in execution._last_slot_dropped_bundles
            if reason == DropReason.SUBMISSION_PATH_DROP
        )

    assert landed + dropped_via_prior == n_slots, (
        "every bundle should either land or drop via the prior — no other "
        "drop path is exercised in this no-conflict, low-CU configuration"
    )
    expected = landing_prob * n_slots
    sigma = math.sqrt(n_slots * landing_prob * (1.0 - landing_prob))
    assert abs(landed - expected) <= 2 * sigma, (
        f"landed={landed} expected≈{expected} ±{2 * sigma:.1f} (2σ)"
    )


def test_bundle_landing_rate_one_when_prior_is_one() -> None:
    """When the prior is 1.0 the Bernoulli sample is skipped entirely."""
    priors = SubmissionPathPriors(jito_relayer_landing_prob_baseline=1.0)
    auction = BundleAuction()
    execution = SolanaLikeExecution(
        bundle_auction=auction,
        submission_priors=priors,
        submission_rng=np.random.default_rng(seed=1),
    )

    n_slots = 100
    landed = 0
    for slot in range(n_slots):
        execution.submit_bundle(_make_bundle())
        ctx = SlotContext(
            slot=slot,
            pending_actions=[],
            ordering_context=OrderingContext(),
            executor=_executor,
            emit=lambda event: None,
            execute_bundle=_exec_bundle,
        )
        execution.execute_slot(ctx)
        landed += len(execution._last_slot_selected_bundles)

    assert landed == n_slots


def test_bundle_dropped_via_prior_uses_submission_path_drop_reason() -> None:
    """Bundles dropped by the prior carry the canonical ``submission_path_drop`` reason."""
    priors = SubmissionPathPriors(jito_relayer_landing_prob_baseline=0.0)
    auction = BundleAuction()
    execution = SolanaLikeExecution(
        bundle_auction=auction,
        submission_priors=priors,
        submission_rng=np.random.default_rng(seed=1),
    )

    execution.submit_bundle(_make_bundle())
    ctx = SlotContext(
        slot=0,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: None,
        execute_bundle=_exec_bundle,
    )
    execution.execute_slot(ctx)

    assert execution._last_slot_selected_bundles == []
    assert len(execution._last_slot_dropped_bundles) == 1
    _, reason = execution._last_slot_dropped_bundles[0]
    assert reason == DropReason.SUBMISSION_PATH_DROP
