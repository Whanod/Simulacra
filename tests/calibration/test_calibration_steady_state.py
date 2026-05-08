"""Calibration test for the boring steady-state baseline (US-004).

Per PRD US-004:

* ``test_baseline_metrics_within_tight_threshold`` — every relevant metric
  passes its threshold on a quiet slot. Tighter thresholds apply because
  there is nothing exciting happening; predicted-vs-actual error should
  collapse toward zero on the floor.

Real fixtures for this category are the easiest to capture (any low-MEV
slot qualifies). The test runs sanity checks that engage even before the
full ReplayExecution-driven comparison is wired:

1. The committed manifest's ``expected.tx_count`` must match the
   committed ``block.json[.gz]`` payload's transaction count. This fails
   immediately if a fixture/manifest pair drift apart.
2. The slot directory's ``category`` field must round-trip through the
   coverage scanner.

Once US-002 + per-protocol decoders ship, the per-metric threshold check
takes over.
"""

from __future__ import annotations

import pytest

from defi_sim.calibration.thresholds import load_thresholds
from tools.snapshotter import StressCategory, corpus_category_coverage

from .conftest import load_block, require_calibration_fixture

pytestmark = pytest.mark.calibration


def test_baseline_metrics_within_tight_threshold() -> None:
    slot, manifest = require_calibration_fixture(StressCategory.STEADY_STATE)
    block = load_block(slot)
    expected = manifest.get("expected") or {}

    # Round-trip sanity: the manifest's ground-truth tx_count matches the
    # captured block. This protects against a corrupted fixture or a
    # mis-stamped manifest landing in the corpus.
    if "tx_count" in expected:
        actual_tx_count = len(block.get("transactions") or [])
        assert expected["tx_count"] == actual_tx_count, (
            f"slot {slot}: manifest tx_count={expected['tx_count']} "
            f"diverges from committed block tx_count={actual_tx_count}; "
            "fixture and manifest are out of sync."
        )

    # Coverage scanner must classify this slot under steady_state.
    coverage = corpus_category_coverage()
    assert slot in coverage.slots_for(StressCategory.STEADY_STATE), (
        f"slot {slot} has manifest category=steady_state but the coverage "
        "scanner does not see it; check tools/snapshotter/coverage.py "
        "regex against this manifest's frontmatter."
    )

    # Threshold loader must be parseable so the per-metric assertion that
    # lands later has the bands to compare against.
    thresholds = load_thresholds()
    assert thresholds, "thresholds.yaml loaded empty; check the YAML file."

    pytest.skip(
        "ReplayExecution-driven baseline comparison not wired yet; "
        "engages when US-002 + per-protocol decoders ship. Manifest/block "
        "round-trip + coverage + threshold-loader assertions did pass."
    )
