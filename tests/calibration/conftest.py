"""Shared fixtures for the per-category calibration tests (US-004).

The corpus loader and category index are exposed through pytest fixtures so
each per-category test file stays a thin scaffold around the shared logic.

The scaffolding pattern is:

* For each stress category, the test scaffold asks ``_calibration_fixture_for``
  to find a committed real fixture.
* If no real fixture exists, the test ``pytest.skip``s with a message that
  tells the reader exactly which snapshotter capture is missing.
* When a real fixture exists, the test compares manifest-side ground truth
  against the engine's predicted metrics and asserts every metric is within
  its per-metric threshold (per ``solana-plans/calibration/thresholds.yaml``).

US-004's full DoD requires ``ReplayExecution`` to populate every metric in
``ReplayDiff._METRICS``. Until those decoders ship, the scaffolds assert
manifest-vs-block sanity checks (tx_count, blockhash, slot) so a regression
in the capture/load path still trips CI even before model-vs-mainnet tests
go live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from defi_sim_solana.replay.corpus import corpus_root, load_corpus_fixture
from tools.snapshotter import StressCategory, corpus_category_coverage


@pytest.fixture(scope="session")
def calibration_corpus_root() -> Path:
    return corpus_root()


@pytest.fixture(scope="session")
def coverage_index():
    return corpus_category_coverage()


def calibration_fixture_for(
    category: StressCategory,
) -> tuple[int, dict[str, Any]] | None:
    """Return ``(slot, manifest)`` for the most recent real fixture in ``category``.

    A "real" fixture is one whose ``manifest.yaml`` carries
    ``category: <stress_category>``. Synthetic fixtures (parser test data)
    return ``None`` so per-category tests skip cleanly.
    """
    coverage = corpus_category_coverage()
    slots = coverage.slots_for(category)
    if not slots:
        return None
    slot = slots[-1]
    manifest = _read_manifest(slot)
    return slot, manifest


def require_calibration_fixture(category: StressCategory) -> tuple[int, dict[str, Any]]:
    fixture = calibration_fixture_for(category)
    if fixture is None:
        pytest.skip(
            f"no real calibration fixture committed for {category.value!r}; "
            "snapshotter (FIX-019) has not yet captured a qualifying slot."
        )
    return fixture


def load_block(slot: int) -> dict[str, Any]:
    block = load_corpus_fixture(slot, kind="block")
    if block is None:
        pytest.skip(f"slot {slot} has a manifest but no block.json[.gz] fixture")
    return block


def _read_manifest(slot: int) -> dict[str, Any]:
    path = corpus_root() / str(slot) / "manifest.yaml"
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)
