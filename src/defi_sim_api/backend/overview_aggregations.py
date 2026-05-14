"""Phase 4 page-rewire aggregations for ``/runs/{id}/views/overview``.

The results page used to derive four summary surfaces by iterating
``result.round_snapshots`` on the client (Solana ticker, bundle-outcome
totals, Jito-searcher totals, replay-metrics). Phase 4 moves the iteration
server-side so the view bundle stays a fixed shape and the page paints from
a single fetch.

Inputs are the per-round summaries plucked by
:meth:`PostgresArtifactStore.query_overview_result_slices`
(``snapshot_summaries``). Outputs are the page-shaped objects consumed by
``frontend/src/app/(studio)/results/[runId]/page.tsx``.
"""

from __future__ import annotations

import math
from typing import Any, Iterable


def aggregate_solana_slot_summary(
    snapshots: Iterable[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Last snapshot's ``current_slot`` / ``current_leader``.

    Returns ``None`` when no snapshot carries Solana metadata — keeps the
    ticker hidden on non-Solana runs without the client having to special-case.
    """
    if not snapshots:
        return None
    last_slot: int | None = None
    last_leader: str | None = None
    for snap in snapshots:
        slot = snap.get("current_slot") if isinstance(snap, dict) else None
        leader = snap.get("current_leader") if isinstance(snap, dict) else None
        if isinstance(slot, int) and not isinstance(slot, bool):
            last_slot = slot
        if isinstance(leader, str):
            last_leader = leader
    if last_slot is None and last_leader is None:
        return None
    return {"current_slot": last_slot, "current_leader": last_leader}


def aggregate_bundle_outcomes_summary(
    snapshots: Iterable[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Per-round bundle-outcome counts, timeline, tips, and drop reasons.

    Mirrors the IIFE at ``results/[runId]/page.tsx:702-784``. We pre-aggregate
    here so the wire shape is the page-shaped summary, not a list of raw
    outcomes per round.
    """
    if not snapshots:
        return None
    landed = 0
    reverted = 0
    dropped = 0
    tips_paid = 0.0
    landed_by_round: list[int] = []
    reverted_by_round: list[int] = []
    dropped_by_round: list[int] = []
    per_round_landing_rates: list[float] = []
    drop_reasons: dict[str, int] = {}
    saw_outcomes = False
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        outcomes = snap.get("bundle_outcomes")
        if not isinstance(outcomes, list):
            outcomes = []
        rl = 0
        rr = 0
        rd = 0
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            saw_outcomes = True
            status = outcome.get("status")
            if status == "landed":
                rl += 1
                validator_rev = outcome.get("validator_revenue_lamports", 0) or 0
                stake_pool_rev = outcome.get("stake_pool_revenue_lamports", 0) or 0
                try:
                    validator_num = float(validator_rev)
                    if math.isfinite(validator_num):
                        tips_paid += validator_num
                except (TypeError, ValueError):
                    pass
                try:
                    stake_pool_num = float(stake_pool_rev)
                    if math.isfinite(stake_pool_num):
                        tips_paid += stake_pool_num
                except (TypeError, ValueError):
                    pass
            elif status == "reverted":
                rr += 1
            elif status == "dropped":
                rd += 1
                reason = outcome.get("drop_reason")
                key = reason if isinstance(reason, str) and reason else "unknown"
                drop_reasons[key] = drop_reasons.get(key, 0) + 1
        landed += rl
        reverted += rr
        dropped += rd
        landed_by_round.append(rl)
        reverted_by_round.append(rr)
        dropped_by_round.append(rd)
        total = rl + rr + rd
        if total > 0:
            per_round_landing_rates.append(rl / total)
    if not saw_outcomes:
        return None
    if per_round_landing_rates:
        avg = sum(per_round_landing_rates) / len(per_round_landing_rates)
        variance = sum((v - avg) ** 2 for v in per_round_landing_rates) / len(
            per_round_landing_rates
        )
        stdev = math.sqrt(variance)
    else:
        avg = 0.0
        stdev = 0.0
    return {
        "counts": {"landed": landed, "reverted": reverted, "dropped": dropped},
        "timeline": {
            "landed": landed_by_round,
            "reverted": reverted_by_round,
            "dropped": dropped_by_round,
        },
        "tips_paid_lamports": tips_paid,
        "drop_reasons": drop_reasons,
        "landing_rate_stats": {
            "avg": avg,
            "stdev": stdev,
            "rounds_with_bundles": len(per_round_landing_rates),
        },
    }


def aggregate_jito_searcher_summary(
    snapshots: Iterable[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Aggregate the *final* snapshot's ``metrics.jito_searcher`` block.

    Mirrors the IIFE at ``results/[runId]/page.tsx:805-863``. The final
    snapshot is canonical for landing-rate / tip-ROI; we sum across searcher
    instances and their ``by_strategy`` children so the wire matches what the
    page renders today.
    """
    snapshot_list = list(snapshots) if snapshots else []
    if not snapshot_list:
        return None
    last = snapshot_list[-1]
    if not isinstance(last, dict):
        return None
    payload = last.get("jito_searcher")
    if not isinstance(payload, dict):
        return None
    bundles_submitted = 0.0
    bundles_landed = 0.0
    tips_submitted = 0.0
    tips_paid = 0.0
    realized_ev = 0.0
    synthetic = False
    calibration: dict[str, Any] | None = None
    strategy_count = 0
    for searcher_payload in payload.values():
        if not isinstance(searcher_payload, dict):
            continue
        if searcher_payload.get("synthetic") is True:
            synthetic = True
        snap_calibration = searcher_payload.get("calibration")
        if isinstance(snap_calibration, dict) and calibration is None:
            calibration = snap_calibration
        by_strategy = searcher_payload.get("by_strategy")
        if not isinstance(by_strategy, dict):
            continue
        for counters in by_strategy.values():
            if not isinstance(counters, dict):
                continue
            bundles_submitted += _coerce_number(counters.get("bundles_submitted"))
            bundles_landed += _coerce_number(counters.get("bundles_landed"))
            tips_submitted += _coerce_number(counters.get("tips_submitted_lamports"))
            tips_paid += _coerce_number(counters.get("tips_paid_lamports"))
            realized_ev += _coerce_number(counters.get("realized_ev_lamports"))
            strategy_count += 1
    if strategy_count == 0:
        return None
    landing_rate = bundles_landed / bundles_submitted if bundles_submitted > 0 else 0.0
    tip_roi = realized_ev / tips_paid if tips_paid > 0 else 0.0
    return {
        "bundles_submitted": bundles_submitted,
        "bundles_landed": bundles_landed,
        "tips_submitted_lamports": tips_submitted,
        "tips_paid_lamports": tips_paid,
        "realized_ev_lamports": realized_ev,
        "landing_rate": landing_rate,
        "tip_roi": tip_roi,
        "synthetic": synthetic,
        "calibration": calibration,
    }


def latest_replay_metrics(
    snapshots: Iterable[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Last non-null ``metrics.replay`` payload across all snapshots.

    Mirrors the memo at ``results/[runId]/page.tsx:482-490``.
    """
    if not snapshots:
        return None
    last_replay: dict[str, Any] | None = None
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        replay = snap.get("replay")
        if isinstance(replay, dict):
            last_replay = replay
    return last_replay


def _coerce_number(value: Any) -> float:
    """Mirror the ``Number(x ?? 0) || 0`` idiom from the page."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(result):
        return 0.0
    return result
