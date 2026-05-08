"""Unit tests for ``tools.snapshotter.categories`` (FIX-019)."""

from __future__ import annotations

import pytest

from tools.snapshotter import DEFAULT_THRESHOLDS, StressCategory
from tools.snapshotter.categories import CapturePolicy


def test_stress_category_enum_includes_phase_2_targets() -> None:
    """Phase 2 calibration targets ``steady_state`` (auto-snapshotter) plus
    ``high_volume_dex`` (lighthouse Whirlpool — captured manually by the
    calibration tool, not by the watch-loop runner).

    Other categories land alongside their Phase 3 protocol models —
    until then they are absent from the enum so unscoped fixtures cannot
    enter the corpus.
    """
    assert {c.value for c in StressCategory} == {"steady_state", "high_volume_dex"}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("steady_state", StressCategory.STEADY_STATE),
        ("STEADY-STATE", StressCategory.STEADY_STATE),
        ("  steady_state  ", StressCategory.STEADY_STATE),
    ],
)
def test_stress_category_parse_normalizes_input(raw: str, expected: StressCategory) -> None:
    assert StressCategory.parse(raw) is expected


def test_stress_category_parse_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="unknown stress category"):
        StressCategory.parse("nope")


def test_default_thresholds_have_bandwidth_for_real_mainnet_baseline() -> None:
    """Steady-state max_tx_count must accommodate the validator-vote floor.

    Vote txs alone consume ~700/slot on mainnet. A threshold below that
    would mean no real slot ever qualifies.
    """
    t = DEFAULT_THRESHOLDS
    assert t.steady_state_max_tx_count >= 1500


def test_default_capture_policy_is_block_only_until_per_pool_targeting_lands() -> None:
    """``getProgramAccounts`` returns ~30MB per program — block-only
    captures keep the corpus committable until per-pool targeting ships.
    """
    policy = CapturePolicy.default()
    for category in StressCategory:
        assert policy.programs.get(category) == (), (
            f"capture policy for {category.value} should be empty until "
            "per-pool getAccountInfo targeting lands."
        )
