"""Calibration support utilities (PRD US-004).

Houses the per-metric threshold loader and breach-marker helpers that the
calibration CI lane uses to assert mainnet-vs-model error bounds. Kept
separate from :mod:`defi_sim.engine.replay_execution` so calibration
tooling can grow without dragging the core replay dataclasses with it.
"""

from defi_sim.calibration.thresholds import (
    Threshold,
    ThresholdBreach,
    assert_no_threshold_breaches,
    flag_breaches,
    load_thresholds,
)

__all__ = [
    "Threshold",
    "ThresholdBreach",
    "assert_no_threshold_breaches",
    "flag_breaches",
    "load_thresholds",
]
