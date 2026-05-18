"""Calibration-lane failure contract for threshold breaches."""

from __future__ import annotations

import pytest

from defi_sim.calibration.thresholds import (
    Threshold,
    assert_no_threshold_breaches,
)
from defi_sim.engine.replay_execution import ErrorBand


@pytest.mark.calibration
def test_threshold_breach_raises_pytest_failure_for_corpus_slot() -> None:
    thresholds = {
        "tips_paid": Threshold(metric="tips_paid", threshold_relative=0.10),
    }
    bands = {
        "tips_paid": ErrorBand(
            metric="tips_paid",
            predicted=1_250.0,
            actual=1_000.0,
            abs_error=250.0,
            rel_error=0.25,
            supported=True,
        ),
    }

    with pytest.raises(AssertionError, match="slot 420196842"):
        assert_no_threshold_breaches(bands, thresholds, slot=420_196_842)


@pytest.mark.calibration
def test_in_band_metrics_do_not_fail_calibration_lane() -> None:
    thresholds = {
        "liquidations_triggered": Threshold(
            metric="liquidations_triggered",
            threshold_absolute=1.0,
        ),
    }
    bands = {
        "liquidations_triggered": ErrorBand(
            metric="liquidations_triggered",
            predicted=6.0,
            actual=5.0,
            abs_error=1.0,
            rel_error=0.2,
            supported=True,
        ),
    }

    assert_no_threshold_breaches(bands, thresholds, slot=420_196_842)
