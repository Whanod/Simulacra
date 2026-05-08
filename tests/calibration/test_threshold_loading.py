"""Threshold-loader unit tests (PRD US-004 lines 814-816).

Pin three properties of the per-metric calibration thresholds YAML:

1. ``test_thresholds_yaml_parses`` — the committed YAML loads via the
   public loader without raising and yields one :class:`Threshold` per
   row, each with exactly one bound set.
2. ``test_threshold_metric_keys_match_run_snapshot_keys`` — every metric
   in the YAML is one of ``ReplayDiff._METRICS``, so the calibration
   lane never asserts against a metric the engine cannot emit.
3. ``test_threshold_breach_marker_flagged_in_results`` — a synthetic
   ``ErrorBand`` whose error exceeds its threshold surfaces as a
   :class:`ThresholdBreach`; an in-band band does not.
"""

from __future__ import annotations

import pytest

from defi_sim.calibration.thresholds import (
    Threshold,
    ThresholdBreach,
    expected_metric_keys,
    flag_breaches,
    load_thresholds,
)
from defi_sim.engine.replay_execution import ErrorBand


def test_thresholds_yaml_parses() -> None:
    thresholds = load_thresholds()
    assert thresholds, "thresholds.yaml produced no rows"
    for metric, threshold in thresholds.items():
        assert isinstance(threshold, Threshold)
        assert threshold.metric == metric
        rel_set = threshold.threshold_relative is not None
        abs_set = threshold.threshold_absolute is not None
        assert rel_set ^ abs_set, (
            f"{metric}: must set exactly one of threshold_relative / "
            "threshold_absolute"
        )


def test_threshold_metric_keys_match_run_snapshot_keys() -> None:
    thresholds = load_thresholds()
    engine_metrics = set(expected_metric_keys())
    yaml_metrics = set(thresholds)
    unknown = yaml_metrics - engine_metrics
    assert not unknown, (
        f"thresholds.yaml references metrics the engine does not emit: "
        f"{sorted(unknown)}"
    )


def test_threshold_breach_marker_flagged_in_results() -> None:
    thresholds = {
        "tips_paid": Threshold(metric="tips_paid", threshold_relative=0.10),
        "liquidations_triggered": Threshold(
            metric="liquidations_triggered", threshold_absolute=1.0
        ),
    }
    breaching = ErrorBand(
        metric="tips_paid",
        predicted=1100.0,
        actual=1000.0,
        abs_error=100.0,
        rel_error=0.1,
        supported=True,
    )
    in_band = ErrorBand(
        metric="liquidations_triggered",
        predicted=5.0,
        actual=5.0,
        abs_error=0.0,
        rel_error=0.0,
        supported=True,
    )
    bands = {
        "tips_paid": breaching,
        "liquidations_triggered": in_band,
    }
    # tips_paid: 100/1000 = 0.10 == limit, not breaching; bump to breach.
    breaches = flag_breaches(bands, thresholds)
    assert breaches == []
    bands["tips_paid"] = ErrorBand(
        metric="tips_paid",
        predicted=1200.0,
        actual=1000.0,
        abs_error=200.0,
        rel_error=0.2,
        supported=True,
    )
    breaches = flag_breaches(bands, thresholds)
    assert len(breaches) == 1
    breach = breaches[0]
    assert isinstance(breach, ThresholdBreach)
    assert breach.metric == "tips_paid"
    assert breach.observed == pytest.approx(0.2)
    assert breach.threshold.threshold_relative == pytest.approx(0.10)


def test_threshold_breach_skips_unsupported_bands() -> None:
    thresholds = {
        "pool_price": Threshold(metric="pool_price", threshold_relative=0.005),
    }
    bands = {
        "pool_price:SOL/USDC": ErrorBand(
            metric="pool_price:SOL/USDC",
            predicted=2.41,
            actual=None,
            abs_error=None,
            rel_error=None,
            supported=False,
        ),
    }
    assert flag_breaches(bands, thresholds) == []
