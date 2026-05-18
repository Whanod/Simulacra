"""Bundle-auction tip-quote Beta-blend regression (FIX-020).

Asserts the documented blend rule:

    w_calibrated = max(0, k - n_observed) / k
    w_observed   = 1 - w_calibrated
    quote        = round(w_calibrated * curve.percentile(p, cohort)
                         + w_observed * observed_percentile)

Three test surfaces:
1. Curve only — no in-process observations.
2. Observations only — no calibrated curve (zero regression).
3. Curve + observations — Beta-weighted blend at multiple n_observed values.
"""

from __future__ import annotations

import pytest

from defi_sim.engine.bundle_auction import BundleAuction
from defi_sim_solana.calibration.tip_quote import (
    TipQuoteCurve,
    _PercentileTable,
)


COHORT = ("Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",)


def _curve_with_p50(p50_lamports: int, *, n_bundles: int = 1000) -> TipQuoteCurve:
    """Build a TipQuoteCurve whose lookup() returns ``p50_lamports`` at every percentile."""
    points = tuple((p, p50_lamports) for p in (25, 50, 75, 90, 95, 99))
    table = _PercentileTable(points=points, n_bundles=n_bundles)
    return TipQuoteCurve(
        captured_at="2026-05-05T00:00:00+00:00",
        n_bundles=n_bundles,
        n_slots=100,
        cohorts={},
        fallback=table,
    )


def test_curve_only_returns_calibrated_quote() -> None:
    """No observations → 100% calibrated weight."""
    curve = _curve_with_p50(25_000)
    auction = BundleAuction(tip_quote_curve=curve, min_bundle_tip_lamports=1_000)
    assert auction.tip_quote(COHORT, 50) == 25_000


def test_no_curve_no_obs_returns_floor() -> None:
    """Backward-compatible behavior: no curve, no observations → floor."""
    auction = BundleAuction(min_bundle_tip_lamports=1_000)
    assert auction.tip_quote(COHORT, 50) == 1_000


def test_no_curve_uses_observed_percentile() -> None:
    """Backward-compatible behavior: no curve, observations → empirical percentile."""
    auction = BundleAuction(min_bundle_tip_lamports=1_000)
    for v in [5_000, 10_000, 15_000, 20_000, 25_000]:
        auction.observe_tip(COHORT, v)
    # Nearest-rank p50 of 5 sorted values: idx = (50 * 4) // 100 = 2 → 15_000
    assert auction.tip_quote(COHORT, 50) == 15_000


def test_blend_decays_as_observations_accumulate() -> None:
    """Calibrated weight decays linearly to zero over k observations."""
    k = 200
    curve = _curve_with_p50(50_000)  # calibrated p50 = 50k
    auction = BundleAuction(
        tip_quote_curve=curve,
        tip_quote_calibration_k=k,
        min_bundle_tip_lamports=1_000,
    )

    # n=0: w_cal=1.0, quote = 50_000
    assert auction.tip_quote(COHORT, 50) == 50_000

    # Add 100 observations of 10_000. n_observed = 100, w_cal = 0.5, w_obs = 0.5.
    # observed p50 = 10_000 → blended = round(0.5 * 50_000 + 0.5 * 10_000) = 30_000.
    for _ in range(100):
        auction.observe_tip(COHORT, 10_000)
    assert auction.tip_quote(COHORT, 50) == 30_000

    # Add 100 more observations (total 200). n=k=200, w_cal=0, w_obs=1.0.
    # quote == observed p50 == 10_000.
    for _ in range(100):
        auction.observe_tip(COHORT, 10_000)
    assert auction.tip_quote(COHORT, 50) == 10_000


def test_blend_clamps_to_min_floor() -> None:
    """Even with calibrated + observed pointing below floor, result clamps up."""
    curve = _curve_with_p50(500)  # below floor
    auction = BundleAuction(
        tip_quote_curve=curve,
        min_bundle_tip_lamports=1_000,
    )
    auction.observe_tip(COHORT, 100)  # also below floor
    assert auction.tip_quote(COHORT, 50) >= 1_000


def test_calibration_k_must_be_positive() -> None:
    with pytest.raises(ValueError):
        BundleAuction(tip_quote_calibration_k=0)
    with pytest.raises(ValueError):
        BundleAuction(tip_quote_calibration_k=-1)


def test_is_calibrated_helper() -> None:
    auction = BundleAuction()
    assert auction.is_tip_quote_calibrated() is False
    curve = _curve_with_p50(10_000)
    auction_cal = BundleAuction(tip_quote_curve=curve)
    assert auction_cal.is_tip_quote_calibrated() is True


def test_cohort_specific_overrides_fallback() -> None:
    """Cohort-keyed lookups take precedence over the fallback table."""
    cohort_table = _PercentileTable(
        points=tuple((p, 99_999) for p in (25, 50, 75, 90, 95, 99)),
        n_bundles=10,
    )
    fallback_table = _PercentileTable(
        points=tuple((p, 1) for p in (25, 50, 75, 90, 95, 99)),
        n_bundles=1000,
    )
    cohort_key = ",".join(sorted(COHORT))
    curve = TipQuoteCurve(
        captured_at="2026-05-05T00:00:00+00:00",
        n_bundles=1010,
        n_slots=10,
        cohorts={cohort_key: cohort_table},
        fallback=fallback_table,
    )
    auction = BundleAuction(tip_quote_curve=curve, min_bundle_tip_lamports=1)
    assert auction.tip_quote(COHORT, 50) == 99_999
    # Out-of-cohort lookup uses fallback
    other_cohort = ("OtherAccount11111111111111111111111111111",)
    assert auction.tip_quote(other_cohort, 50) == 1
