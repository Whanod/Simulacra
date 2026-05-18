"""Held-out slot regression for the Jito tip-quote prior (FIX-020).

Acceptance criterion: fit on 90% of captured slots, predict
``tip-needed-at-p90`` for the held-out 10%, assert the engine prediction
is within 30% of the held-out population p90.

The comparison is intentionally population-vs-population (calibrated p90
on the fit set vs. empirical p90 of the held-out bundles in aggregate),
not population-vs-average-per-slot. A per-slot average can drift away
from the population percentile under heavy round-number clustering
(100_000-lamport tips are a known community floor) where Jensen's-
inequality-style averaging penalizes the calibrated estimate without
indicating real prediction error.

Skips when the captured corpus is below the minimum sample size — the
test runs only when a fresh capture has produced enough data to make the
assertion meaningful. The minimum is 1,000 slots / 10,000 bundles per
the FIX-020 spec.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from defi_sim_solana.calibration import fit_tip_quote_curve
from defi_sim_solana.calibration.tip_quote import iter_bundle_rows

pytestmark = pytest.mark.calibration


CORPUS_ROOT = Path("solana-plans/calibration/corpus/jito_bundles")
COHORT = (
    "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",
    "EUuUbDcafPrmVTD5M6qoJAoyyNbihBhugADAxRMn5he9",
    "2WLWEuKDgkDUccTpbwYp1GToYktiSB1cXvreHUwiSUVP",
)
MIN_SLOTS = 1_000
MIN_BUNDLES = 10_000
HOLDOUT_FRACTION = 0.10
TARGET_PERCENTILE = 90
TOLERANCE = 0.30


def _newest_capture_dir() -> Path | None:
    if not CORPUS_ROOT.exists():
        return None
    candidates = sorted(
        (p for p in CORPUS_ROOT.iterdir() if p.is_dir()),
        reverse=True,
    )
    for candidate in candidates:
        if (candidate / "bundles.jsonl.gz").exists():
            return candidate
    return None


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, (percentile * (len(s) - 1)) // 100))
    return s[idx]


def test_holdout_p90_within_tolerance() -> None:
    capture = _newest_capture_dir()
    if capture is None:
        pytest.skip("no Jito bundle capture committed")
    rows = list(iter_bundle_rows(capture))
    if len(rows) < MIN_BUNDLES:
        pytest.skip(
            f"captured corpus has {len(rows)} bundles, "
            f"need ≥{MIN_BUNDLES} for held-out regression"
        )

    slots_present = sorted({int(r.get("slot") or 0) for r in rows})
    if len(slots_present) < MIN_SLOTS:
        pytest.skip(
            f"captured corpus spans {len(slots_present)} slots, "
            f"need ≥{MIN_SLOTS} for held-out regression"
        )

    n_holdout = max(1, int(round(len(slots_present) * HOLDOUT_FRACTION)))
    # Deterministic split: take every Nth slot for the holdout so the fit
    # set isn't biased to a single contiguous chunk of mainnet activity.
    stride = len(slots_present) // n_holdout
    holdout_slots = {slots_present[i * stride] for i in range(n_holdout)}

    fit_rows = [r for r in rows if int(r.get("slot") or 0) not in holdout_slots]
    holdout_rows = [r for r in rows if int(r.get("slot") or 0) in holdout_slots]

    curve = fit_tip_quote_curve(capture, cohort=COHORT, rows=fit_rows)
    predicted = curve.percentile(TARGET_PERCENTILE, None)
    assert predicted > 0, "fit produced zero p90 — fallback distribution empty?"

    holdout_tips = [
        int(r.get("tip_lamports") or 0)
        for r in holdout_rows
        if int(r.get("tip_lamports") or 0) > 0
    ]
    assert holdout_tips, "no held-out bundle had a positive tip"
    actual = _percentile(holdout_tips, TARGET_PERCENTILE)
    rel_err = abs(predicted - actual) / max(actual, 1)
    assert rel_err <= TOLERANCE, (
        f"calibrated p{TARGET_PERCENTILE}={predicted:.0f} vs "
        f"holdout p{TARGET_PERCENTILE}={actual:.0f} "
        f"(relative error {rel_err:.1%}, tolerance {TOLERANCE:.0%})"
    )
